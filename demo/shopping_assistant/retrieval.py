from __future__ import annotations

import logging
import os
import uuid
from functools import lru_cache
from typing import Any

from langchain.embeddings import init_embeddings
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlalchemy import select

from . import db

# Semantic retrieval over the demo's two corpora — the policy/FAQ knowledge base and
# the product catalog (the RAG layer behind faq_kb and product_db). Vectors live in
# Qdrant (one collection per corpus, cosine distance, HNSW-indexed by default; see
# VECTOR_DB_CHOICE.md for why Qdrant), started alongside Postgres/Redis by Docker
# Compose. The embedding model is provider-agnostic — EMBEDDING_MODEL takes an
# init_embeddings string like "openai:text-embedding-3-small" (the default) —
# because Anthropic offers no embeddings API, so the embedding lane is configured
# separately from the chat lane. Fail-silent throughout: no API key, Qdrant down,
# or an unindexed corpus all return None, and the callers in tools.py fall back to
# the keyword search in db.py.

logger = logging.getLogger(__name__)

_DEFAULT_EMBED_MODEL = "openai:text-embedding-3-small"

# Below this cosine score the best FAQ hit is noise, not an answer — fall back to
# the keyword path (which has its own honest "couldn't find that" reply).
_MIN_FAQ_SCORE = 0.25

_COLLECTIONS = {"faq": "loom_faq", "product": "loom_products"}


@lru_cache(maxsize=1)
def _embedder():
    return init_embeddings(os.environ.get("EMBEDDING_MODEL", _DEFAULT_EMBED_MODEL))


@lru_cache(maxsize=1)
def _client() -> QdrantClient:
    return QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"), timeout=10)


def _point_id(kind: str, ref_id: str) -> str:
    """Qdrant point ids must be UUIDs or integers; derive a stable UUID from our
    business ids so re-indexing a document overwrites its point instead of duplicating."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"loom/{kind}/{ref_id}"))


def _corpus(session) -> list[tuple[str, str, str]]:
    """Every (kind, ref_id, text) the two corpora should have embedded."""
    docs: list[tuple[str, str, str]] = []
    for f in session.scalars(select(db.Faq)):
        docs.append(("faq", str(f.id), f"{f.question}\n{f.answer}"))
    for p in session.scalars(select(db.Product)):
        deal = f" Deal: {p.deal}." if p.deal else ""
        docs.append(
            ("product", p.id, f"{p.name} — {p.category} from {p.shop}. {p.description}{deal}")
        )
    return docs


def _existing_content(client: QdrantClient, collection: str) -> dict[str, str]:
    """ref_id -> embedded text currently stored in a collection (empty if absent)."""
    if not client.collection_exists(collection):
        return {}
    existing: dict[str, str] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection, limit=256, offset=offset, with_payload=True, with_vectors=False
        )
        for point in points:
            existing[point.payload["ref_id"]] = point.payload.get("content", "")
        if offset is None:
            return existing


def ensure_embeddings() -> int:
    """Index both corpora in Qdrant: embed every new or changed document, batched.

    Idempotent — unchanged documents are skipped (the stored payload carries the
    exact embedded text, so staleness is detectable), and point ids are stable, so
    re-indexing overwrites in place. Returns how many documents were (re)embedded;
    raises on provider/Qdrant errors so the seeding script can report them honestly.
    Collections are created lazily with the actual embedding dimension — switching
    EMBEDDING_MODEL to a different dimension requires deleting the collections once.
    """
    with db.SessionLocal() as session:
        docs = _corpus(session)
    by_kind: dict[str, list[tuple[str, str]]] = {}
    for kind, ref_id, text in docs:
        by_kind.setdefault(kind, []).append((ref_id, text))

    client = _client()
    embedded = 0
    for kind, entries in by_kind.items():
        collection = _COLLECTIONS[kind]
        existing = _existing_content(client, collection)
        stale = [(ref_id, text) for ref_id, text in entries if existing.get(ref_id) != text]
        if not stale:
            continue
        vectors = _embedder().embed_documents([text for _, text in stale])
        if not client.collection_exists(collection):
            client.create_collection(
                collection,
                vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
            )
        client.upsert(
            collection,
            points=[
                PointStruct(
                    id=_point_id(kind, ref_id),
                    vector=vector,
                    payload={"ref_id": ref_id, "content": text},
                )
                for (ref_id, text), vector in zip(stale, vectors)
            ],
        )
        embedded += len(stale)
    return embedded


def _search(kind: str, query: str, limit: int) -> list[tuple[float, str]] | None:
    """Rank a corpus against the query by cosine similarity in Qdrant.

    None means 'no semantic answer available' (corpus not indexed, Qdrant or the
    embedding provider unreachable) and sends the caller to the keyword fallback.
    """
    try:
        client = _client()
        collection = _COLLECTIONS[kind]
        if not client.collection_exists(collection):
            return None
        query_vector = _embedder().embed_query(query)
        hits = client.query_points(collection, query=query_vector, limit=limit, with_payload=True)
        return [(point.score, point.payload["ref_id"]) for point in hits.points]
    except Exception:  # noqa: BLE001 - retrieval is an upgrade, never an outage
        logger.warning("semantic search failed for kind=%s", kind, exc_info=True)
        return None


def search_faq(query: str) -> dict[str, Any] | None:
    """The best policy/FAQ answers for the query, or None to use the keyword fallback."""
    hits = _search("faq", query, limit=3)
    if not hits or hits[0][0] < _MIN_FAQ_SCORE:
        return None
    with db.SessionLocal() as session:
        faqs = {str(f.id): f for f in session.scalars(select(db.Faq))}
    results = [
        {
            "question": faqs[ref_id].question,
            "answer": faqs[ref_id].answer,
            "source": faqs[ref_id].source,
            "score": round(score, 3),
        }
        for score, ref_id in hits
        if ref_id in faqs and score >= _MIN_FAQ_SCORE
    ]
    if not results:
        # Indexed points whose rows have since been deleted: every hit filtered out, so
        # results[0] would raise. Returning None hands the caller its keyword fallback,
        # which is what "retrieval is an upgrade, never an outage" has to mean here.
        return None
    best = results[0]
    return {
        "query": query,
        "answer": best["answer"],
        "source": best["source"],
        "related": results[1:],
    }


def search_products(query: str) -> dict[str, Any] | None:
    """Products ranked by meaning, or None to use the keyword fallback.

    Same payload shape as db.search_products, plus a match score per product, so
    the agent (and the trace) can see how confident each match is.
    """
    hits = _search("product", query, limit=8)
    if not hits:
        return None
    with db.SessionLocal() as session:
        products = {p.id: p for p in session.scalars(select(db.Product))}
    results = [
        {
            "id": p.id,
            "name": p.name,
            "price": p.price,
            "rating": p.rating,
            "in_stock": p.in_stock,
            "shop": p.shop,
            "match": round(score, 3),
        }
        for score, ref_id in hits
        if (p := products.get(ref_id)) is not None
    ]
    # An empty list is a miss, not a result: the tool composes these as `retrieval or db`,
    # so a truthy dict wrapping zero products would suppress the keyword fallback.
    return {"query": query, "results": results} if results else None

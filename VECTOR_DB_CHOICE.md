# Choosing a Vector Database — Why Qdrant

Loom's demo retrieves policy answers and products **by meaning** (see
`demo/shopping_assistant/retrieval.py`): the FAQ handbook and the catalog are embedded
once, each user query is embedded per call, and the nearest documents ground the agent's
answer. That requires somewhere to store vectors and run similarity search. This document
records why that somewhere is **Qdrant**, and what each realistic alternative is good at —
so the choice can be re-made deliberately if the constraints change.

## What this project actually needs

Any honest selection starts from the workload, not the leaderboard:

- **Tiny corpus** (~40 documents today; thousands at most for a real shop) — every engine
  is "fast enough"; raw benchmark speed is irrelevant here.
- **One developer, Docker Compose, Windows dev machine** — operational simplicity beats
  scalability features we will never use.
- **Cosine similarity + a payload** (the source row id and the embedded text) — no hybrid
  keyword+vector fusion, no multi-tenancy, no GPU indexes needed.
- **Fail-silent integration** — retrieval is an upgrade over keyword search, never an
  outage; the store must be easy to health-check and safe to be absent.
- **Thesis visibility** — the component should be a recognizable, production-credible
  piece of the modern RAG stack, not a toy.

## The candidates and what each is best at

| Engine | Deployment | Sweet spot | Why it wins / why not here |
|---|---|---|---|
| **Qdrant** ✅ | one small Docker container (Rust, single binary) | production RAG from thousands to ~50M vectors; filtered search | Simplest real engine to operate; HNSW by default; payload storage and filtering built in; first-class Python client; strong filtered-query latency. Nothing about it is oversized for us. |
| **Chroma** | embedded in-process, or a small server | fastest prototyping; notebooks and local experiments | Zero-infrastructure embedded mode is genuinely great for iteration — but it reads (and behaves) like a dev tool; the common industry path is "prototype on Chroma, deploy on Qdrant". We want the deployed-shape component. |
| **Milvus** | multi-service stack (etcd + object storage + node), or Milvus Lite | billions of vectors, GPU acceleration, heavy enterprise scale | The most scalable open-source option — and the most operational overhead. Milvus Lite (the embedded version) does not support Windows, and the full stack is absurd next to a 40-document corpus. |
| **Weaviate** | one Docker container | hybrid (keyword+vector) search out of the box; built-in embedding modules; multi-tenancy | Strong when you want the database to *own* hybrid search and vectorization. We compute embeddings ourselves (provider-agnostic lane) and don't need hybrid fusion, so its extra surface buys nothing here. |
| **pgvector** | extension inside the Postgres we already run | "no new service" shops; vectors next to relational data, transactional consistency | The best answer when adding a service is expensive — but it ties vector search to the relational schema and is an *extension*, not a dedicated engine; this project explicitly wanted the dedicated-engine architecture (and the separate service keeps the demo's storage story legible: Postgres = records, Redis = counters, Qdrant = vectors). |
| **FAISS** | a library, not a database | research; custom index experimentation; offline batch similarity | No server, no persistence story, no payloads — you build the database around it yourself. Wrong altitude for an application. |
| **Pinecone** | managed SaaS | zero-ops teams happy with a cloud dependency | Excellent operationally, but a paid external dependency contradicts the demo's fully-local, keys-optional design. |

## Why Qdrant, in one paragraph

Qdrant is the smallest thing that is still a *real* vector database: one pinned container
(`qdrant/qdrant:v1.18.2`) beside Postgres and Redis in `docker-compose.yml`, a clean Python
client (`qdrant-client`), cosine distance with an HNSW index by default, and payloads that
carry our back-references (`ref_id`) and staleness markers (the exact embedded text). It is
also the engine this architecture would keep if the catalog grew from 24 products to a
million — so the demo demonstrates the production shape, not a stand-in. Chroma lost on
production credibility, Milvus on operational weight (and Windows), Weaviate on unneeded
surface, pgvector on the explicit requirement for a dedicated engine, Pinecone on the
cloud dependency.

## How it's wired here (for the reader in a hurry)

- **Collections:** `loom_faq` (the 14-entry policy handbook) and `loom_products` (the
  catalog), created lazily with the embedding model's actual dimension, cosine distance.
- **Ids:** deterministic UUIDv5 derived from `kind + business id`, so re-indexing
  overwrites in place — no duplicates, ever.
- **Indexing:** `python -m demo.shopping_assistant.seed` diffs the stored payload text
  against the current corpus and embeds only what changed, in one batched call.
- **Query path:** `faq_kb` / `product_db` embed the query, `query_points` in Qdrant,
  score floor on FAQ answers (a weak match falls back rather than citing noise).
- **Failure:** Qdrant down, no API key, or an unindexed corpus → keyword search, silently.
  Retrieval is an upgrade, never an outage.
- **Config:** `QDRANT_URL` (default `http://localhost:6333`), `EMBEDDING_MODEL` (default
  `openai:text-embedding-3-small`; changing to a model with a different dimension requires
  deleting the two collections once so they are recreated at the new size).

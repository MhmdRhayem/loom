"""Retrieval facade behavior — the ranking backend is monkeypatched, so these run
without Qdrant, Postgres, or an embedding provider."""

from demo.shopping_assistant import retrieval


def test_faq_below_score_floor_falls_back(monkeypatch):
    # A weak best hit must yield None so the caller uses the keyword path,
    # not a low-similarity answer wearing a citation.
    monkeypatch.setattr(retrieval, "_search", lambda kind, query, limit: [(0.11, "1")])
    assert retrieval.search_faq("something unrelated to any policy") is None


def test_no_semantic_backend_falls_back(monkeypatch):
    monkeypatch.setattr(retrieval, "_search", lambda kind, query, limit: None)
    assert retrieval.search_faq("return policy") is None
    assert retrieval.search_products("red dress") is None


def test_point_ids_are_stable_and_distinct():
    a1 = retrieval._point_id("faq", "1")
    a2 = retrieval._point_id("faq", "1")
    b = retrieval._point_id("product", "1")
    assert a1 == a2  # re-indexing overwrites in place
    assert a1 != b  # corpora never collide

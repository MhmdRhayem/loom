"""Layer 2 (auto-memory) behavior. The extraction model and the repository are both
fakes, so these run with no API calls and no Postgres."""

from types import SimpleNamespace

from backend.memory import auto_memory
from backend.memory.auto_memory import _Extraction, _Memory, extract_and_upsert, load_hints


class FakeMemoryRepo:
    """In-memory stand-in for MemoryRepository; `fail_on` forces a write to blow up."""

    def __init__(self, rows=(), load_raises=False, fail_on=()):
        self.rows = list(rows)
        self.load_raises = load_raises
        self.fail_on = set(fail_on)
        self.upserts = []

    async def load_auto_memory(self, owner_id, limit=200):
        if self.load_raises:
            raise RuntimeError("db down")
        return self.rows

    async def upsert_auto_memory(self, owner_id, topic, content, confidence=0.5):
        if topic in self.fail_on:
            raise RuntimeError("write failed")
        self.upserts.append((owner_id, topic, content, confidence))


def fake_settings():
    return SimpleNamespace(
        default_provider="openai", model_id_for_tier=lambda tier: f"model-{tier}"
    )


def _usage_message(input_tokens=120, output_tokens=30):
    """A stand-in model reply carrying the usage_metadata the accounting reads."""
    return SimpleNamespace(
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": 0},
        }
    )


def patch_model(monkeypatch, extraction=None, raises=False):
    """Replace init_chat_model so no provider is ever contacted."""

    class FakeModel:
        def with_structured_output(self, schema, include_raw=False):
            # include_raw is what the production call passes, so that the underlying
            # message (and its usage_metadata) survives for token accounting.
            assert include_raw, "production code must ask for the raw message"
            return self

        async def ainvoke(self, prompt):
            if raises:
                raise RuntimeError("provider error")
            return {"raw": _usage_message(), "parsed": extraction, "parsing_error": None}

    monkeypatch.setattr(auto_memory, "init_chat_model", lambda *a, **k: FakeModel())


# --- load_hints -------------------------------------------------------------


async def test_load_hints_formats_topic_and_content():
    repo = FakeMemoryRepo(
        rows=[
            SimpleNamespace(topic="preferred_size", content="Wears size M."),
            SimpleNamespace(topic="name", content="The user is Mohammad."),
        ]
    )
    assert await load_hints(repo, "owner-1") == [
        "preferred_size: Wears size M.",
        "name: The user is Mohammad.",
    ]


async def test_load_hints_is_fail_silent():
    # A storage outage must degrade to "no hints", never break the turn.
    assert await load_hints(FakeMemoryRepo(load_raises=True), "owner-1") == []


# --- extract_and_upsert -----------------------------------------------------


async def test_extraction_writes_each_memory(monkeypatch):
    patch_model(
        monkeypatch,
        _Extraction(
            memories=[
                _Memory(topic="preferred_size", content="Wears size M.", confidence=0.9),
                _Memory(topic="city", content="Lives in Beirut.", confidence=0.7),
            ]
        ),
    )
    repo = FakeMemoryRepo()
    written = await extract_and_upsert(repo, fake_settings(), "owner-1", "I wear M", "Noted.")
    assert written == 2
    assert repo.upserts[0] == ("owner-1", "preferred_size", "Wears size M.", 0.9)


async def test_topics_are_normalized_and_blanks_skipped(monkeypatch):
    # Topic keys are the upsert conflict key, so "  Preferred_Size " and
    # "preferred_size" must not become two separate rows.
    patch_model(
        monkeypatch,
        _Extraction(
            memories=[
                _Memory(topic="  Preferred_Size ", content=" Wears size M. ", confidence=0.8),
                _Memory(topic="   ", content="no topic", confidence=0.5),
                _Memory(topic="city", content="   ", confidence=0.5),
            ]
        ),
    )
    repo = FakeMemoryRepo()
    written = await extract_and_upsert(repo, fake_settings(), "owner-1", "hi", "hello")
    assert written == 1
    assert repo.upserts == [("owner-1", "preferred_size", "Wears size M.", 0.8)]


async def test_nothing_durable_writes_nothing(monkeypatch):
    patch_model(monkeypatch, _Extraction(memories=[]))
    repo = FakeMemoryRepo()
    assert await extract_and_upsert(repo, fake_settings(), "owner-1", "hi", "hello") == 0
    assert repo.upserts == []


async def test_extraction_failure_is_fail_silent(monkeypatch):
    patch_model(monkeypatch, raises=True)
    assert await extract_and_upsert(FakeMemoryRepo(), fake_settings(), "o", "hi", "yo") == 0


async def test_one_failed_write_does_not_lose_the_others(monkeypatch):
    patch_model(
        monkeypatch,
        _Extraction(
            memories=[
                _Memory(topic="bad", content="explodes", confidence=0.5),
                _Memory(topic="good", content="survives", confidence=0.5),
            ]
        ),
    )
    repo = FakeMemoryRepo(fail_on={"bad"})
    written = await extract_and_upsert(repo, fake_settings(), "owner-1", "hi", "hello")
    assert written == 1
    assert [u[1] for u in repo.upserts] == ["good"]

"""Layer 4 (dreaming) behavior: the size/interval gate, and the merge/prune pass.

The planning model is a fake, so no provider is contacted. The focus is the guard
against overlapping plans: the model can name the same topic in two merges, or in
both a merge and the prune list, and neither may double-count or resurrect a row.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.memory import consolidation
from backend.memory.consolidation import DreamPlan, Merge, consolidate, should_dream


class FakeMemoryRepo:
    def __init__(self, rows=(), count=0, raises=False):
        self.rows = list(rows)
        self.count = count
        self.raises = raises
        self.updated = []
        self.deleted = []

    async def count_auto_memory(self, owner_id, include_expired=False):
        if self.raises:
            raise RuntimeError("db down")
        return self.count

    async def load_auto_memory(self, owner_id, limit=200):
        return self.rows

    async def update_auto_memory(self, memory_id, **kwargs):
        self.updated.append((memory_id, kwargs.get("content")))

    async def delete_auto_memory(self, memory_id):
        self.deleted.append(memory_id)


class FakeDreamRepo:
    def __init__(self, last=None):
        self.last = last
        self.runs = []

    async def get_last_dream_run(self, owner_id):
        return self.last

    async def log_dream_run(self, owner_id, memories_merged, memories_pruned, duration_ms, **kw):
        self.runs.append({"merged": memories_merged, "pruned": memories_pruned})


def fake_settings(min_memories=5, interval_hours=24):
    return SimpleNamespace(
        default_provider="openai",
        model_id_for_tier=lambda tier: f"model-{tier}",
        dream_min_memories=min_memories,
        dream_interval_hours=interval_hours,
    )


def row(topic, content="x"):
    return SimpleNamespace(id=topic, topic=topic, content=content)


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


def patch_planner(monkeypatch, plan=None, raises=False):
    class FakeModel:
        def with_structured_output(self, schema, include_raw=False):
            # include_raw is what the production call passes, so that the underlying
            # message (and its usage_metadata) survives for token accounting.
            assert include_raw, "production code must ask for the raw message"
            return self

        async def ainvoke(self, prompt):
            if raises:
                raise RuntimeError("provider error")
            return {"raw": _usage_message(), "parsed": plan, "parsing_error": None}

    monkeypatch.setattr(consolidation, "init_chat_model", lambda *a, **k: FakeModel())


def hours_ago(n):
    return SimpleNamespace(started_at=datetime.now(timezone.utc) - timedelta(hours=n))


# --- should_dream -----------------------------------------------------------


async def test_too_few_memories_does_not_dream():
    repo = FakeMemoryRepo(count=4)
    assert await should_dream(repo, FakeDreamRepo(), "o", fake_settings(min_memories=5)) is False


async def test_first_ever_run_dreams():
    repo = FakeMemoryRepo(count=10)
    assert await should_dream(repo, FakeDreamRepo(last=None), "o", fake_settings()) is True


async def test_recent_run_blocks_dreaming():
    repo = FakeMemoryRepo(count=10)
    dreams = FakeDreamRepo(last=hours_ago(2))
    assert await should_dream(repo, dreams, "o", fake_settings(interval_hours=24)) is False


async def test_dreams_again_once_the_interval_has_passed():
    repo = FakeMemoryRepo(count=10)
    dreams = FakeDreamRepo(last=hours_ago(30))
    assert await should_dream(repo, dreams, "o", fake_settings(interval_hours=24)) is True


async def test_gate_fails_closed_on_error():
    repo = FakeMemoryRepo(count=10, raises=True)
    assert await should_dream(repo, FakeDreamRepo(), "o", fake_settings()) is False


# --- consolidate ------------------------------------------------------------


async def test_merge_rewrites_keeper_and_deletes_drops(monkeypatch):
    repo = FakeMemoryRepo(rows=[row("size"), row("shirt_size"), row("city")])
    patch_planner(
        monkeypatch,
        DreamPlan(merges=[Merge(topic="size", content="Wears M.", drop_topics=["shirt_size"])]),
    )
    result = await consolidate(repo, FakeDreamRepo(), "o", fake_settings())
    assert repo.updated == [("size", "Wears M.")]
    assert repo.deleted == ["shirt_size"]
    assert (result["merged"], result["pruned"]) == (1, 0)


async def test_prune_deletes_stale_topics(monkeypatch):
    repo = FakeMemoryRepo(rows=[row("size"), row("asked_about_weather")])
    patch_planner(monkeypatch, DreamPlan(prune_topics=["asked_about_weather"]))
    result = await consolidate(repo, FakeDreamRepo(), "o", fake_settings())
    assert repo.deleted == ["asked_about_weather"]
    assert (result["merged"], result["pruned"]) == (0, 1)


async def test_row_dropped_twice_is_deleted_and_counted_once(monkeypatch):
    # The model named "shirt_size" as redundant in two separate merges.
    repo = FakeMemoryRepo(rows=[row("size"), row("shirt_size"), row("fit")])
    patch_planner(
        monkeypatch,
        DreamPlan(
            merges=[
                Merge(topic="size", content="Wears M.", drop_topics=["shirt_size"]),
                Merge(topic="fit", content="Prefers slim.", drop_topics=["shirt_size"]),
            ]
        ),
    )
    result = await consolidate(repo, FakeDreamRepo(), "o", fake_settings())
    assert repo.deleted == ["shirt_size"]
    assert result["merged"] == 1


async def test_topic_in_both_a_merge_and_the_prune_list_is_not_double_counted(monkeypatch):
    repo = FakeMemoryRepo(rows=[row("size"), row("shirt_size")])
    patch_planner(
        monkeypatch,
        DreamPlan(
            merges=[Merge(topic="size", content="Wears M.", drop_topics=["shirt_size"])],
            prune_topics=["shirt_size"],
        ),
    )
    result = await consolidate(repo, FakeDreamRepo(), "o", fake_settings())
    assert repo.deleted == ["shirt_size"]
    assert (result["merged"], result["pruned"]) == (1, 0)


async def test_merge_into_an_already_deleted_row_is_skipped(monkeypatch):
    # "shirt_size" is dropped by the first merge, so the second merge would be
    # writing into a deleted row — it must be skipped, leaving "fit" intact.
    repo = FakeMemoryRepo(rows=[row("size"), row("shirt_size"), row("fit")])
    patch_planner(
        monkeypatch,
        DreamPlan(
            merges=[
                Merge(topic="size", content="Wears M.", drop_topics=["shirt_size"]),
                Merge(topic="shirt_size", content="orphan", drop_topics=["fit"]),
            ]
        ),
    )
    result = await consolidate(repo, FakeDreamRepo(), "o", fake_settings())
    assert repo.deleted == ["shirt_size"]
    assert repo.updated == [("size", "Wears M.")]
    assert result["merged"] == 1


async def test_a_merge_never_deletes_its_own_keeper(monkeypatch):
    repo = FakeMemoryRepo(rows=[row("size")])
    patch_planner(
        monkeypatch,
        DreamPlan(merges=[Merge(topic="size", content="Wears M.", drop_topics=["size"])]),
    )
    result = await consolidate(repo, FakeDreamRepo(), "o", fake_settings())
    assert repo.deleted == []
    assert result["merged"] == 0


async def test_unknown_topics_in_the_plan_are_ignored(monkeypatch):
    repo = FakeMemoryRepo(rows=[row("size")])
    patch_planner(
        monkeypatch,
        DreamPlan(
            merges=[Merge(topic="hallucinated", content="?", drop_topics=["also_fake"])],
            prune_topics=["not_a_topic"],
        ),
    )
    result = await consolidate(repo, FakeDreamRepo(), "o", fake_settings())
    assert repo.deleted == [] and repo.updated == []
    assert (result["merged"], result["pruned"]) == (0, 0)


async def test_planner_failure_leaves_memory_untouched_but_still_logs(monkeypatch):
    repo = FakeMemoryRepo(rows=[row("size"), row("city")])
    dreams = FakeDreamRepo()
    patch_planner(monkeypatch, raises=True)
    result = await consolidate(repo, dreams, "o", fake_settings())
    assert repo.deleted == [] and repo.updated == []
    assert (result["merged"], result["pruned"]) == (0, 0)
    assert dreams.runs == [{"merged": 0, "pruned": 0}]  # the run is still recorded


async def test_run_is_logged_with_the_real_counts(monkeypatch):
    repo = FakeMemoryRepo(rows=[row("size"), row("shirt_size"), row("weather")])
    dreams = FakeDreamRepo()
    patch_planner(
        monkeypatch,
        DreamPlan(
            merges=[Merge(topic="size", content="Wears M.", drop_topics=["shirt_size"])],
            prune_topics=["weather"],
        ),
    )
    await consolidate(repo, dreams, "o", fake_settings())
    assert dreams.runs == [{"merged": 1, "pruned": 1}]

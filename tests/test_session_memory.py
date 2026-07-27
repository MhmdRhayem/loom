"""Layer 3 (session memory) behavior: the replay window, and the rolling summary that
absorbs turns as they age out. The summarizer model and the store are fakes, so these
run with no API calls and no Postgres."""

from types import SimpleNamespace

from backend import service
from backend.service import _MAX_HISTORY_TURNS, _load_history

CID = "11111111-1111-1111-1111-111111111111"


class FakeConversations:
    def __init__(self, turns=(), conversation=None, turns_raise=False):
        self.turns = list(turns)
        self.conversation = conversation
        self.turns_raise = turns_raise
        self.saved = []

    async def get_turns(self, cid):
        if self.turns_raise:
            raise RuntimeError("db down")
        return self.turns

    async def get_conversation(self, cid):
        return self.conversation

    async def set_summary(self, cid, summary, summarized_turns):
        self.saved.append((summary, summarized_turns))


def fake_db(**kwargs):
    return SimpleNamespace(conversations=FakeConversations(**kwargs))


def fake_settings():
    return SimpleNamespace(
        default_provider="openai", model_id_for_tier=lambda tier: f"model-{tier}"
    )


def turns(n, start=1):
    return [
        SimpleNamespace(user_message=f"q{i}", agent_response=f"a{i}")
        for i in range(start, start + n)
    ]


def patch_summarizer(monkeypatch, text="earlier: the user shopped for shirts", raises=False):
    calls = []

    class FakeModel:
        async def ainvoke(self, prompt):
            if raises:
                raise RuntimeError("provider error")
            calls.append(prompt)
            return SimpleNamespace(content=text)

    monkeypatch.setattr(service, "init_chat_model", lambda *a, **k: FakeModel())
    return calls


# --- the replay window ------------------------------------------------------


async def test_no_db_or_no_conversation_gives_empty_history():
    assert await _load_history(None, fake_settings(), CID) == []
    assert await _load_history(fake_db(), fake_settings(), None) == []


async def test_short_conversation_replays_verbatim(monkeypatch):
    calls = patch_summarizer(monkeypatch)
    db = fake_db(turns=turns(3))
    history = await _load_history(db, fake_settings(), CID)
    assert history == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
    ]
    assert calls == []  # nothing aged out, so no summarization spend


async def test_turn_without_a_response_contributes_only_the_user_message():
    db = fake_db(turns=[SimpleNamespace(user_message="q1", agent_response=None)])
    assert await _load_history(db, fake_settings(), CID) == [{"role": "user", "content": "q1"}]


async def test_storage_error_is_fail_silent():
    db = fake_db(turns_raise=True)
    assert await _load_history(db, fake_settings(), CID) == []


async def test_malformed_conversation_id_is_fail_silent():
    assert await _load_history(fake_db(turns=turns(2)), fake_settings(), "not-a-uuid") == []


# --- compaction -------------------------------------------------------------


async def test_overflow_summarizes_the_aged_out_turns(monkeypatch):
    patch_summarizer(monkeypatch, text="the user asked about shirts")
    db = fake_db(turns=turns(13), conversation=SimpleNamespace(summary=None, summarized_turns=0))
    history = await _load_history(db, fake_settings(), CID)

    # One summary block, then exactly the last 10 turns replayed verbatim.
    assert history[0]["role"] == "user"
    assert "the user asked about shirts" in history[0]["content"]
    assert "turns 1-3" in history[0]["content"]
    assert len(history) == 1 + 2 * _MAX_HISTORY_TURNS
    assert history[1] == {"role": "user", "content": "q4"}
    assert history[-1] == {"role": "assistant", "content": "a13"}


async def test_summary_is_persisted_with_its_turn_count(monkeypatch):
    patch_summarizer(monkeypatch, text="a summary")
    db = fake_db(turns=turns(12), conversation=SimpleNamespace(summary=None, summarized_turns=0))
    await _load_history(db, fake_settings(), CID)
    assert db.conversations.saved == [("a summary", 2)]


async def test_up_to_date_summary_is_reused_without_calling_the_model(monkeypatch):
    # The stored summary already covers all 2 aged-out turns, so re-summarizing
    # would be pure spend for an identical result.
    calls = patch_summarizer(monkeypatch)
    db = fake_db(
        turns=turns(12), conversation=SimpleNamespace(summary="already known", summarized_turns=2)
    )
    history = await _load_history(db, fake_settings(), CID)
    assert "already known" in history[0]["content"]
    assert calls == []
    assert db.conversations.saved == []


async def test_stale_summary_folds_in_only_the_newly_aged_out_turns(monkeypatch):
    # 5 turns have aged out but the stored summary covers 2, so turns 3-5 are new.
    calls = patch_summarizer(monkeypatch, text="updated summary")
    db = fake_db(
        turns=turns(15), conversation=SimpleNamespace(summary="covers q1-q2", summarized_turns=2)
    )
    await _load_history(db, fake_settings(), CID)
    sent = str(calls[0])
    assert "covers q1-q2" in sent  # the existing summary is carried forward
    assert "User: q3" in sent and "User: q5" in sent
    assert "User: q1" not in sent  # already-summarized turns are not re-sent
    assert "User: q6" not in sent  # turns still in the replay window are not summarized
    assert db.conversations.saved == [("updated summary", 5)]


async def test_summarizer_failure_still_returns_the_recent_turns(monkeypatch):
    patch_summarizer(monkeypatch, raises=True)
    db = fake_db(turns=turns(12), conversation=SimpleNamespace(summary=None, summarized_turns=0))
    history = await _load_history(db, fake_settings(), CID)
    assert len(history) == 2 * _MAX_HISTORY_TURNS  # no summary block, nothing crashed
    assert history[0] == {"role": "user", "content": "q3"}


async def test_missing_conversation_row_skips_the_summary_block(monkeypatch):
    patch_summarizer(monkeypatch)
    db = fake_db(turns=turns(12), conversation=None)
    history = await _load_history(db, fake_settings(), CID)
    assert len(history) == 2 * _MAX_HISTORY_TURNS

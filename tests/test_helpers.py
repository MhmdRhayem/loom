"""Small pure helpers: config env parsing, title truncation, prompt assembly."""

from backend.core.config import _csv, _flag
from backend.core.prompt_builder import build_messages, build_system_prompt, message_text
from backend.storage.repositories.analytics import _truncate


def test_flag_parsing(monkeypatch):
    monkeypatch.setenv("X_FLAG", "false")
    assert _flag("X_FLAG") is False
    monkeypatch.setenv("X_FLAG", "TRUE")
    assert _flag("X_FLAG") is True
    monkeypatch.delenv("X_FLAG", raising=False)
    assert _flag("X_FLAG", default=False) is False


def test_csv_parsing(monkeypatch):
    monkeypatch.setenv("X_CSV", "a, b ,c")
    assert _csv("X_CSV", ("z",)) == ("a", "b", "c")
    monkeypatch.setenv("X_CSV", "  ")
    assert _csv("X_CSV", ("z",)) == ("z",)
    monkeypatch.delenv("X_CSV", raising=False)
    assert _csv("X_CSV", ("z",)) == ("z",)


def test_truncate_collapses_whitespace_and_caps_length():
    assert _truncate(None) is None
    assert _truncate("") is None
    assert _truncate("short  message\nhere") == "short message here"
    long = "word " * 40
    out = _truncate(long, limit=30)
    assert out is not None and len(out) <= 30 and out.endswith("…")


def test_message_text_handles_content_blocks():
    assert message_text("plain") == "plain"

    class Msg:
        content = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]

    assert message_text(Msg()) == "ab"


def test_build_messages_inserts_hints_before_latest_message():
    # The hints block goes right before the newest user message — never at the
    # front, where its turn-to-turn churn would break prompt prefix caching.
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    latest = {"role": "user", "content": "what size?"}
    out = build_messages([*history, latest], ["size: M"])
    assert out[:2] == history
    assert "size: M" in out[2]["content"]
    assert out[3] == latest


def test_build_messages_without_context_is_passthrough():
    base = [{"role": "user", "content": "hi"}]
    assert build_messages(base, []) == base


def test_system_prompt_carries_the_definition(monkeypatch):
    # Layer 1: the YAML definition becomes the cache-stable prompt prefix.
    prompt = build_system_prompt(
        {
            "name": "catalog_advisor",
            "description": "Finds products and compares prices.",
            "capabilities": ["search the catalog", "compare prices"],
        }
    )
    assert "You are the catalog_advisor agent." in prompt
    assert "Finds products and compares prices." in prompt
    assert "- search the catalog" in prompt
    assert "Operating rules:" in prompt  # always present, definition or not


def test_system_prompt_tolerates_a_bare_definition():
    prompt = build_system_prompt({})
    assert prompt.startswith("You are the agent agent.")
    assert "Capabilities:" not in prompt

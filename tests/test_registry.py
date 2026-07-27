"""Agent-definition loading and validation, plus a smoke test over the real roster.
The boot-time ladder is the only thing standing between a typo in a YAML file and a
malformed agent reaching production, and the roster test pins the shipped counts."""

from pathlib import Path

import pytest

from backend.agents.registry import AgentRegistry, RegistryError

DEFINITIONS = Path(__file__).resolve().parents[1] / "demo" / "shopping_assistant" / "definitions"

VALID = """
name: {name}
description: Handles {name} requests.
capabilities:
  - something
tools:
  - some_tool
model: fast
fallback_agent: {fallback}
"""


def write(tmp_path, name, fallback="human_handoff", body=None, filename=None):
    text = body if body is not None else VALID.format(name=name, fallback=fallback)
    path = tmp_path / (filename or f"{name}.yaml")
    path.write_text(text, encoding="utf-8")
    return path


def load(tmp_path):
    return AgentRegistry.from_directory(tmp_path)


# --- the shipped roster -----------------------------------------------------


def test_the_real_definitions_directory_loads():
    # Four lines that would have caught every count drift this roster has had.
    registry = AgentRegistry.from_directory(DEFINITIONS)
    assert len(registry) == 8
    assert "support_concierge" in registry
    assert "shop_manager" in registry


def test_every_shipped_fallback_resolves():
    registry = AgentRegistry.from_directory(DEFINITIONS)
    known = set(registry.names()) | {"human_handoff"}
    assert all(defn.fallback_agent in known for defn in registry.all())


def test_every_shipped_tool_is_resolvable():
    # A tool name no provider can resolve is silently dropped at bind time, so the
    # agent runs without the capability it advertises.
    from demo.shopping_assistant.tools import _MERCHANT_TOOL_NAMES, TOOLS, _user_tools

    known = set(TOOLS) | set(_user_tools(None)) | _MERCHANT_TOOL_NAMES
    for defn in AgentRegistry.from_directory(DEFINITIONS).all():
        assert set(defn.tools) <= known, f"{defn.name} declares unknown tool(s)"


# --- the validation ladder --------------------------------------------------


def test_missing_directory_is_rejected(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        load(tmp_path / "nope")


def test_empty_directory_is_rejected(tmp_path):
    with pytest.raises(RegistryError, match="no agent definition"):
        load(tmp_path)


def test_name_must_match_the_filename_stem(tmp_path):
    write(tmp_path, "alpha", filename="beta.yaml")
    with pytest.raises(RegistryError, match="must match the filename stem"):
        load(tmp_path)


def test_missing_required_field_is_rejected(tmp_path):
    write(tmp_path, "alpha", body="name: alpha\ndescription: x\n")
    with pytest.raises(RegistryError, match="missing required field"):
        load(tmp_path)


def test_scalar_tools_is_rejected_not_split_into_characters(tmp_path):
    # Without this guard, `tools: some_tool` becomes ('s', 'o', 'm', ...).
    body = VALID.format(name="alpha", fallback="human_handoff").replace(
        "tools:\n  - some_tool", "tools: some_tool"
    )
    write(tmp_path, "alpha", body=body)
    with pytest.raises(RegistryError, match="'tools' must be a list"):
        load(tmp_path)


def test_unknown_model_tier_is_rejected(tmp_path):
    write(
        tmp_path,
        "alpha",
        body=VALID.format(name="alpha", fallback="human_handoff").replace(
            "model: fast", "model: claude-opus-4-8"
        ),
    )
    with pytest.raises(RegistryError, match="not one of"):
        load(tmp_path)


def test_dangling_fallback_is_rejected(tmp_path):
    write(tmp_path, "alpha", fallback="ghost")
    with pytest.raises(RegistryError, match="neither a known agent"):
        load(tmp_path)


def test_fallback_to_another_agent_resolves(tmp_path):
    write(tmp_path, "alpha", fallback="beta")
    write(tmp_path, "beta")
    assert load(tmp_path).names() == ["alpha", "beta"]


def test_out_of_range_judge_sample_rate_is_rejected(tmp_path):
    body = VALID.format(name="alpha", fallback="human_handoff") + "judge_sample_rate: 1.5\n"
    write(tmp_path, "alpha", body=body)
    with pytest.raises(RegistryError, match="judge_sample_rate"):
        load(tmp_path)


def test_top_level_scalar_yaml_is_rejected(tmp_path):
    write(tmp_path, "alpha", body="just a string\n")
    with pytest.raises(RegistryError, match="must be a mapping"):
        load(tmp_path)


def test_router_menu_exposes_only_routing_signal(tmp_path):
    write(tmp_path, "alpha")
    menu = load(tmp_path).router_menu()
    assert menu == [
        {"name": "alpha", "description": "Handles alpha requests.", "capabilities": ["something"]}
    ]

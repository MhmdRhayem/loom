"""Runtime agent-visibility enforcement and the peer-call surface. ARCHITECTURE.md
promises visibility holds at every surface, not just the router menu, and that
delegation is bounded by construction; both are checked here with no LLM call."""

from types import SimpleNamespace

from backend.agents import factory
from backend.agents.factory import _ask_peer_tools, run_agent


class FakeRegistry:
    def __init__(self, names):
        self._names = list(names)

    def __contains__(self, name):
        return name in self._names

    def names(self):
        return sorted(self._names)

    def get(self, name):
        return SimpleNamespace(
            name=name,
            description=f"{name} does {name} things.",
            capabilities=[f"{name}_cap"],
            tools=[f"{name}_tool"],
            model="fast",
            max_tokens=256,
        )


REGISTRY = FakeRegistry(["catalog_advisor", "order_tracking", "shop_manager"])


def settings(max_depth=2):
    return SimpleNamespace(max_delegation_depth=max_depth)


def no_tools(names):
    return []


# --- the guards in run_agent ------------------------------------------------


async def test_unknown_agent_is_reported_not_raised():
    run = await run_agent(
        "nonsense", "hi", registry=REGISTRY, settings=settings(), tool_provider=no_tools
    )
    assert "unknown agent" in run.text
    assert run.tokens == 0


async def test_hidden_agent_is_refused_even_when_named_directly():
    # Defence in depth: the router already filters, but a peer call or a stale routing
    # decision must not be able to run an agent this caller cannot see.
    run = await run_agent(
        "shop_manager",
        "list my products",
        registry=REGISTRY,
        settings=settings(),
        tool_provider=no_tools,
        allowed_agents={"catalog_advisor"},
    )
    assert "not available" in run.text


async def test_allowed_agent_runs(monkeypatch):
    class FakeAgent:
        async def ainvoke(self, payload):
            return {"messages": [SimpleNamespace(content="here you go")]}

    monkeypatch.setattr(factory, "build_agent", lambda *a, **k: FakeAgent())
    run = await run_agent(
        "catalog_advisor",
        "find me a dress",
        registry=REGISTRY,
        settings=settings(),
        tool_provider=no_tools,
        allowed_agents={"catalog_advisor"},
    )
    assert run.text == "here you go"


async def test_setup_failure_is_contained(monkeypatch):
    # build_agent resolves the model, which raises on a missing key or a bad model id.
    # run_agent documents "never raises", so this must come back as a placeholder.
    def boom(*a, **k):
        raise RuntimeError("no API key")

    monkeypatch.setattr(factory, "build_agent", boom)
    run = await run_agent(
        "catalog_advisor", "hi", registry=REGISTRY, settings=settings(), tool_provider=no_tools
    )
    assert "could not complete" in run.text


# --- the ask_<peer> surface -------------------------------------------------


def peer_names(**kwargs):
    tools = _ask_peer_tools(REGISTRY, settings(), no_tools, 1, [], **kwargs)
    return sorted(t.__name__ for t in tools)


def test_peer_tools_are_named_ask_agent():
    assert peer_names() == ["ask_catalog_advisor", "ask_order_tracking", "ask_shop_manager"]


def test_an_agent_gets_no_tool_for_itself():
    assert "ask_catalog_advisor" not in peer_names(exclude="catalog_advisor")


def test_hidden_agents_get_no_peer_tool():
    # The merchant-only agent must not be reachable by delegation from a client turn.
    assert peer_names(allowed_agents={"catalog_advisor", "order_tracking"}) == [
        "ask_catalog_advisor",
        "ask_order_tracking",
    ]


def test_peer_tool_description_carries_the_agents_own_description():
    tool = _ask_peer_tools(REGISTRY, settings(), no_tools, 1, [], exclude="order_tracking")[0]
    assert "catalog_advisor does catalog_advisor things." in tool.__doc__


async def test_depth_limit_removes_the_peer_tools_entirely(monkeypatch):
    # Cycles are impossible by construction rather than by detection: at the last
    # allowed depth the agent is simply handed no ask_* tool.
    seen = {}

    class FakeAgent:
        async def ainvoke(self, payload):
            return {"messages": [SimpleNamespace(content="ok")]}

    def capture(defn, settings_, tools):
        seen["tools"] = list(tools)
        return FakeAgent()

    monkeypatch.setattr(factory, "build_agent", capture)
    await run_agent(
        "catalog_advisor",
        "hi",
        registry=REGISTRY,
        settings=settings(max_depth=2),
        tool_provider=no_tools,
        depth=1,
    )
    assert seen["tools"] == []


async def test_delegation_depth_one_disables_peer_calls(monkeypatch):
    seen = {}

    class FakeAgent:
        async def ainvoke(self, payload):
            return {"messages": [SimpleNamespace(content="ok")]}

    monkeypatch.setattr(
        factory, "build_agent", lambda d, s, tools: (seen.update(tools=list(tools)), FakeAgent())[1]
    )
    await run_agent(
        "catalog_advisor",
        "hi",
        registry=REGISTRY,
        settings=settings(max_depth=1),
        tool_provider=no_tools,
    )
    assert seen["tools"] == []

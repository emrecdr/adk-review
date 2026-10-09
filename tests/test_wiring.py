"""The package loads the way `adk web .` loads it, on both arms, and answers through the real engine."""

from __future__ import annotations

import importlib
import sys

import pytest
from conftest import ROOT, judge_module, load_agent, run_turn
from google.adk.cli.utils.agent_loader import AgentLoader
from google.adk.models.lite_llm import LiteLlm


def test_the_loader_lists_it_and_the_agent_is_named_after_its_folder(monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("REVIEW_PROVIDER", "gemini")
    loader = AgentLoader(agents_dir=".")
    assert "adk_review" in loader.list_agents()
    loaded = loader.load_agent("adk_review")
    assert loaded.root_agent.name == "adk_review", "the App, which the loader prefers: a bare agent loses the plugins"


def test_adk_web_lists_the_package_and_nothing_else(monkeypatch) -> None:
    """`adk web` uses ADK's nested loader, not the flat one `adk run` and the test above use: any folder holding an
    `agent.py`, five levels down, is an agent of its own. Measured in the browser: the graph module, then
    `judge/agent.py`, put `adk_review.judge` in the dropdown, and choosing it failed with no `root_agent`."""
    from google.adk.cli.utils._nested_agent_loader import NestedAgentLoader

    monkeypatch.chdir(ROOT)
    assert sorted(NestedAgentLoader(".").list_agents()) == ["adk_review"]


def test_the_package_imports_nothing_at_its_root(monkeypatch) -> None:
    """`python -m adk_review.review` imports the package before the command's `main` reads the root `.env`, and
    `agent.py` builds its agents when it is imported. Measured with the scaffold's `from . import agent` kept:
    `.env` named Copilot and the command's collector was built on Gemini. The loader test above is the other
    half: `adk web` finds `agent.py` without the line."""
    for name in [m for m in sys.modules if m == "adk_review" or m.startswith("adk_review.")]:
        monkeypatch.delitem(sys.modules, name)
    importlib.import_module("adk_review")
    assert "adk_review.agent" not in sys.modules


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("gemini", str), ("copilot", LiteLlm), (None, LiteLlm), ("", LiteLlm), ("  ", LiteLlm)],
)
def test_both_arms_build_a_model(provider: str | None, expected: type, monkeypatch) -> None:
    """Gemini is a native model string; everything else is LiteLlm. One variable decides, and names Copilot, the
    first-class arm, when it names none: left out, or left blank as a blank `REVIEW_MODEL=` is the default model.
    Measured by review: python-dotenv reads `REVIEW_PROVIDER=` as the empty string, which was refused as no provider
    at all."""
    if provider is None:
        monkeypatch.delenv("REVIEW_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("REVIEW_PROVIDER", provider)
    monkeypatch.delenv("REVIEW_MODEL", raising=False)
    name = judge_module("config")
    sys.modules.pop(name, None)
    assert isinstance(importlib.import_module(name).build_model(), expected)


def test_every_judging_agent_reads_only_its_instruction_at_temperature_zero(monkeypatch) -> None:
    """A guideline's prompt is its instruction with the change in it; the conversation would repeat the change a
    second time. And a severity is a decision, so the judging agents sample at zero."""
    from google.adk.agents import LlmAgent

    entry = load_agent(monkeypatch)
    judges = [n for n in entry.root_agent.graph.nodes if isinstance(n, LlmAgent) and n.name != "intake"]
    judges.append(importlib.import_module(judge_module("verify")).build_verifier(0))  # in a graph of its own
    for agent in judges:
        assert agent.include_contents == "none", agent.name
        assert agent.generate_content_config is not None and agent.generate_content_config.temperature == 0.0, (
            agent.name
        )


def test_every_agent_with_tools_carries_the_guardrail(monkeypatch) -> None:
    """A tool argument is untrusted input. test_callbacks proves the callback; this pins that every agent with tools
    has it attached. The chat reads no diff, so its guard holds the conversation to its repository alone."""
    from google.adk.agents import LlmAgent

    module = load_agent(monkeypatch)
    armed = [n for n in module.root_agent.graph.nodes if isinstance(n, LlmAgent) and n.tools]
    assert armed, "no agent with tools"
    for agent in armed:
        callbacks = agent.before_tool_callback
        callbacks = callbacks if isinstance(callbacks, list) else [callbacks] if callbacks else []
        assert "one_repository" in [cb.__name__ for cb in callbacks], agent.name


@pytest.mark.asyncio
async def test_it_answers_through_the_real_engine_with_a_fake_model(monkeypatch) -> None:
    module = load_agent(monkeypatch)
    final, _state = await run_turn(module, "hello")
    assert final, "no final response reached the runner"


def test_the_suite_reads_neither_the_developers_env_nor_the_network() -> None:
    """LiteLLM, in its default mode, reads the nearest `.env` into the process when imported, and fetches its model
    map from the network. Measured: the suite took five keys from the developer's `.env` into every test, and every
    import tried the network first. `.env.example` offers every run the second switch."""
    import os

    assert os.environ.get("LITELLM_MODE") == "PRODUCTION"
    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"
    assert "\nLITELLM_LOCAL_MODEL_COST_MAP=True\n" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_no_test_inherits_a_provider_or_a_model_from_the_shell() -> None:
    """A developer's own `REVIEW_PROVIDER=copilot` or `REVIEW_VERIFIER_MODEL` would send a test to a real model."""
    import os

    assert not [name for name in os.environ if name.startswith(("REVIEW_", "GOOGLE_GENAI_USE_"))]

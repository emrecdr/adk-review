"""The package loads the way `adk web .` loads it, on both arms, and answers through the real engine."""

from __future__ import annotations

import importlib
import json
import sys

import litellm
import pytest
from conftest import INTAKE, ROOT, Chat, _cached_copilot_token, confirms, judge_module, load_agent, run_turn
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
    default arm, when it names none: left out, or left blank as a blank `REVIEW_MODEL=` is the default model.
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


def _described(schema: dict) -> dict[str, str]:
    """Every field's description in a JSON schema, by its path: `$ref`s followed, `anyOf`s and array items read."""
    found: dict[str, str] = {}

    def walk(node: dict, path: str) -> None:
        while "$ref" in node:
            node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
        for name, field in node.get("properties", {}).items():
            if "description" in field:
                found[path + name] = field["description"]
            walk(field, f"{path}{name}.")
        for inner in (node.get("items"), *node.get("anyOf", ())):
            if isinstance(inner, dict):
                walk(inner, path)

    walk(schema, "")
    return found


@pytest.mark.parametrize("where", ["core.findings:Review", "judge.verify:Judgement"])
def test_each_arm_is_sent_each_answers_schema_as_it_is_written(where: str) -> None:
    """What a model is asked to answer in, as each arm's own converter writes it into the request: google-genai's for
    Gemini, ADK's for Copilot through LiteLLM. Every field keeps the description it is written with, the severity's
    scale among them, which the guidelines judge by. Measured, two one-line changes each passed every other test: a
    PEP 695 `type Severity` made the scale a `$ref`, whose description both arms dropped; `extra="forbid"` sent
    Gemini `additionalProperties`, which its `response_schema` has refused with a 400. Copilot's is strict already."""
    from google.adk.models.lite_llm import _to_litellm_response_format
    from google.genai import Client, _transformers

    module, name = where.split(":")
    schema = getattr(importlib.import_module(f"adk_review.{module}"), name)
    written = _described(schema.model_json_schema())
    assert "findings.severity" in written or "holds" in written, written
    developer_api = Client(api_key="offline")._api_client  # the stricter of Gemini's two: no Vertex AI project
    gemini = _transformers.t_schema(developer_api, schema).model_dump(exclude_none=True, by_alias=True, mode="json")
    assert _described(gemini) == written and "additionalProperties" not in str(gemini), gemini
    copilot = _to_litellm_response_format(schema, "github_copilot/gpt-4.1")["json_schema"]
    assert copilot["strict"] is True and _described(copilot["schema"]) == written, copilot


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


def _intakes(asked: list[tuple[str, list[str]]]) -> list[bool]:
    """For each request sent, in order, whether it was the intake's."""
    return [INTAKE in instruction for instruction, _turns in asked]


@pytest.mark.asyncio
async def test_a_turn_whose_intake_confirms_no_scope_ends_at_its_answer(monkeypatch, asked) -> None:
    """Measured before: "hello" ran the whole graph, the guidelines skipped for want of a change, and the verdict agent
    called its model to report a review of nothing beside the intake's own question: two calls, one wasted, a turn."""
    from adk_review.judge.graph import VERDICT

    final, state = await run_turn(load_agent(monkeypatch), "hello")
    assert final and _intakes(asked) == [True] and VERDICT not in state, asked


@pytest.mark.asyncio
async def test_a_later_turn_reviews_again_only_when_its_own_intake_confirms(
    monkeypatch, fake_provider, asked, demo_repo
) -> None:
    """Measured before: the scope an intake confirmed stayed in the session, so every later turn, a "thanks!" among
    them, gathered the repository again and judged it again: five model calls, and another verdict that could differ.
    A turn reviews the scope its own intake confirmed, or none."""
    from adk_review.judge.graph import VERDICT

    chat = Chat(load_agent(monkeypatch))
    confirms(fake_provider, demo_repo)
    _final, state = await chat.say("Review feature/payments against main.")
    assert _intakes(asked) == [True, True, False, False, False, False] and state[VERDICT], asked
    fake_provider.clear()
    asked.clear()
    final, _state = await chat.say("thanks!")
    assert final and _intakes(asked) == [True], asked


@pytest.mark.asyncio
async def test_in_chat_each_judging_agent_reads_its_instruction_and_one_fixed_turn(
    monkeypatch, fake_provider, asked, demo_repo
) -> None:
    """Every untrusted text a judging agent reads sits inside its instruction's fences. Measured before: ADK hands a
    node its predecessor's output as a turn of its own, so chat's verdict read every guideline's findings as raw JSON,
    the branch's quoted lines among them, outside the fences its instruction holds them in, and each guideline read
    the intake's reply. The command's read one turn, `review`; in chat each judging agent reads the same."""
    from adk_review.judge.plugins import ONE_TURN

    confirms(fake_provider, demo_repo)
    await run_turn(load_agent(monkeypatch), "Review feature/payments against main.")
    judging = [turns for instruction, turns in asked if INTAKE not in instruction]
    assert len(judging) == 4 and all(turns == [ONE_TURN] for turns in judging), judging


@pytest.mark.asyncio
async def test_the_intake_is_told_the_repository_its_conversation_confirmed(
    monkeypatch, fake_provider, asked, demo_repo
) -> None:
    """Measured before: as a node of the graph the intake reads its turn's message alone, so a person who had named the
    repository once named it again with every branch. Its instruction carries the one its conversation confirmed,
    the one the guard holds it to."""
    from adk_review.judge.agents import REPOSITORY

    chat = Chat(load_agent(monkeypatch))
    confirms(fake_provider, demo_repo)
    _final, state = await chat.say("Review feature/payments against main.")
    assert state[REPOSITORY] not in asked[0][0], "none confirmed yet"
    fake_provider.clear()
    asked.clear()
    await chat.say("Now review feature/other.")
    assert state[REPOSITORY] in asked[0][0] and len(asked[0][1]) == 1, asked


def _said(message: dict) -> str:
    """A LiteLLM message's text, whether ADK wrote its content as a string or as parts."""
    content = message.get("content") or ""
    return content if isinstance(content, str) else "".join(part.get("text", "") for part in content)


@pytest.mark.asyncio
async def test_the_chat_gate_and_the_one_turn_hold_on_the_copilot_arm(monkeypatch, tmp_path, demo_repo) -> None:
    """The gate and the judging agents' one turn on Copilot's own path: ADK's LiteLLM wrapper, a tool call read back
    from LiteLLM's reply, only the network client replaced. A greeting is the intake's one call; a review turn's
    judging agents each read one turn, `review`, beside the instruction LiteLLM sends as the system turn."""
    from google.adk.models.lite_llm import LiteLLMClient

    from adk_review.judge.graph import VERDICT
    from adk_review.judge.plugins import ONE_TURN

    sent: list[tuple[str, list[str]]] = []
    scope = {"repo": str(demo_repo), "base": "main", "head": "feature/payments"}

    async def answer(_client, messages, **request):
        system = "".join(_said(m) for m in messages if m["role"] == "system")
        turns = [_said(m) for m in messages if m["role"] != "system"]
        sent.append((system, turns))
        if INTAKE in system and turns[-1].startswith("Review"):
            call = {
                "id": "c1",
                "type": "function",
                "function": {"name": "inspect_repository", "arguments": json.dumps(scope)},
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
        else:
            reply = '{"findings": [], "summary": "Nothing."}' if request.get("response_format") else "Noted."
            message = {"role": "assistant", "content": reply}
        return litellm.ModelResponse(
            choices=[{"message": message}], usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        )

    monkeypatch.setattr(LiteLLMClient, "acompletion", answer)
    _cached_copilot_token(monkeypatch, tmp_path)
    chat = Chat(load_agent(monkeypatch, "copilot", "gpt-4.1"))
    await chat.say("hello")
    assert _intakes(sent) == [True], sent
    sent.clear()
    _final, state = await chat.say("Review feature/payments against main.")
    judging = [turns for system, turns in sent if INTAKE not in system]
    assert state[VERDICT] and len(judging) == 4 and all(turns == [ONE_TURN] for turns in judging), judging

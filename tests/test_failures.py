"""A provider error is a sentence, never a traceback — each agent's own hook, then the plugin — and nothing judges
what was never gathered."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import litellm
import pytest
from conftest import (
    CANNED_REVIEW,
    GUIDELINE_NAMES,
    INTAKE,
    QUOTA_TEXT,
    REVIEW_REQUEST,
    ROOT,
    SPACES,
    Chat,
    _cached_copilot_token,
    _committed,
    _file_url,
    _git_in,
    _pushed_from,
    _with_profiles,
    confirms,
    judge_module,
    load_agent,
    model_check,
    model_key,
    profiles_of,
    quota,
    run_review,
    run_turn,
)
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel


def _judged(state: dict) -> set[str]:
    """The judging agents that left an answer in state, each by its own key: every guideline `default` runs, and chat's
    verdict. Spelled by hand, a renamed key would leave a check that nothing was judged passing on nothing."""
    from adk_review.judge.graph import VERDICT

    return {key for key in (*map(model_key, GUIDELINE_NAMES), VERDICT) if key in state}


@pytest.mark.asyncio
async def test_when_every_call_fails_the_answer_names_the_error_and_nothing_raises(monkeypatch, fake_provider):
    """Measured on Gemini's free tier before this: `adk run` printed sixty lines of traceback for a 429."""
    module = load_agent(monkeypatch)
    fake_provider.raises_when("", quota())  # every instruction contains the empty string: every call fails
    final, state = await run_turn(module, REVIEW_REQUEST)
    assert QUOTA_TEXT in final, final
    assert not _judged(state), "the turn ended at the intake's error: nothing judged"
    assert not [k for k in state if k.startswith("temp:")], "the note lives for one invocation, not the session"


@pytest.mark.asyncio
async def test_when_the_intake_fails_the_turn_ends_at_its_error_and_nothing_is_judged(monkeypatch, fake_provider):
    """An intake that fails confirms no scope: a reviewer run over nothing would find nothing, and "did not look" must
    never read as "found nothing". Measured before the gate: the graph went on, each guideline answered that nothing
    was gathered, and the verdict agent called its model to report it; the turn now ends at the intake's error."""
    module = load_agent(monkeypatch)
    fake_provider.raises_when(INTAKE, quota())
    final, state = await run_turn(module, REVIEW_REQUEST)
    assert QUOTA_TEXT in final, final
    assert not _judged(state), "nothing judged, and no verdict on nothing"


def test_a_guideline_with_nothing_gathered_or_a_failure_before_it_says_so_and_why() -> None:
    """The guidelines' guard: no change, or a failure earlier in the turn, and the guideline is skipped with a
    `Review` saying it did not look, and why, which the verdict is told never to approve on; with the change and no
    failure it runs."""
    agents = importlib.import_module(judge_module("agents"))

    def guarded(**state) -> str | None:
        answer = agents.without_evidence(SimpleNamespace(state=state))
        return None if answer is None else answer.parts[0].text

    assert guarded(**{agents.DIFF: "+x = 1"}) is None
    assert "Nothing was gathered, so nothing was judged against this guideline." in guarded(**{agents.DIFF: ""})
    said = guarded(**{agents.DIFF: "+x = 1", agents.FAILURE: f"intake: {QUOTA_TEXT}"})
    assert "Nothing was judged" in said and QUOTA_TEXT in said, said


@pytest.mark.asyncio
async def test_when_one_lane_fails_the_others_finish_and_the_verdict_still_answers(
    monkeypatch, fake_provider, demo_repo
):
    """The one agent that runs after a failed guideline must not die on its missing key: the plugin answers nothing
    for a guideline, so the key is a hole the command names — in chat, the verdict reads it as an empty section. The
    checks node gathers the change from the scope the intake confirms, here by the fake intake's own tool call."""
    from adk_review.judge.graph import VERDICT

    module = load_agent(monkeypatch)
    fake_provider.raises_when("tests are honest", quota())
    confirms(fake_provider, demo_repo)
    final, state = await run_turn(module, REVIEW_REQUEST)
    assert final and state[VERDICT], "the verdict agent answered"
    assert state[model_key("security")]["findings"], "the other guidelines finished"
    assert model_key("tests") not in state, "a hole, for the code that decides"


#: Words of chat's verdict agent's instruction alone, and of the security guideline's.
THE_VERDICT, SECURITY = "Rules and guidelines have judged one change", "security defects and unsafe patterns"


@pytest.mark.asyncio
async def test_chats_verdict_reads_what_the_diff_bears_out_and_the_verdict_code_decides(
    monkeypatch, fake_provider, asked, demo_repo
):
    """Measured by review: chat's verdict agent read each guideline's answer as its model gave it and decided the
    verdict itself, where the command grounds every finding in the diff and decides in code: a blocker quoting no line
    of the change reached chat's verdict as one. Chat grounds and decides as the command does now, a finding whose
    quote the diff does not bear out named as dropped, and its verdict agent writes up what code decided."""
    module = load_agent(monkeypatch)
    confirms(fake_provider, demo_repo)
    await run_turn(module, REVIEW_REQUEST)
    (read,) = [system for system, _turns in asked if THE_VERDICT in system]
    # The canned finding stands on lint's S602 line and folds into it, as the command's report folds it.
    assert "verdict: REQUEST_CHANGES" in read and "## guideline.security" in read, read
    assert "- dropped," not in read, "every canned quote is a line of the planted diff"

    asked.clear()
    planted = CANNED_REVIEW["findings"][0]
    invented = {**CANNED_REVIEW, "findings": [{**planted, "severity": "blocker", "title": "an invented injection"}]}
    invented["findings"][0]["evidence"] = "eval(input())"  # no line of the change
    fake_provider.answers_when(SECURITY, invented)
    await run_turn(module, REVIEW_REQUEST)
    (read,) = [system for system, _turns in asked if THE_VERDICT in system]
    assert "verdict: DEGRADED" in read and "- dropped, the diff does not bear its quote out: blocker" in read, read
    assert "an invented injection" in read


@pytest.mark.asyncio
async def test_a_guideline_that_fails_in_a_later_turn_never_reads_as_its_earlier_answer(
    monkeypatch, fake_provider, asked, demo_repo
):
    """Read by review: a guideline's answer stays in the session's state from one turn to the next, and chat's
    grounding read the state whole, so a guideline that failed in a later turn read as its earlier answer, and code
    approved what no guideline judged that turn. Only what this turn wrote is read."""
    module = load_agent(monkeypatch)
    confirms(fake_provider, demo_repo)
    chat = Chat(module)
    await chat.say(REVIEW_REQUEST)
    fake_provider.raises_when(SECURITY, quota())
    asked.clear()
    await chat.say(REVIEW_REQUEST)
    (read,) = [system for system, _turns in asked if THE_VERDICT in system]
    assert "## guideline.security — could not look:" in read and "verdict: DEGRADED" in read, read


@pytest.mark.asyncio
async def test_a_guideline_its_guard_skipped_reads_as_one_that_could_not_look(
    monkeypatch, fake_provider, asked, demo_repo
):
    """Read by review: the intake's call after its tool confirmed the scope failed, the checks gathered the change,
    and every guideline's guard skipped it, its answer a review naming no finding, which code read as nothing found:
    approved, no guideline having judged anything. A guideline its guard skipped could not look."""
    from conftest import FakeModel

    module = load_agent(monkeypatch)
    confirms(fake_provider, demo_repo)
    answer = FakeModel.generate_content_async

    async def fails_after_its_tool(self, llm_request, stream=False):
        last = (llm_request.contents[-1].parts or ()) if llm_request.contents else ()
        if INTAKE in str(llm_request.config.system_instruction or "") and any(p.function_response for p in last):
            raise RuntimeError("upstream connect error: reset before headers")
        async for response in answer(self, llm_request, stream):
            yield response

    monkeypatch.setattr(FakeModel, "generate_content_async", fails_after_its_tool)
    await Chat(module).say(REVIEW_REQUEST)
    (read,) = [system for system, _turns in asked if THE_VERDICT in system]
    assert "## guideline.security — could not look:" in read and "verdict: DEGRADED" in read, read


class _RateLimitError(Exception):
    """A provider's refusal for its rate, as LiteLLM's `RateLimitError` and genai's `ClientError` carry it: a 429."""

    status_code = 429


@pytest.mark.asyncio
async def test_after_a_rate_limit_a_call_is_answered_unsent_and_a_chat_turn_asks_again() -> None:
    """A 429 the client's retries and any fallback could not get past ends what the review spends: each later call is
    answered in the model's place, never sent. An error with no 429 stops nothing, and in chat the next turn asks."""
    from adk_review.judge.plugins import StopAtRateLimit

    request = SimpleNamespace(config=SimpleNamespace(response_schema=_Answer))
    at = SimpleNamespace(agent_name="guideline_security", state={})
    limited = _RateLimitError("429: Sorry, you've exceeded your rate limit for utility models.")
    stop = StopAtRateLimit(per_turn=False)
    assert await stop.before_model_callback(callback_context=at, llm_request=request) is None
    assert await stop.on_model_error_callback(callback_context=at, llm_request=request, error=quota()) is None
    assert await stop.before_model_callback(callback_context=at, llm_request=request) is None, "no 429: no stop"
    assert await stop.on_model_error_callback(callback_context=at, llm_request=request, error=limited) is None
    refused = await stop.before_model_callback(callback_context=at, llm_request=request)
    assert refused.error_code == "RATE_LIMITED" and "exceeded your rate limit" in refused.error_message, refused
    turned = StopAtRateLimit(per_turn=True)
    await turned.on_model_error_callback(callback_context=at, llm_request=request, error=limited)
    await turned.before_run_callback(invocation_context=SimpleNamespace())
    assert await turned.before_model_callback(callback_context=at, llm_request=request) is None, "a new turn asks"


def test_a_review_that_met_a_rate_limit_sends_no_later_call(monkeypatch, fake_provider, asked, cli, demo_repo, capsys):
    """Measured live: Copilot's limit for utility models lasts hours, and each guideline after the first 429 sent three
    requests into it, each a hole all the same. One call at a time here, so later is well defined: the first is sent
    and refused, and every later one is named and never sent, nor counted as attempted."""
    for name in ("adk_review.judge.graph", "adk_review.judge.verify"):
        monkeypatch.setattr(importlib.import_module(name), "CONCURRENCY", 1)
    fake_provider.raises_when("", _RateLimitError("429: Sorry, you've exceeded your rate limit for utility models."))
    assert run_review(cli, demo_repo) == 3
    out = capsys.readouterr().out
    assert len(asked) == 1, [instruction[:60] for instruction, _turns in asked]
    assert out.count("RATE_LIMITED: not sent") == len(GUIDELINE_NAMES) - 1, out
    assert "0 model call(s) completed of 1 attempted" in out, out


class _Answer(BaseModel):
    """A schema a judging agent answers in, as small as one can be."""

    findings: list[str]


@pytest.mark.asyncio
async def test_a_reply_the_schema_refuses_is_that_agents_own_error() -> None:
    """Read as ADK reads a structured reply: every part that is not a thought, a fence around the whole stripped. A
    cut reply that carries an error code and keeps its text, as ADK's LiteLLM wrapper marks one, is refused too."""
    plugin = importlib.import_module(judge_module("plugins")).ReportProviderErrors()
    judging, prose = SimpleNamespace(agent_name="judging"), SimpleNamespace(agent_name="intake")
    asked = SimpleNamespace(config=SimpleNamespace(response_schema=_Answer))
    assert await plugin.before_model_callback(callback_context=judging, llm_request=asked) is None

    async def answer(text: str, context=judging, **extra) -> LlmResponse | None:
        reply = LlmResponse(content=types.ModelContent(text), **extra)
        return await plugin.after_model_callback(callback_context=context, llm_response=reply)

    refused = await answer("The change looks fine to me.")
    assert refused.error_code == "SCHEMA_MISMATCH", refused
    assert refused.error_message.startswith("the reply did not match the schema: Invalid JSON"), refused
    cut = await answer('{"findings": ["a', error_code="MAX_TOKENS", finish_reason=types.FinishReason.MAX_TOKENS)
    assert cut.error_message == "the reply did not match the schema: the reply was cut short (MAX_TOKENS)"
    assert await answer('{"findings": []}') is None
    assert await answer('```json\n{"findings": []}\n```') is None, "a fence ADK strips is no mismatch"
    thinking = types.Part(text="Let me think it through.", thought=True)
    thought = LlmResponse(content=types.ModelContent([thinking, types.Part(text='{"findings": []}')]))
    assert await plugin.after_model_callback(callback_context=judging, llm_response=thought) is None, (
        "a thought skipped"
    )
    assert await answer("   ") is None, "nothing ADK would validate"
    assert await answer("Hello.", context=prose) is None, "an agent that answers in prose has no schema to fit"


def test_a_reply_the_schema_refuses_fails_that_lane_alone(demo_repo, fake_provider, cli, capsys) -> None:
    """Measured on ADK 2.9.2 before: one guideline that answered prose ended the whole run, and every guideline
    still in flight was named with its error. Now that guideline fails under its own name and the others answer."""
    fake_provider.answers_when("security defects and unsafe patterns", "The change looks fine to me.")
    assert run_review(cli, demo_repo) == 3
    out = capsys.readouterr().out
    failed = f"### {model_check('security')} — FAILED: SCHEMA_MISMATCH: the reply did not match the schema"
    assert failed in out, out
    assert f"### {model_check('tests')}\n" in out and f"### {model_check('complexity')}\n" in out, out


def test_litellms_banner_is_turned_off_where_the_copilot_arm_is_built(monkeypatch) -> None:
    """LiteLLM prints a red "Give Feedback" banner on stdout with every error it maps. The arm it serves, and only
    that arm, imports it and turns the banner off as its model is built: measured at the top of a command, the
    import cost about a second of every start and fetched a price list."""
    monkeypatch.setattr(litellm, "suppress_debug_info", False)
    monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
    importlib.import_module(judge_module("config")).build_model()
    assert litellm.suppress_debug_info


def test_a_providers_error_is_one_readable_line() -> None:
    """genai's `str` is the whole JSON body; its `.message` is the sentence; LiteLLM's `.message` carries its class as
    a prefix and its code as `.status_code`. The same line from either arm."""
    from google.genai import errors

    from adk_review.judge.plugins import describe

    message = "You exceeded your current quota. Please retry in 19s."
    spent = errors.ClientError(429, {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED"}})
    assert describe(spent) == f"429: {message}", "the status is the error code, not the message"
    limited = litellm.exceptions.RateLimitError("Rate limit reached", llm_provider="github_copilot", model="gpt-4.1")
    assert describe(limited) == "429: Rate limit reached", "the class prefix goes, the code stays"
    assert describe(RuntimeError("x\n y")) == "x y", "no code, so the text alone; the caller adds the class"
    assert describe(RuntimeError("z" * 1000)).endswith("[… cut at 400 characters]")


def test_each_arms_error_reads_as_its_reason_not_its_body() -> None:
    """Measured by review: Copilot's error, as LiteLLM's own mapping raises it, read as its raw JSON body behind a
    second class name, where Gemini's reads as its sentence; and a Gemini call past its deadline raised a bare
    `TimeoutError`, named with no reason at all."""
    from litellm.litellm_core_utils.exception_mapping_utils import exception_type
    from litellm.llms.github_copilot.chat.transformation import GithubCopilotConfig

    from adk_review.judge.plugins import describe

    body = '{"error": {"message": "The requested model is not supported.", "code": "model_not_supported"}}'
    raw = GithubCopilotConfig().get_error_class(error_message=body, status_code=400, headers={})
    with pytest.raises(litellm.exceptions.BadRequestError) as mapped:
        exception_type(model="gpt-5", original_exception=raw, custom_llm_provider="github_copilot")
    assert describe(mapped.value) == "400: model_not_supported: The requested model is not supported."
    assert "deadline" in describe(TimeoutError()), "the one error that says nothing of itself"
    # Measured live, Copilot's rate limit: a body of plain text, read as "RateLimitError: 429: RateLimitError:
    # Github_copilotException - Sorry, …", the class named twice and the provider's own once.
    said = "Sorry, you've exceeded your rate limit."
    raw = GithubCopilotConfig().get_error_class(error_message=said, status_code=429, headers={})
    with pytest.raises(litellm.exceptions.RateLimitError) as limited:
        exception_type(model="gpt-4.1", original_exception=raw, custom_llm_provider="github_copilot")
    assert describe(limited.value) == f"429: {said}"


def test_an_error_nothing_named_is_a_sentence_and_exit_3_never_a_verdict(monkeypatch, capsys) -> None:
    """Python ends an uncaught exception with exit 1, the code for "a blocker was found". Measured before: errors
    found and named one at a time, six recorded in the design record and a seventh by review, each ended in a
    traceback and exit 1. Now any error nothing else named is a review that did not finish: a sentence, exit 3."""
    module = importlib.import_module("adk_review.review")

    def unforeseen(*_args, **_kwargs):
        raise RuntimeError("something nothing named")

    monkeypatch.setattr(module, "build_parser", unforeseen)
    assert module.main(["--head", "x"]) == 3
    err = capsys.readouterr().err
    assert "the review did not finish: RuntimeError: something nothing named" in err and "Traceback" not in err, err


@pytest.mark.parametrize(
    ("raised", "code", "said"),
    [
        (KeyboardInterrupt, 130, "interrupted: the review did not finish"),
        (BrokenPipeError, 3, "the report's reader closed it before its end: the verdict was not delivered"),
    ],
)
def test_ctrl_c_and_a_closed_pipe_are_one_sentence_each(
    raised: type[BaseException], code: int, said: str, monkeypatch, capsys
) -> None:
    """Measured before: Ctrl-C printed a traceback, and a reader that closed the pipe early, as `| head` does, gave
    exit 3 or Python's own 120. Each is one sentence now: Ctrl-C the shell's own 130, a closed pipe exit 3."""
    module = importlib.import_module("adk_review.review")

    def stopped(*_args, **_kwargs):
        raise raised

    monkeypatch.setattr(module, "_review", stopped)
    assert module.main([]) == code
    err = capsys.readouterr().err
    assert said in err and "Traceback" not in err, err


def test_a_reviewer_that_cannot_load_is_a_sentence_and_exit_3(tmp_path) -> None:
    """Measured before: a package that would not import ended `python -m adk_review.review` in a traceback and
    Python's exit 1, the code for "a blocker was found", before `main` ran."""
    (tmp_path / "dotenv.py").write_text('raise RuntimeError("a broken install")\n', encoding="utf-8", newline="\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), os.environ.get("PYTHONPATH", "")])}
    done = subprocess.run(
        [sys.executable, "-m", "adk_review.review", "--list-checks"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert done.returncode == 3, done.stderr
    assert "the reviewer could not load (RuntimeError: a broken install)" in done.stderr, done.stderr
    assert "Traceback" not in done.stderr


def test_a_dot_env_that_is_not_utf8_is_a_sentence_and_exit_2(monkeypatch, tmp_path, capsys) -> None:
    """Windows PowerShell 5.1's `>` writes UTF-16. Measured before: a `.env` written that way ended the command in a
    `UnicodeDecodeError` traceback and exit 1, the code for "a blocker was found"."""
    (tmp_path / ".env").write_text("REVIEW_PROVIDER=copilot\n", encoding="utf-16")
    module = importlib.import_module("adk_review.review")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    assert module.main(["--head", "x"]) == 2
    err = capsys.readouterr().err
    assert ".env is not UTF-8" in err and "Traceback" not in err, err


def test_a_misspelt_provider_is_a_sentence_and_exit_2_from_every_command() -> None:
    """Measured before: a chat graph built at import raised on `REVIEW_PROVIDER=gemni` — a
    traceback and Python's exit 1, the code for "a blocker was found" — before the readiness check could answer."""
    import os
    import subprocess

    env = {**os.environ, "REVIEW_PROVIDER": "gemni", "LITELLM_LOCAL_MODEL_COST_MAP": "True"}
    done = subprocess.run(
        [sys.executable, "-m", "adk_review.review", "--head", "x"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert done.returncode == 2, done.stderr[-400:]
    assert "REVIEW_PROVIDER must be one of" in done.stderr and "'gemni'" in done.stderr, done.stderr[-400:]
    assert "Traceback" not in done.stderr, done.stderr[-400:]


def test_the_report_names_the_model_that_ran(monkeypatch, tmp_path: Path) -> None:
    """Every entry point lets the shell's variables win over `.env`, so a stale export picks the arm or the model
    unseen: the report names both, and Vertex AI when the environment sends the Gemini arm there. Measured by review:
    a GitHub Enterprise tenant's own Copilot endpoint went unnamed, in the report and in the cache's key, which reads
    this line, so one tenant's review was served for another's. And traced in LiteLLM's source: the calls go where the
    cached API key's endpoint says before the variable, so the report reads the two in that order."""
    load_agent(monkeypatch)
    config = importlib.import_module(judge_module("config"))
    monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
    assert config.serving() == "fake on copilot"
    monkeypatch.setenv("GITHUB_COPILOT_API_BASE", "https://copilot-api.my-company.ghe.com")
    assert config.serving() == "fake on copilot through copilot-api.my-company.ghe.com"
    _cached_copilot_token(monkeypatch, tmp_path)
    issued = tmp_path / "api-key.json"
    for endpoint, said in (
        ("api.individual.githubcopilot.com", "fake on copilot"),  # github.com's own, the variable's tenant passed over
        ("copilot-api.other.ghe.com", "fake on copilot through copilot-api.other.ghe.com"),
    ):
        issued.write_text(json.dumps({"token": "a key", "endpoints": {"api": f"https://{endpoint}"}}), encoding="utf-8")
        assert config.serving() == said, "the cached key's endpoint before the variable, as LiteLLM reads them"
    monkeypatch.setenv("REVIEW_PROVIDER", "gemini")
    assert config.serving() == "fake on gemini"
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    assert config.serving() == "fake on gemini through Vertex AI"
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "false")  # the name ADK 2.9.2 reads first, deprecating the other
    assert config.serving() == "fake on gemini"
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI")
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "TRUE")
    assert config.serving() == "fake on gemini through Vertex AI"


def test_vertex_ai_is_ready_wherever_the_sdk_finds_its_project(monkeypatch) -> None:
    """Measured by review: the Gemini arm sent through Vertex AI was refused for want of an API key, and once fixed,
    for want of `GOOGLE_CLOUD_PROJECT`, while google-genai also takes the project Application Default Credentials name —
    a service account's file, gcloud's own project, the metadata server Cloud Run and GKE answer from. Readiness asks
    the SDK rather than a copy of its rules; whether the credentials sign a call, only a call tells."""
    load_agent(monkeypatch)
    config = importlib.import_module(judge_module("config"))
    auth, auth_errors = importlib.import_module("google.auth"), importlib.import_module("google.auth.exceptions")
    monkeypatch.delenv("GOOGLE_API_KEY")  # the key `load_agent` gives the fake
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "true")

    def signed_in_nowhere(*_args, **_kwargs):
        raise auth_errors.DefaultCredentialsError("no Application Default Credentials were found")

    monkeypatch.setattr(auth, "default", signed_in_nowhere)  # offline, and the same on every machine
    why = config.require_ready()
    assert why and "GOOGLE_CLOUD_PROJECT" in why and "application-default login" in why, why
    monkeypatch.setattr(auth, "default", lambda *_args, **_kwargs: (object(), "named-by-adc"))
    assert config.require_ready() is None, "the project Application Default Credentials name"
    monkeypatch.setattr(auth, "default", signed_in_nowhere)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "a-project")
    assert config.require_ready() is None, "a project named outright, its credentials read at the first call"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arm", "model", "said"),
    [("copilot", "gpt-4.1", "needs a cached token"), ("gemini", "gemini-2.5-flash", "needs GOOGLE_API_KEY")],
)
async def test_chat_on_an_arm_that_cannot_run_says_why_and_calls_no_model(
    arm: str, model: str, said: str, monkeypatch, tmp_path
) -> None:
    """Measured by review: only the command asked whether its arm could run. Chat on Copilot with no token cached let
    LiteLLM start its own device login on the server's console, three minutes of polling no one saw, and keep the
    token it got readable by whoever can read its folder, where the login script keeps it to its owner. Chat asks
    once a turn, on both arms, and every model call of the turn answers the command's sentence in the call's place:
    the intake's, the one the turn makes, since an intake that could not run confirmed nothing to review."""
    from google.adk.models.google_llm import Gemini
    from google.adk.models.lite_llm import LiteLlm

    called: list[str] = []

    async def no_call(self, *_args, **_kwargs):
        called.append(type(self).__name__)
        raise AssertionError("a model was called")
        yield  # an async generator, as each model's own is

    for model_class in (LiteLlm, Gemini):
        monkeypatch.setattr(model_class, "generate_content_async", no_call)
    agent = load_agent(monkeypatch, arm, model)
    _cached_copilot_token(monkeypatch, tmp_path, None)
    monkeypatch.delenv("GOOGLE_API_KEY")  # the key `load_agent` gives the fake
    final, state = await run_turn(agent, "Review /nowhere, feature against main.")
    assert said in final and called == [], (final, called)
    assert not _judged(state), "the turn ended at the intake's answer"


def test_copilot_refuses_a_model_that_cannot_judge_at_temperature_zero(monkeypatch, tmp_path) -> None:
    """Measured by review: LiteLLM refuses temperature 0, at which every judging agent runs, for Copilot's `gpt-5`,
    `gpt-5-mini`, `o3-mini` and `o4-mini`, before any call, so a review on one ended in exit 3, every guideline a
    hole. It is said before anything is spent, exit 2, of each role's model, as LiteLLM itself says it offline."""
    load_agent(monkeypatch, "copilot", "gpt-5-mini")
    _cached_copilot_token(monkeypatch, tmp_path)
    config = importlib.import_module(judge_module("config"))
    why = config.require_ready()
    assert why and why.startswith("REVIEW_MODEL=gpt-5-mini") and "temperature" in why, why
    monkeypatch.setenv("REVIEW_MODEL", "gpt-4.1")
    monkeypatch.setenv("REVIEW_VERIFIER_MODEL", "o4-mini")
    why = config.require_ready(roles=("verifier",))
    assert why and why.startswith("REVIEW_VERIFIER_MODEL=o4-mini"), why
    monkeypatch.setenv("REVIEW_VERIFIER_MODEL", "claude-sonnet-4")  # served by Copilot, and takes it
    assert config.require_ready(roles=("verifier",)) is None


def test_the_copilot_default_judges_at_temperature_zero_as_litellm_says() -> None:
    """Readiness does not ask LiteLLM about the Copilot arm's own default: importing LiteLLM took two seconds of every
    dry run and every usage error. This holds the default to LiteLLM's answer instead, asked as readiness asks every
    other model, so an upgrade that refused it fails here, not in every guideline of every review."""
    config = importlib.import_module(judge_module("config"))
    assert config._refuses_temperature_zero(config.DEFAULTS["copilot"]) is None


def test_the_gemini_arm_runs_no_model_adk_sends_to_another_provider(monkeypatch) -> None:
    """Measured by review: `REVIEW_PROVIDER=gemini` beside `REVIEW_MODEL=gpt-4.1`, Copilot's default left in `.env`,
    was ready, and ADK sends `gpt-*` to its own OpenAI class: the diff would have gone to OpenAI's API under a report
    naming gemini. On the Gemini arm every role's model and the fallback is one ADK sends to Gemini."""
    load_agent(monkeypatch)
    config = importlib.import_module(judge_module("config"))
    monkeypatch.setenv("REVIEW_MODEL", "gpt-4.1")
    why = config.require_ready()
    assert why and why.startswith("REVIEW_MODEL=gpt-4.1") and "OpenAILlm" in why, why
    monkeypatch.setenv("REVIEW_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("REVIEW_VERIFIER_MODEL", "claude-sonnet-4")
    why = config.require_ready(roles=("verifier",))
    assert why and why.startswith("REVIEW_VERIFIER_MODEL=claude-sonnet-4"), why
    monkeypatch.setenv("REVIEW_VERIFIER_MODEL", "gemma-3-27b-it")  # Gemma, served by the Gemini API
    assert config.require_ready(roles=("verifier",)) is None


def test_an_empty_copilot_token_is_no_token(monkeypatch, tmp_path) -> None:
    """Measured before: an empty token file passed the readiness check, and LiteLLM began its own device login
    inside the first model call, three minutes, its code printed where no one was looking."""
    load_agent(monkeypatch)
    _cached_copilot_token(monkeypatch, tmp_path, "")
    monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
    why = importlib.import_module(judge_module("config")).require_ready()
    assert why and "needs a cached token" in why, why


def test_the_copilot_token_never_reaches_a_log(monkeypatch, caplog) -> None:
    """LiteLLM's debug dump masks a request's headers and prints its body whole, and the Copilot arm carries its
    token in the body's `extra_headers`. Measured with a fake token: `adk web -v` logged it whole."""
    import logging

    load_agent(monkeypatch)
    monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
    importlib.import_module(judge_module("config")).build_model()
    with caplog.at_level(logging.DEBUG, logger="LiteLLM"):
        logging.getLogger("LiteLLM").debug("-d '%s'", '{"extra_headers": {"Authorization": "Bearer tid=abc123;exp=9"}}')
    assert "tid=abc123" not in caplog.text and "Bearer [REDACTED]" in caplog.text, caplog.text


def test_the_copilot_login_reads_an_empty_token_file_as_none(tmp_path, monkeypatch, capsys) -> None:
    """The reviewer calls an empty token file no token and says to run the login; found by review, the login then
    answered "already authenticated" and logged no one in."""
    from litellm.llms.github_copilot import authenticator

    from adk_review import login as copilot_login

    _cached_copilot_token(monkeypatch, tmp_path, "")
    monkeypatch.delenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", raising=False)

    def began(_self):  # the device flow's first request: no network in the suite
        raise RuntimeError("the login began")

    monkeypatch.setattr(authenticator.Authenticator, "_get_device_code", began)
    with pytest.raises(RuntimeError, match="the login began"):
        copilot_login.main([])
    assert "already authenticated" not in capsys.readouterr().out


def test_the_copilot_login_names_a_network_it_cannot_reach_and_the_certificate_fix(
    tmp_path, monkeypatch, capsys
) -> None:
    """Measured on a company Windows machine: the proxy re-signs HTTPS with a root certificate Python's own list does
    not hold, and the login's first request to GitHub ended in a traceback. Now a sentence names GitHub as unreachable
    and, for a certificate error, the one variable that fixes it."""
    from litellm.llms.github_copilot import authenticator
    from litellm.llms.github_copilot.common_utils import GetDeviceCodeError

    from adk_review import login as copilot_login

    monkeypatch.setattr(copilot_login, "token_path", lambda: tmp_path / "none")

    def refused(_self):
        raise GetDeviceCodeError(
            status_code=500,
            message="Failed to get device code: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
        )

    monkeypatch.setattr(authenticator.Authenticator, "_get_device_code", refused)
    assert copilot_login.main([]) == 1
    err = capsys.readouterr().err
    assert "GitHub could not be reached" in err and "SSL_CERT_FILE" in err, err


def test_the_copilot_login_caches_no_token_for_an_account_without_copilot(tmp_path, monkeypatch, capsys) -> None:
    """GitHub signs the account in; Copilot serves it no key. Measured before: the token was cached first and the
    exchange raised a traceback after it, so the reviewer read the machine as ready and failed at their first call."""
    from litellm.llms.github_copilot import authenticator
    from litellm.llms.github_copilot.common_utils import GetAPIKeyError

    from adk_review import login as copilot_login

    _cached_copilot_token(monkeypatch, tmp_path, None)
    monkeypatch.delenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", raising=False)
    monkeypatch.setattr(copilot_login.time, "sleep", lambda _s: None)
    code = {"device_code": "d", "user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device"}
    monkeypatch.setattr(authenticator.Authenticator, "_get_device_code", lambda _self: code)

    def no_seat(_self):
        raise GetAPIKeyError(status_code=403, message="Failed to get API key: 403")

    monkeypatch.setattr(authenticator.Authenticator, "get_api_key", no_seat)

    class Approved:  # GitHub's answer once the person has approved the code
        def __init__(self, **_kwargs) -> None: ...

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> bool:
            return False

        def post(self, *_args, **_kwargs):
            return type("Reply", (), {"json": lambda _self: {"access_token": "gho_test"}})()

    import httpx

    monkeypatch.setattr(httpx, "Client", Approved)  # main imports httpx and resolves Client at the call
    assert copilot_login.main([]) == 1
    assert not (tmp_path / "access-token").exists(), "nothing is cached for an account Copilot does not serve"
    assert "no Copilot seat" in capsys.readouterr().err


def test_the_copilot_login_reads_copilots_settings_from_the_central_env(tmp_path, monkeypatch, capsys) -> None:
    """The reviewer reads `.env`; an enterprise tenant names Copilot's endpoints there, so the login reads the same
    `GITHUB_COPILOT_*` settings, a variable already set in the shell winning, and nothing else of the file."""
    import os

    from adk_review import login as copilot_login

    (tmp_path / "tokens").mkdir()
    (tmp_path / "tokens" / "access-token").write_text("t", encoding="utf-8")
    (tmp_path / ".env").write_text(
        f"GITHUB_COPILOT_TOKEN_DIR={tmp_path / 'tokens'}\nREVIEW_PROVIDER=gemini\n", encoding="utf-8"
    )
    monkeypatch.setattr(copilot_login, "ROOT", tmp_path)
    monkeypatch.delenv("GITHUB_COPILOT_TOKEN_DIR", raising=False)
    monkeypatch.delenv("GITHUB_COPILOT_ACCESS_TOKEN_FILE", raising=False)
    monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
    assert copilot_login.main([]) == 0
    assert str(tmp_path / "tokens") in capsys.readouterr().out, "the token dir came from .env"
    assert os.environ["REVIEW_PROVIDER"] == "copilot", "only GITHUB_COPILOT_* is read, and the shell wins"


@pytest.mark.skipif(sys.platform == "win32", reason="a Windows file has no owner/group/other modes to narrow")
def test_the_copilot_login_makes_its_files_private_from_the_first_byte(monkeypatch) -> None:
    """Measured before: LiteLLM wrote the token with the default umask, readable by every account until the login's
    own `chmod` ran a network exchange later. The login sets its umask before anything is written."""
    from adk_review import login as copilot_login

    masks: list[int] = []
    monkeypatch.setattr(copilot_login.os, "umask", lambda mask: masks.append(mask) or 0o022)
    with pytest.raises(SystemExit):
        copilot_login.main(["--help"])
    assert masks == [0o077]


@pytest.mark.skipif(sys.platform == "win32", reason="a Windows file has no owner/group/other modes to narrow")
def test_the_copilot_login_keeps_its_files_to_their_owner(tmp_path) -> None:
    """Measured before: both token files 0644 in a 0755 directory, readable by every account on the machine."""
    from adk_review import login as copilot_login

    (tmp_path / "access-token").write_text("t", encoding="utf-8")
    (tmp_path / "api-key.json").write_text("{}", encoding="utf-8")
    for path in (tmp_path, *tmp_path.iterdir()):
        path.chmod(0o755 if path.is_dir() else 0o644)
    copilot_login.keep_private(tmp_path)
    assert oct(tmp_path.stat().st_mode & 0o777) == "0o700"
    assert {oct(path.stat().st_mode & 0o777) for path in tmp_path.iterdir()} == {"0o600"}


@pytest.mark.parametrize(("arm", "said"), [("gemini", "needs GOOGLE_API_KEY"), ("copilot", "needs a cached token")])
def test_an_arm_that_cannot_run_is_a_sentence(arm: str, said: str, monkeypatch, tmp_path) -> None:
    """Measured by coverage: neither refusal ran in a test. A key missing, or no token cached, is named before
    anything is spent."""
    load_agent(monkeypatch)
    monkeypatch.setenv("REVIEW_PROVIDER", arm)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    _cached_copilot_token(monkeypatch, tmp_path, None)
    why = importlib.import_module(judge_module("config")).require_ready()
    assert why and said in why, why


def test_what_a_push_sends_is_read_from_git(tmp_path) -> None:
    """`push_scope` against a real remote: the commits neither the ref nor any of the remote's branches hold, reviewed
    against the commits they grow from on the remote's side, or as a whole project where the remote holds none of the
    history. Measured by review: an empty first commit, a common start, was reviewed as a project holding no file and
    stopped its push; and of a merge's bases one was reviewed alone, whichever git listed first, so a merge of the
    remote's newer `main` into a branch it held was reviewed with `main`'s own changes, and a clean push stopped.
    `sent` reads each of those commits as it was made, and what it says; `tag_message`, what a pushed tag says."""
    from adk_review.collect.git import GitError
    from adk_review.collect.history import push_scope, sent, tag_messages

    _remote, work = _pushed_from(tmp_path)

    def git(*args: str) -> str:  # with none of the developer's config, an identity of its own
        return _git_in(work, *args)

    git("commit", "-q", "--allow-empty", "-m", "start")
    repo, start = str(work), git("rev-parse", "HEAD")
    assert push_scope(repo, "origin", start, None) is None, "a first commit holding no file: nothing to review"
    first = _committed(work, "a.py")
    assert push_scope(repo, "origin", first, None) == (first, []), "a first push: the remote holds none of it"
    git("push", "-q", "origin", "main")
    assert push_scope(repo, "origin", first, first) is None, "nothing the remote lacks"
    git("checkout", "-q", "-b", "feature")
    own = _committed(work, "f.py")
    git("checkout", "-q", "main")
    _committed(work, "m1.py")
    newer = _committed(work, "m2.py")
    git("push", "-q", "origin", "main")
    git("checkout", "-q", "feature")
    git("merge", "-q", "--no-edit", "main")
    merged = git("rev-parse", "HEAD")
    assert push_scope(repo, "origin", merged, None) == (merged, [newer]), "a base another one holds is none"
    git("push", "-q", "origin", "feature")
    git("checkout", "-q", "main")
    newest = _committed(work, "m3.py")
    git("push", "-q", "origin", "main")
    git("checkout", "-q", "feature")
    _committed(work, "f2.py")
    git("merge", "-q", "--no-edit", "main")
    again = git("rev-parse", "HEAD")
    pushed, bases = push_scope(repo, "origin", again, merged) or ("", [])
    assert pushed == again and sorted(bases) == sorted([merged, newest]), "each base a merge brings, reviewed apart"
    assert push_scope(repo, None, own, first) == (own, [first]), "a range a person named: as named"
    assert push_scope(repo, None, merged, None) == (merged, []), "none named: the whole project"
    for name in ("c" * 40, git("rev-parse", "HEAD^{tree}")):
        with pytest.raises(GitError, match="names no commit in this repository"):
            push_scope(repo, "origin", name, None)

    # Each commit as it was made: what one adds and the next takes out, what each says, a name git quotes.
    git("push", "-q", "origin", "feature")
    git("checkout", "-q", "-b", "undone", "main")
    added = _committed(work, "sp é.py", 'KEY = "x"\nTOKEN = 1\n', "add the key")
    removed = _committed(work, "sp é.py", "TOKEN = 1\n", "take it out\n\nas a review asked")
    git("merge", "-q", "--no-ff", "--no-edit", "feature")
    joined = git("rev-parse", "HEAD")
    said = {commit: (message, files) for commit, message, files in sent(repo, "origin", joined, None)}
    assert set(said) == {joined, removed, added}, "what the remote's branches hold is not sent"
    assert said[added] == ("add the key", [{"path": "sp é.py", "added": [(1, 'KEY = "x"'), (2, "TOKEN = 1")]}])
    assert said[removed] == ("take it out\n\nas a review asked", []), "a commit that only takes lines out adds none"
    assert said[joined][0].startswith("Merge") and said[joined][1] == [], "a merge says what it says, and adds nothing"
    assert {commit for commit, _, _ in sent(repo, None, removed, added)} == {removed}, "a range a person named"
    git("tag", "-a", "v1", "-m", "release notes", joined)
    git("tag", "light", joined)
    assert tag_messages(repo, "v1") == [(git("rev-parse", "v1"), "release notes")]
    assert tag_messages(repo, "light") == tag_messages(repo, joined) == [], "only a tag object says anything"


def test_the_pre_push_hook_reviews_what_a_push_sends_and_stops_it_at_the_bar(
    tmp_path, monkeypatch, capsys, cli
) -> None:
    """One command is git's hook and pre-commit's. Git runs its hook with the remote's name and URL and a line a ref,
    the local side spelt as the push spelt it, spaces and all; pre-commit reads those lines itself and names the ref a
    push sends in its environment, and run by a person, the range as named. Measured before: a line split at every
    space was passed over and its commit went unreviewed; pre-commit's hook, which reviewed the branch checked out,
    stopped every push from `main` and approved a token pushed while a clean branch was checked out; and a variable
    left in a shell outranked git's own lines. Measured by review: arguments a pre-commit config gave the hook were
    read as git's, and a push holding a token passed unreviewed; and a range left in a shell stood in for the ref a
    pre-commit push names. At the bar, a hole or any other exit, the push stops and the report is printed; a clean
    push, or one that sends nothing new, says nothing."""
    import io

    push = importlib.import_module("adk_review.push")
    sha, held, zero = "a" * 40, "b" * 40, "0" * 40
    lines = [f"refs/heads/x {sha} refs/heads/x {held}", f"main^{{/add the key}} {sha} refs/heads/n {zero}", ""]
    expected = [(sha, held, "refs/heads/x"), (sha, None, "refs/heads/n")]
    assert push.refs([*lines, f"refs/heads/d {zero} refs/heads/d {held}"]) == expected
    for space in SPACES:  # in a ref's name, where `str.split` breaks: once read as `main`
        named = f"refs/heads/main{space}x"
        assert push.refs([f"{named} {sha} {named} {zero}"]) == [(sha, None, named)], repr(space)
    with pytest.raises(ValueError, match="names no ref"):
        push.refs(["noise"])
    assert push.command("r", sha, None) == ["r", "--head", sha, "--all", "--profile", "default-rules"]

    remote, work = _pushed_from(tmp_path)

    def git(*args: str) -> str:  # with none of the developer's config, an identity of its own
        return _git_in(work, *args)

    first = _committed(work, "a.py")
    git("push", "-q", "origin", "main")
    second = _committed(work, "b.py")  # on main, never pushed
    git("checkout", "-q", "-b", "feature")
    tip = _committed(work, "c.py")

    ran: list[list[str]] = []
    codes: list[int] = []

    def reviewed(argv: list[str]) -> int:
        ran.append(argv)
        print("## Verdict: REQUEST_CHANGES")
        return codes.pop(0)

    def pushing(*refs: str) -> None:  # git's lines, as bytes in UTF-8 whatever a console's code page
        data = "".join(f"{ref} {ref_sha} {ref} {zero}\n" for ref, ref_sha in zip(refs[::2], refs[1::2], strict=True))
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data.encode()), encoding="cp1252"))

    monkeypatch.chdir(work)
    monkeypatch.setattr(push, "review", reviewed)
    for returned, expected in ((1, 1), (3, 1), (2, 1), (0, 0)):
        codes[:] = [returned]
        pushing("refs/heads/feature", tip)
        assert push.main(["origin", str(remote)]) == expected
    assert ran[0][1:-2] == ["--head", tip, "--base", first, "--profile", "default-rules"]
    assert ran[0][-2] == "--json", "each review says what stopped it, for a merge's reviews to be weighed together"
    said = capsys.readouterr().out
    assert said.count("## Verdict: REQUEST_CHANGES") == 3, "the report is printed where the push stops, only there"
    assert "ended in exit 1; push with --no-verify to skip it" in said

    # Git's own lines decide whatever a shell holds; pre-commit's environment, where pre-commit runs the hook.
    monkeypatch.setenv("PRE_COMMIT_TO_REF", first)
    codes[:] = [0]
    pushing("refs/heads/feature", tip)
    assert push.main(["origin", str(remote)]) == 0 and ran[-1][2] == tip
    # A pre-commit push: the ref it names, and what is new is what the remote's branches lack. A range left in a shell
    # is no part of it: this one would have read as nothing new.
    monkeypatch.setenv("PRE_COMMIT", "1")
    monkeypatch.setenv("PRE_COMMIT_REMOTE_NAME", "origin")
    monkeypatch.setenv("PRE_COMMIT_LOCAL_BRANCH", "refs/heads/feature")
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", tip)
    codes[:] = [0]
    assert push.main([]) == 0 and ran[-1][1:-2] == ["--head", tip, "--base", first, "--profile", "default-rules"]
    # A push to a remote this clone knows no branch of: the whole project is new.
    monkeypatch.setenv("PRE_COMMIT_REMOTE_NAME", "elsewhere")
    codes[:] = [0]
    assert push.main([]) == 0 and ran[-1][1:-2] == ["--head", tip, "--all", "--profile", "default-rules"]
    # Arguments a config gives the hook, where pre-commit passes none of git's lines on: refused, never approved.
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert push.main(["--profile", "rules-only"]) == 1 and "takes none of" in capsys.readouterr().out
    # Run by a person: the range as named, though the remote holds its base; with none named, the whole project.
    monkeypatch.delenv("PRE_COMMIT_LOCAL_BRANCH")
    monkeypatch.delenv("PRE_COMMIT_REMOTE_NAME")
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", first)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", tip)
    codes[:] = [0]
    assert push.main([]) == 0 and ran[-1][1:-2] == ["--head", tip, "--base", first, "--profile", "default-rules"]
    for name in ("PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF"):
        monkeypatch.delenv(name)
    codes[:] = [0]
    assert push.main([]) == 0 and ran[-1][1:-2] == ["--head", tip, "--all", "--profile", "default-rules"]
    monkeypatch.delenv("PRE_COMMIT")

    git("checkout", "-q", "-b", "undo", first)
    _committed(work, "d.py")
    git("revert", "--no-edit", "HEAD")
    ran.clear()
    pushing("refs/heads/undo-Á", git("rev-parse", "HEAD"))  # a branch named in any script
    assert push.main(["origin"]) == 0 and not ran, "commits that change nothing between them: nothing to review"
    pushing("refs/heads/gone", "c" * 40)
    assert push.main(["origin"]) == 1 and "could not be listed" in capsys.readouterr().out, "a hole never approves"
    monkeypatch.setattr(sys, "stdin", io.StringIO("noise\n"))
    assert push.main(["origin"]) == 1 and "names no ref" in capsys.readouterr().out, "nor does a line it cannot read"
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    assert push.main(["origin"]) == 2 and "a pre-push hook" in capsys.readouterr().out, "never a wait at a terminal"
    assert push.main([]) == 2, "neither git nor pre-commit runs it"

    # The review itself, in this process: the token stops the push, and a clean push says nothing.
    git("checkout", "-q", "feature")
    leak = _committed(work, "t.py", 'TOKEN = "ghp_' + "a1B2" * 9 + '"\n')
    monkeypatch.setattr(push, "review", cli.main)
    pushing("refs/heads/feature", leak)
    assert push.main(["origin"]) == 1
    said = capsys.readouterr().out
    assert "rule.secrets" in said and "ended in exit 1" in said
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/main {second} refs/heads/main {first}\n"))
    assert push.main(["origin"]) == 0
    assert capsys.readouterr() == ("", ""), "a clean push says nothing"
    monkeypatch.setattr(push, "settle", lambda: None)
    monkeypatch.setattr(push, "main", lambda: 1)
    assert push.run() == 1, "the installed hook ends as its main does"


def test_the_pre_push_hook_reads_each_commit_a_push_sends_for_credentials(tmp_path, monkeypatch, capsys, cli) -> None:
    """The change as a whole is reviewed, and each commit it is made of is read by the credential rules, what it says
    too: the history the remote receives holds what one commit adds and a later one takes out. Measured by review: a
    token added and taken out within one push, a token in a commit's message and one in a tag's each reached the
    remote with the hook passing; and a merge brought a teammate's token, already on the remote, into the review
    against the wrong base, and a clean push stopped, merged into a branch the remote held or into `main`."""
    import io

    push = importlib.import_module("adk_review.push")
    (remote, work), mate = _pushed_from(tmp_path), tmp_path / "mate"
    token = "ghp_" + "a1B2" * 9

    def pushed(ref: str, held: str | None = None, name: str = "refs/heads/x") -> int:  # git's line, then the hook
        local = _git_in(work, "rev-parse", ref)
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"{name} {local} {name} {held or '0' * 40}\n"))
        return push.main(["origin", str(remote)])

    first = _committed(work, "a.py")
    _git_in(work, "push", "-q", "origin", "main")
    monkeypatch.chdir(work)
    monkeypatch.setattr(push, "review", cli.main)

    _git_in(work, "checkout", "-q", "-b", "undone")
    added = _committed(work, "t.py", f'TOKEN = "{token}"\n', "add a token")
    _committed(work, "t.py", "x = 2\n", "take it out")
    assert pushed("undone") == 1, "the change is clean; its history is not"
    said = capsys.readouterr().out
    assert f"{added[:9]}, t.py:1: a hard-coded credential (rule.secrets)" in said and "git rebase -i" in said
    _git_in(work, "checkout", "-q", "-b", "said", "main")
    told = _committed(work, "s.py", "x = 3\n", f"deploy with {token}")
    assert pushed("said") == 1 and f"{told[:9]}, its message, line 1" in capsys.readouterr().out
    _git_in(work, "tag", "-a", "v1", "-m", f"notes\n\n{token}", first)
    tag = _git_in(work, "rev-parse", "v1")
    assert pushed("v1", name="refs/tags/v1") == 1, "a tag's words are in no commit"
    assert f"{tag[:9]}, its message, line 3" in capsys.readouterr().out

    # A teammate's token reaches `main` from a clone with no hook, and is merged on: the push sends none of it.
    _git_in(work, "checkout", "-q", "-b", "feature", "main")
    _committed(work, "f.py")
    _git_in(work, "push", "-q", "origin", "feature")
    _git_in(tmp_path, "clone", "-q", str(remote), str(mate))
    _committed(mate, "old.py", f'TOKEN = "{token}"\n', "a teammate's token")
    _git_in(mate, "push", "-q", "origin", "main")
    _git_in(work, "fetch", "-q", "origin")
    _committed(work, "f2.py")  # a commit before the merge: git listed the branch's old tip first
    _git_in(work, "merge", "-q", "--no-edit", "origin/main")
    assert pushed("feature", _git_in(work, "rev-parse", "origin/feature")) == 0, "merged into a branch it held"
    _git_in(work, "checkout", "-q", "-b", "f3", "origin/feature")
    _committed(work, "f3.py")
    _git_in(work, "push", "-q", "origin", "f3")
    _git_in(work, "checkout", "-q", "-B", "main", "origin/main")
    _git_in(work, "merge", "-q", "--no-ff", "--no-edit", "f3")
    assert pushed("main", _git_in(work, "rev-parse", "origin/main")) == 0, "a branch it held, merged into main"
    assert capsys.readouterr() == ("", "")


def test_a_binary_file_one_commit_adds_and_a_later_takes_out_stops_the_push(tmp_path) -> None:
    """Measured by review: a key store one commit added and the next took out passed the hook unnamed, where the same
    file in the change was a hole. An image the skip list names passes, as it does in a review of the change."""
    push = importlib.import_module("adk_review.push")
    _remote, work = _pushed_from(tmp_path)
    _committed(work, "a.py")
    _git_in(work, "push", "-q", "origin", "main")
    _git_in(work, "checkout", "-q", "-b", "undone")
    stored = _committed(work, "prod.p12", b"\x00\x01keystore\x00" * 64, "add a key store")
    _committed(work, "logo.png", b"\x89PNG\x00" * 64, "add a logo")
    _git_in(work, "rm", "-q", "prod.p12", "logo.png")
    _git_in(work, "commit", "-qm", "take them out")
    found = push.in_history(str(work), "origin", "HEAD", None, [])
    assert found == [f"{push._named(stored)}, prod.p12: a binary file, which no rule can read"], found


def test_a_push_the_clone_misreads_the_remote_for_stops_and_says_to_fetch(tmp_path, monkeypatch, capsys) -> None:
    """Read by review: what a remote holds was read off this clone's last fetch of it, so after `git remote set-url` to
    another server, or with a `pushurl` naming one, the old server's branches counted as held, and a token one commit
    added and the next took out reached the new server unread. Where git says the remote holds no branch this clone
    saw there, its picture of the remote is wrong, and the push stops, saying to fetch it with `--prune` first."""
    import io

    push = importlib.import_module("adk_review.push")
    (old, work), token = _pushed_from(tmp_path), "ghp_" + "a1B2" * 9
    _committed(work, "settings.py", f'TOKEN = "{token}"\n', "add settings")
    _committed(work, "settings.py", "TOKEN = None\n", "take the token out")
    _git_in(work, "push", "-q", "origin", "main")  # from a clone with no hook: the old server holds it all
    new = tmp_path / "new.git"
    _git_in(tmp_path, "init", "-q", "--bare", "-b", "main", str(new))
    monkeypatch.chdir(work)
    local = _git_in(work, "rev-parse", "main")
    for moved in (("remote", "set-url", "origin", str(new)), ("config", "remote.origin.pushurl", str(new))):
        _git_in(work, "remote", "set-url", "origin", str(old))
        _git_in(work, *moved)
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/main {local} refs/heads/main {'0' * 40}\n"))
        assert push.main(["origin", str(new)]) == 1, moved
        said = capsys.readouterr().out
        assert "git fetch --prune origin" in said and "main" in said, said


def test_a_large_file_stores_pointer_one_commit_adds_and_a_later_takes_out_stops_the_push(tmp_path) -> None:
    """Read by review: a key store tracked by Git LFS, one commit adding its pointer and the next taking it out, passed
    the hook unnamed, and Git LFS's own hook uploaded the stored file it names; a binary file so added was a hole."""
    push = importlib.import_module("adk_review.push")
    _remote, work = _pushed_from(tmp_path)
    _committed(work, "a.py")
    _git_in(work, "push", "-q", "origin", "main")
    _git_in(work, "checkout", "-q", "-b", "undone")
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 9\n"
    stored = _committed(work, "prod.p12", pointer, "add a key store")
    _git_in(work, "rm", "-q", "prod.p12")
    _git_in(work, "commit", "-qm", "take it out")
    found = push.in_history(str(work), "origin", "HEAD", None, [])
    assert found == [f"{push._named(stored)}, prod.p12: a Git LFS pointer, whose file no rule can read"], found


def test_what_a_tag_of_a_tag_and_a_merged_signed_tag_say_is_read(tmp_path, monkeypatch, capsys, cli) -> None:
    """Read by review: a tag made of another tag sends both, and only the outer one's message was read, the inner's
    token reaching the remote; and a merge of a signed tag carries the tag's message in its own header, `mergetag`,
    which was not read, a token in it reaching the remote with the tag itself never pushed. Each tag a chain names is
    read, and each tag a merge carries."""
    import io

    push = importlib.import_module("adk_review.push")
    (remote, work), token = _pushed_from(tmp_path), "ghp_" + "a1B2" * 9
    first = _committed(work, "a.py")
    _git_in(work, "push", "-q", "origin", "main")
    monkeypatch.chdir(work)
    monkeypatch.setattr(push, "review", cli.main)

    def pushed(ref: str, held: str | None, name: str) -> int:
        local = _git_in(work, "rev-parse", ref)
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"{name} {local} {name} {held or '0' * 40}\n"))
        return push.main(["origin", str(remote)])

    _git_in(work, "tag", "-a", "v1.0-rc1", "-m", f"rc1: deploy with {token}", first)
    inner = _git_in(work, "rev-parse", "v1.0-rc1")
    _git_in(work, "tag", "-a", "v1.0", "v1.0-rc1", "-m", "release 1.0")
    assert pushed("v1.0", None, "refs/tags/v1.0") == 1
    assert f"{inner[:9]}, its message, line 1" in capsys.readouterr().out

    _git_in(work, "checkout", "-q", "-b", "side")
    side = _committed(work, "s.py", "x = 2\n", "a side")
    _git_in(work, "checkout", "-q", "main")
    signed = f"object {side}\ntype commit\ntag v1-rc\ntagger t <t@t> 1700000000 +0000\n\ndeploy with {token}\n"
    signed += "-----BEGIN PGP SIGNATURE-----\nabc\n-----END PGP SIGNATURE-----\n"
    _git_in(work, "update-ref", "refs/tags/v1-rc", _git_in(work, "mktag", stdin=signed))
    _git_in(work, "merge", "-q", "--no-ff", "-m", "Merge the release", "v1-rc")
    _git_in(work, "tag", "-d", "v1-rc")
    merge = _git_in(work, "rev-parse", "HEAD")
    assert pushed("main", first, "refs/heads/main") == 1
    assert f"{merge[:9]}, the tag it merges, line " in capsys.readouterr().out


def test_a_merge_of_what_the_remote_holds_stops_only_on_what_is_new_to_it(tmp_path, monkeypatch, capsys, cli) -> None:
    """Read by review: a merge was reviewed against each base it grows from, and passed when one review did, so a
    merge of two branches the remote held, each with a file no rule can read, stopped though it added nothing: each
    review read the other side's own file. What stops a merge is what every base's review found."""
    import io

    push = importlib.import_module("adk_review.push")
    remote, work = _pushed_from(tmp_path)
    _committed(work, "a.py")
    _git_in(work, "push", "-q", "origin", "main")
    _git_in(work, "checkout", "-q", "-b", "release")
    _committed(work, "notes.pdf", b"%PDF\x00" * 64, "release notes")
    _git_in(work, "push", "-q", "origin", "release")
    _git_in(work, "checkout", "-q", "main")
    _committed(work, "manual.pdf", b"%PDF\x00" * 64, "a manual")
    _git_in(work, "push", "-q", "origin", "main")  # from a clone with no hook: the remote holds both sides
    monkeypatch.chdir(work)
    monkeypatch.setattr(push, "review", cli.main)

    def pushed() -> int:
        local, held = _git_in(work, "rev-parse", "main"), _git_in(work, "rev-parse", "origin/main")
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/main {local} refs/heads/main {held}\n"))
        return push.main(["origin", str(remote)])

    _git_in(work, "merge", "-q", "--no-edit", "release")
    assert pushed() == 0, capsys.readouterr().out
    _git_in(work, "reset", "-q", "--hard", "origin/main")
    _git_in(work, "merge", "-q", "--no-commit", "release")
    (work / "deck.pdf").write_bytes(b"%PDF\x00" * 64)  # new in the merge itself, to every base
    _git_in(work, "add", "deck.pdf")
    _git_in(work, "commit", "-qm", "merge, with a deck")
    assert pushed() == 1 and "deck.pdf" in capsys.readouterr().out


def test_a_push_to_this_repository_itself_sends_nothing_off_the_machine(tmp_path, monkeypatch, capsys) -> None:
    """Read by review: `git push . origin/main:main`, which moves a branch here and sends nothing anywhere, was reviewed
    as a push to a remote holding nothing, and stopped on a teammate's file."""
    import io

    push = importlib.import_module("adk_review.push")
    _remote, work = _pushed_from(tmp_path)
    local = _committed(work, "manual.pdf", b"%PDF\x00" * 64, "a manual")
    monkeypatch.chdir(work)
    monkeypatch.setattr(push, "review", lambda argv: pytest.fail(f"reviewed {argv}"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/main {local} refs/heads/copy {'0' * 40}\n"))
    assert push.main([".", "."]) == 0 and capsys.readouterr() == ("", "")


@pytest.mark.skipif(os.name == "nt", reason="the recipe is a POSIX shell's, run by Git Bash there")
def test_the_install_recipe_keeps_the_hook_it_finds_and_hands_both_gits_lines(tmp_path) -> None:
    """Read by review: the recipe in the guide replaced any pre-push hook already there, Git LFS's among them, so a
    pushed image's file was never uploaded and a fresh clone could not fetch it. The recipe keeps the hook it finds
    and runs it after the review passes, each given git's own lines."""
    import re
    import shutil

    guide = ROOT.joinpath("GUIDE.md").read_text(encoding="utf-8")
    recipe = re.search(r"\n\n((?:    [^\n]*\n|\n)+)", guide[guide.index("To install it, run these lines") :])
    assert recipe, "the guide's recipe"
    script = "\n".join(line.removeprefix("    ") for line in recipe.group(1).strip("\n").splitlines())
    work, bin_ = tmp_path / "work", tmp_path / "bin"
    work.mkdir()
    bin_.mkdir()
    _git_in(work, "init", "-q", "-b", "main")
    hooks = work / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-push").write_text(f'#!/bin/sh\ncat > "{tmp_path}/before.in"\necho "$@" > "{tmp_path}/before.args"\n')
    (hooks / "pre-push").chmod(0o755)
    reviewed = bin_ / "adk-review-push"
    reviewed.write_text(f'#!/bin/sh\ncat > "{tmp_path}/review.in"\nexit "${{REVIEW_EXIT:-0}}"\n')
    reviewed.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(["sh", "-c", script], cwd=work, env=env, check=True)
    lines = f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n"
    hook = [str(hooks / "pre-push"), "origin", "https://example.com/r.git"]
    done = subprocess.run(hook, cwd=work, env=env, input=lines, text=True, check=False)
    assert done.returncode == 0
    assert (tmp_path / "review.in").read_text() == lines == (tmp_path / "before.in").read_text()
    assert (tmp_path / "before.args").read_text().split() == hook[1:]
    (tmp_path / "before.in").unlink()
    stopped = subprocess.run(hook, cwd=work, env={**env, "REVIEW_EXIT": "1"}, input=lines, text=True, check=False)
    assert stopped.returncode == 1 and not (tmp_path / "before.in").exists(), "a stopped push runs nothing after"
    assert shutil.which("sh", path=env["PATH"]), "a shell to run the recipe in"


def test_the_history_a_push_sends_is_read_with_the_push_history_profile(tmp_path, monkeypatch) -> None:
    """`push-history` in `config.toml` names the rules each commit a push sends is read with: a profile like any other,
    where a project's own credential rule joins it, never ids written into the hook. A commit is read line by line, by
    code alone: a profile naming a file rule or a guideline is refused, so the push waits, never read short."""
    push = importlib.import_module("adk_review.push")
    assert [rule.check_id for rule in push.history_rules()] == ["rule.secrets", "rule.named-secrets"]
    for named in (["rule.secrets", "rule.lint"], ["guideline.security"]):
        _with_profiles(push, monkeypatch, {**profiles_of(), push.HISTORY: named})
        with pytest.raises(ValueError, match=f"push-history names {named[-1]}: a commit is read line by line"):
            push.in_history(str(tmp_path), "origin", "HEAD", None, [])  # no repository: refused before git is asked


def test_a_push_from_a_shallow_clone_is_reviewed(tmp_path, monkeypatch, capsys, cli) -> None:
    """Measured by review: every push from a shallow clone was stopped, a review there refusing any merge base. The
    base of a push is one of the head's own commits, and so the merge base, whatever lies past the cut; a base that is
    not stays refused, as a merge base found in such a clone can be the wrong one."""
    import io

    from adk_review.collect.git import preflight

    push = importlib.import_module("adk_review.push")
    remote, work = _pushed_from(tmp_path)
    for name in ("a.py", "b.py", "c.py"):
        _committed(work, name)
    _git_in(work, "push", "-q", _file_url(remote), "main")
    _git_in(tmp_path, "clone", "-q", "--depth", "1", _file_url(remote), "shallow")
    shallow = tmp_path / "shallow"
    assert _git_in(shallow, "rev-parse", "--is-shallow-repository") == "true", "the clone is whole, this proves nothing"
    held = _git_in(shallow, "rev-parse", "HEAD")
    monkeypatch.chdir(shallow)
    monkeypatch.setattr(push, "review", cli.main)
    for name, text, expected in (("d.py", "x = 2\n", 0), ("t.py", 'TOKEN = "ghp_' + "a1B2" * 9 + '"\n', 1)):
        local = _committed(shallow, name, text)
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/main {local} refs/heads/main {held}\n"))
        assert push.main(["origin", _file_url(remote)]) == expected, capsys.readouterr().out
    assert "rule.secrets" in capsys.readouterr().out
    _git_in(shallow, "checkout", "-q", "-b", "other", held)
    _committed(shallow, "o.py", "x = 3\n", "other")
    refused = preflight(str(shallow), "main", "other")
    assert refused["status"] == "error" and "shallow clone" in refused["error_message"], "not one of its commits"


def test_the_reviewer_installs_as_one_command_that_runs_from_any_folder() -> None:
    """`uv build` failed on the flat layout, and the command needed the clone and the right folder. The wheel holds
    the reviewer alone, its config beside it; `adk-review` runs `run`, the process boundary `python -m` runs too, and
    `adk-review-push`, the pre-push hook pre-commit's hook runs as well, its own, and `adk-review-login`, the Copilot
    arm's one-time login, which a script of the checkout once was; and the lint rule finds the ruff
    installed beside the reviewer's interpreter, whose folder no installed tool puts on PATH. Tried end to end: built,
    installed with `uv tool install`, and run from outside the clone."""
    import tomllib

    from adk_review import review
    from adk_review.collect import ruff

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"] == {
        "adk-review": "adk_review.review:run",
        "adk-review-push": "adk_review.push:run",
        "adk-review-login": "adk_review.login:run",
    }
    hooks = (ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
    assert "\n  entry: adk-review-push\n" in hooks and "\n  stages: [pre-push]\n" in hooks
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["adk_review"]
    assert any(need.startswith("ruff") for need in project["project"]["dependencies"]), "lint needs ruff installed"
    assert callable(review.run) and review.version() == project["project"]["version"]
    found = ruff.beside()
    assert found is not None and Path(found).parent == Path(sys.executable).parent

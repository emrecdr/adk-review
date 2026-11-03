"""The engine around the checks, through the real one with the fake model: retries on both arms, the token ceiling, the
second opinion, the fences every judging agent reads in, the state keys and the checks node."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import litellm
import pytest
from conftest import (
    BASE,
    CANNED_REVIEW,
    GUIDELINE_NAMES,
    HEAD,
    REFUTED,
    SECURITY,
    _branch_off_empty,
    _cached_copilot_token,
    _committed,
    _entry,
    _git_in,
    branched,
    catalog,
    load_agent,
    load_command,
    model_check,
    model_key,
    quota,
    run_review,
)

from adk_review.core.profiles import rule_id, state_key
from adk_review.core.text import TITLE_MAX_CHARS, cut_at

#: The heading only a verifier's instruction carries: a rule on it reaches the verifiers and no guideline.
THE_VERIFIER = "## The finding"


#: Text only a guideline's instruction carries.
THE_GUIDELINES = "only in your dimension"


#: A guideline's reading of S602's line more severe than the rule's: no restatement, so the verifier is asked.
SAYS_MORE = {**CANNED_REVIEW, "findings": [{**CANNED_REVIEW["findings"][0], "severity": "blocker"}]}


def _said_done(said: str) -> bool:
    """Whether any guideline was said done, by its own line: `rules done in` is another path's."""
    return any(f"{model_check(name)} done in" in said for name in GUIDELINE_NAMES)


class TestRetriesOnBothArms:
    """Retries ride on the request on Gemini and in the OpenAI SDK on Copilot, one constant for both; the live proof
    is a 429."""

    @pytest.mark.parametrize("arm", ["gemini", "copilot"])
    def test_every_judging_request_carries_its_arms_retries_beside_its_deadline(self, arm: str, monkeypatch) -> None:
        """Copilot's request asks for none: the OpenAI SDK under LiteLLM retries twice on its own, backing off and
        waiting out the server's Retry-After. Measured against a server that never answered, three attempts asked became
        seven; and against a local server answering 429, asking for one added LiteLLM's own retry twenty milliseconds
        after the SDK's last, its Retry-After unread, where asking for none spaced the same three requests."""
        from adk_review.judge.config import REQUEST_TIMEOUT_S, RETRY_ATTEMPTS, request_config

        monkeypatch.setenv("REVIEW_PROVIDER", arm)
        options = request_config().http_options
        if arm == "gemini":
            assert options.retry_options is not None and options.retry_options.attempts == RETRY_ATTEMPTS
        else:
            assert options.retry_options is None, "the SDK's own retries, spaced, and nothing around them"
        assert options.timeout == REQUEST_TIMEOUT_S * 1000

    @pytest.mark.parametrize("vertex", [False, True])
    def test_gemini_retries_a_rate_limit_only_where_its_quota_is_shared(self, vertex: bool, monkeypatch) -> None:
        """Measured offline: genai retried AI Studio's 429, which names a wait of most of a minute or a day's quota
        spent, after 1.3 s and 2.7 s, each retry a request more against the quota and none able to pass it. On AI
        Studio a 429 is not retried, and the review stops its spending at once; on Vertex AI, whose shared quota throws
        429s that pass, it is; a timeout and each 5xx are retried on both."""
        from adk_review.judge.config import request_config

        monkeypatch.setenv("REVIEW_PROVIDER", "gemini")
        monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "TRUE" if vertex else "FALSE")
        retries = request_config().http_options.retry_options
        assert retries is not None and retries.http_status_codes is not None
        assert (429 in retries.http_status_codes) == vertex, retries.http_status_codes
        assert {408, 500, 502, 503, 504} <= set(retries.http_status_codes)

    def test_copilot_is_sent_the_instruction_as_its_system_turn(self, monkeypatch) -> None:
        """Measured by review: LiteLLM sends Copilot each system message as an assistant turn unless told not to, so
        the fences around the branch's text, and the reminder after them, reached Copilot as words the model had said
        itself, where Gemini reads them as its instruction. LiteLLM's own note says Copilot now takes a system turn
        for every model it serves; the Copilot arm sends one."""
        from litellm.llms.github_copilot.chat.transformation import GithubCopilotConfig

        monkeypatch.setattr(litellm, "disable_copilot_system_to_assistant", False)  # LiteLLM's default, put back after
        load_agent(monkeypatch, "copilot", "gpt-4.1")
        importlib.import_module("adk_review.judge.config").build_model()
        asked = [{"role": "system", "content": "the instruction"}, {"role": "user", "content": "the change"}]
        sent = GithubCopilotConfig()._transform_messages(asked, "gpt-4.1")
        assert [message["role"] for message in sent] == ["system", "user"], sent

    def test_a_copilot_answer_in_time_is_never_lost_to_the_time_adk_takes_with_it(self, monkeypatch) -> None:
        """Read by review: the deadline was held across the answer's yield, so the time ADK spent with an answer that
        came in time, appending it to the session or running chat's intake's tool, counted against it, and where it ran
        out there ADK's own await was cancelled: the answer lost, the guideline named as one that produced no output,
        and no timeout for a fallback to move on from. The deadline holds the call alone."""
        import asyncio

        from google.adk.models.lite_llm import LiteLlm

        config = importlib.import_module("adk_review.judge.config")
        monkeypatch.setattr(config, "REQUEST_TIMEOUT_S", 0.2)

        async def answered(_self, _llm_request, _stream=False):
            yield "the answer"

        monkeypatch.setattr(LiteLlm, "generate_content_async", answered)
        model = config._bounded()(model="github_copilot/gpt-4.1")

        async def consumed() -> list:
            got = []
            async for response in model.generate_content_async(None):
                got.append(response)
                await asyncio.sleep(0.4)  # ADK's own work with the answer, past the deadline
            return got

        assert asyncio.run(consumed()) == ["the answer"]

    def test_a_copilot_call_that_hangs_ends_at_one_deadline_and_its_fallback_answers(
        self, demo_repo: Path, monkeypatch, tmp_path: Path, capsys
    ) -> None:
        """Measured offline against a server that never answered: each call was sent three times, each waiting out
        the 180 s deadline, nine minutes, and the fallback model was never asked, a timeout being no status ADK's
        fallback reads. One deadline holds a call and every retry the client makes of it now, and past it the call is
        a timeout, 408, which the fallback moves on from: here each guideline is asked of the primary once and of the
        fallback once, and the review decides in seconds."""
        import asyncio

        from google.adk.models.lite_llm import LiteLLMClient

        asked: list[str] = []

        async def answer(_client, model, **_request):
            asked.append(model)
            if model == "github_copilot/gpt-4.1":
                await asyncio.sleep(60)  # the primary never answers
            return litellm.ModelResponse(
                choices=[{"message": {"role": "assistant", "content": '{"findings": []}'}}],
                usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            )

        monkeypatch.setattr(LiteLLMClient, "acompletion", answer)
        _cached_copilot_token(monkeypatch, tmp_path)
        monkeypatch.setenv("REVIEW_FALLBACK_MODEL", "gpt-4o")
        cli, data = load_command(monkeypatch, "copilot", "gpt-4.1"), tmp_path / "review.json"
        monkeypatch.setattr(importlib.import_module("adk_review.judge.config"), "REQUEST_TIMEOUT_S", 0.5)
        started = time.perf_counter()
        assert run_review(cli, demo_repo, "--json", str(data)) == 1, capsys.readouterr().out
        assert time.perf_counter() - started < 20, "a deadline a call, not one an attempt"
        report, calls = json.loads(data.read_text(encoding="utf-8")), len(GUIDELINE_NAMES)
        assert sorted(asked) == ["github_copilot/gpt-4.1"] * calls + ["github_copilot/gpt-4o"] * calls, asked
        assert report["could_not_look"] == {}, "each guideline answered by the fallback"

    def test_the_planted_review_runs_end_to_end_on_the_copilot_arm(
        self, demo_repo: Path, monkeypatch, tmp_path: Path, capsys
    ) -> None:
        """Measured by review: every offline test of the engine ran on the Gemini arm, where the fake registers, so
        the Copilot arm's own path — ADK's LiteLLM wrapper, the schema it asks for, the reply read back and validated,
        the usage counted — ran only live. Only the network client is replaced here: each guideline answers no
        findings, through the whole path, and the review decides on the rules, naming the arm and what it spent."""
        from google.adk.models.lite_llm import LiteLLMClient

        asked: list[str] = []

        async def answer(_client, model, **_request):
            asked.append(model)
            return litellm.ModelResponse(
                choices=[{"message": {"role": "assistant", "content": '{"findings": []}'}}],
                usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            )

        monkeypatch.setattr(LiteLLMClient, "acompletion", answer)
        _cached_copilot_token(monkeypatch, tmp_path)
        cli, data = load_command(monkeypatch, "copilot", "gpt-4.1"), tmp_path / "review.json"
        assert run_review(cli, demo_repo, "--json", str(data)) == 1, capsys.readouterr().out
        report, calls = json.loads(data.read_text(encoding="utf-8")), len(GUIDELINE_NAMES)
        assert report["model"] == "gpt-4.1 on copilot" and asked == ["github_copilot/gpt-4.1"] * calls, (report, asked)
        assert (report["usage"]["calls"], report["usage"]["tokens"]) == (calls, 120 * calls), report["usage"]
        assert report["could_not_look"] == {}, "each guideline answered, its reply valid"

    @pytest.mark.asyncio
    async def test_adk_hands_litellm_the_sdks_retries_the_deadline_and_no_retry_around_them(self, monkeypatch) -> None:
        """The Copilot arm cannot run offline. This drives ADK's own LiteLLM wrapper, the model built as a review builds
        it, with only the network client replaced, so what LiteLLM would be called with is what is asserted: the SDK's
        own retries, `RETRY_ATTEMPTS` in all, where LiteLLM's default of two made them before; no `num_retries`, which
        would retry around them; the deadline in seconds; the temperature."""
        from google.adk.models.lite_llm import LiteLLMClient
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        from adk_review.judge.config import REQUEST_TIMEOUT_S, RETRY_ATTEMPTS, build_model, request_config

        monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
        monkeypatch.setenv("REVIEW_MODEL", "gpt-4.1")
        called: dict = {}

        class Capture(LiteLLMClient):
            async def acompletion(self, **kwargs):
                called.update(kwargs)
                return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": "ok"}}])

        model = build_model()
        model.llm_client = Capture()
        request = LlmRequest(model=model.model, contents=[types.UserContent("judge")], config=request_config())
        async for _response in model.generate_content_async(request):
            pass
        assert called["max_retries"] == RETRY_ATTEMPTS - 1, "the SDK's own retries, from the reviewer's constant"
        assert called.get("num_retries") is None and called["timeout"] == REQUEST_TIMEOUT_S
        assert called["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_a_copilot_call_carrying_a_tool_reaches_copilot(self, monkeypatch, tmp_path: Path) -> None:
        """Measured offline: from LiteLLM 1.106.0, a call carrying a tool imported LiteLLM's MCP gateway, which needs
        `mcp`, a package of LiteLLM's proxy the reviewer does not install, so chat's intake, which confirms a scope with
        its tools, failed on the Copilot arm before anything was sent. The tests above replace ADK's client and run none
        of LiteLLM's own code; here only the network is replaced, below LiteLLM."""
        import httpx
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        from adk_review.judge.config import build_model, request_config

        key = {"token": "a key", "expires_at": time.time() + 3600, "endpoints": {"api": "https://copilot.invalid"}}
        (tmp_path / "api-key.json").write_text(json.dumps(key), encoding="utf-8")  # LiteLLM asks GitHub for none
        _cached_copilot_token(monkeypatch, tmp_path)
        monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
        monkeypatch.setenv("REVIEW_MODEL", "gpt-4.1")
        sent: list[dict] = []

        async def send(_client, request, *_args, **_kwargs):
            sent.append(json.loads(request.content))
            choice = {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
            reply = {"id": "1", "object": "chat.completion", "created": 1, "model": "gpt-4.1", "choices": [choice]}
            return httpx.Response(200, json=reply, request=request)

        monkeypatch.setattr(httpx.AsyncClient, "send", send)
        config = request_config()
        config.tools = [types.Tool(function_declarations=[types.FunctionDeclaration(name="changed_files")])]
        model = build_model()
        request = LlmRequest(model=model.model, contents=[types.UserContent("review main")], config=config)
        answered = [response async for response in model.generate_content_async(request)]
        assert [body["tools"][0]["function"]["name"] for body in sent] == ["changed_files"], answered
        assert answered[-1].content.parts[0].text == "ok", answered

    @pytest.mark.asyncio
    async def test_a_copilot_rate_limit_says_when_to_try_again_where_copilot_names_it(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Asked by the owner: a 429 said only "Sorry, you've exceeded your rate limit", nothing of when it clears.
        Measured offline: LiteLLM keeps Copilot's headers on its error as `litellm_response_headers`, and its
        `response` holds none. Only the network is replaced, below LiteLLM."""
        import httpx
        import litellm
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        from adk_review.judge.config import build_model, request_config
        from adk_review.judge.plugins import describe

        key = {"token": "a key", "expires_at": time.time() + 3600, "endpoints": {"api": "https://copilot.invalid"}}
        (tmp_path / "api-key.json").write_text(json.dumps(key), encoding="utf-8")
        _cached_copilot_token(monkeypatch, tmp_path)
        monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
        monkeypatch.setenv("REVIEW_MODEL", "gpt-4.1")

        async def send(_client, request, *_args, **_kwargs):
            said = "Sorry, you've exceeded your rate limit for utility models."
            return httpx.Response(429, headers={"retry-after": "1800"}, text=said, request=request)

        monkeypatch.setattr(httpx.AsyncClient, "send", send)
        model = build_model()
        request = LlmRequest(model=model.model, contents=[types.UserContent("hi")], config=request_config())
        with pytest.raises(litellm.RateLimitError) as raised:
            async for _ in model.generate_content_async(request):
                pass
        said = describe(raised.value)
        sorry = r"429: Sorry, you've exceeded your rate limit for utility models\."
        assert re.fullmatch(sorry + r" Try again after 30 min, at \d\d:\d\d\.", said), said

    @pytest.mark.asyncio
    async def test_a_model_copilot_does_not_serve_says_to_name_another(self, monkeypatch, tmp_path: Path) -> None:
        """Measured live on Copilot: `gemini-3.8-flash`, which GitHub's pricing lists, was refused for the seat with
        `model_not_supported`, each guideline a hole, and the report said only to check each by hand. GitHub has
        deprecated `gpt-4.1`, the arm's default, so a review meets this the day Copilot stops serving it. Only the
        network is replaced, below LiteLLM."""
        import httpx
        import litellm
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        from adk_review.judge.config import build_model, request_config
        from adk_review.judge.plugins import describe

        key = {"token": "a key", "expires_at": time.time() + 3600, "endpoints": {"api": "https://copilot.invalid"}}
        (tmp_path / "api-key.json").write_text(json.dumps(key), encoding="utf-8")
        _cached_copilot_token(monkeypatch, tmp_path)
        monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
        monkeypatch.setenv("REVIEW_MODEL", "gemini-3.8-flash")

        async def send(_client, request, *_args, **_kwargs):
            refused = {"message": "The requested model is not supported.", "code": "model_not_supported"}
            body = {"error": {**refused, "param": "model", "type": "invalid_request_error"}}
            return httpx.Response(400, json=body, request=request)

        monkeypatch.setattr(httpx.AsyncClient, "send", send)
        model = build_model()
        request = LlmRequest(model=model.model, contents=[types.UserContent("hi")], config=request_config())
        with pytest.raises(litellm.BadRequestError) as raised:
            async for _ in model.generate_content_async(request):
                pass
        assert describe(raised.value) == (
            "model_not_supported: The requested model is not supported. "
            "Name a model your Copilot seat serves, in REVIEW_MODEL or the variable that chose this one."
        )

    def test_a_rate_limit_names_its_wait_from_either_header_and_nothing_without_one(self) -> None:
        """`retry-after` in seconds or as an HTTP date, or GitHub's `x-ratelimit-reset`, the second it clears; a time
        already past, or none, adds nothing, never a wait the provider did not name."""
        from email.utils import formatdate

        import litellm

        from adk_review.judge.plugins import describe

        def limited(headers: dict[str, str]) -> str:
            error = litellm.RateLimitError("Sorry, you've exceeded your rate limit.", "github_copilot", "gpt-4.1")
            error.litellm_response_headers = headers
            return describe(error)

        assert "Try again after 45 s, at " in limited({"Retry-After": "45"}), "a header's name in any case"
        assert "Try again after 46 s, at " in limited({"Retry-After": "45.2"}), "float seconds in retry-after"
        assert "Try again after 2 h, at " in limited({"x-ratelimit-reset": str(int(time.time()) + 7200)})
        assert "Try again after 1 h, at " in limited({"x-ratelimit-user-reset": str(time.time() + 3600)})
        assert "Try again after 2 min, at " in limited({"retry-after": formatdate(time.time() + 90, usegmt=True)})
        # Read by review: `float` takes "nan", "inf" and an exponent, and the wait past a date's range raised inside
        # the error's own callback, so a 429 read as a crash. None adds a wait, and none raises.
        hostile = ({"retry-after": value} for value in ("nan", "inf", "1e400", "1e12", "99999999999999", "-30"))
        reset = {"retry-after": "0", "x-ratelimit-reset": str(time.time() + 3600)}
        assert "Try again after 1 h, at " in limited(reset), "read by review: a retry-after of 0 hid the reset header"
        for none in ({}, {"x-ratelimit-reset": str(int(time.time()) - 60)}, {"retry-after": "soon"}, *hostile):
            assert "Try again" not in limited(none), none

    def test_a_retry_after_date_a_system_cannot_place_names_no_wait_and_raises_nothing(self, monkeypatch) -> None:
        """Read by review: Python raises `OSError` where `timestamp()` places a date with no zone before 1970 on
        Windows, and it escaped the error's own callback, so a 429 read as a crash."""
        import litellm

        from adk_review.judge import plugins

        class Before1970:
            def timestamp(self) -> float:
                raise OSError(22, "Invalid argument")

        monkeypatch.setattr(plugins, "parsedate_to_datetime", lambda _said: Before1970())
        error = litellm.RateLimitError("Sorry, you've exceeded your rate limit.", "github_copilot", "gpt-4.1")
        error.litellm_response_headers = {"retry-after": "Mon, 01 Jan 1900 00:00:00 -0000"}
        assert "Try again" not in plugins.describe(error)

    def test_a_run_past_its_deadline_is_named_and_degrades(self, demo_repo: Path, cli, monkeypatch, capsys):
        """Measured before: no run had a deadline of its own, and on the Copilot arm one guideline call could take its
        180 s seven times over. A run past the deadline is the run's failure, each guideline named with it."""
        monkeypatch.setattr(importlib.import_module("adk_review.judge.run"), "RUN_DEADLINE_S", 1e-6)
        assert run_review(cli, demo_repo) == 3
        assert "the run did not finish within 1e-06 s" in capsys.readouterr().out

    def test_a_guideline_cut_short_with_the_run_is_never_said_done(self, cli, demo_repo: Path, monkeypatch, capsys):
        """A guideline cut short with the run, here by a job's stop inside a sibling's call, is still out, and a person
        at the terminal reads it so. Measured on ADK 2.12.0: ADK runs the after-agent hook on a cancelled agent too,
        where 2.9.2 did not, and `Progress` said "done in N s" of a guideline no model answered."""
        import asyncio

        from conftest import FakeModel

        async def stopped(_self, llm_request, **_kwargs):
            if SECURITY in str(llm_request.config.system_instruction or ""):
                raise cli.Terminated  # the stop, inside one guideline's call
            await asyncio.Event().wait()  # its siblings mid-call, cut short with the run, never answered
            yield

        monkeypatch.setattr(FakeModel, "generate_content_async", stopped)
        # The deadline as a backstop: a stop the fake never raises, its phrase reworded, is exit 3 here, not a hang.
        monkeypatch.setattr(importlib.import_module("adk_review.judge.run"), "RUN_DEADLINE_S", 5)
        assert run_review(cli, demo_repo, "--progress") == 143
        said = capsys.readouterr().err
        assert "guideline.tests started" in said and not _said_done(said), said

    def test_a_guideline_still_out_past_the_deadline_is_never_said_done(
        self, cli, demo_repo: Path, monkeypatch, capsys
    ):
        """The deadline's own cancellation, from outside every call: a few seconds here, where a hanging Copilot call
        takes the run's whole deadline. Measured on ADK 2.12.0 without the guard: "done in 7.9 s" of each guideline."""
        import asyncio

        from conftest import FakeModel

        from adk_review.judge.plugins import Progress

        entered: list[str] = []  # each call the fake was asked, so a deadline met before one reads as the timing
        ended: list[str] = []  # each agent ADK ran the after-agent hook for: the cut-short ones among them

        async def hangs(_self, llm_request, **_kwargs):
            entered.append(str(llm_request.model))
            await asyncio.Event().wait()  # a call no provider answers
            yield

        after = Progress.after_agent_callback

        async def noted(self, *, agent, callback_context):
            ended.append(agent.name)
            return await after(self, agent=agent, callback_context=callback_context)

        monkeypatch.setattr(importlib.import_module("adk_review.judge.run"), "RUN_DEADLINE_S", 5)
        monkeypatch.setattr(FakeModel, "generate_content_async", hangs)
        monkeypatch.setattr(Progress, "after_agent_callback", noted)
        assert run_review(cli, demo_repo, "--progress") == 3
        said = capsys.readouterr()
        assert "the run did not finish within 5 s" in said.out
        assert len(entered) == len(GUIDELINE_NAMES) and "guideline.tests started" in said.err, (entered, said.err)
        assert {model_key(name) for name in GUIDELINE_NAMES} <= set(ended), ended
        assert not _said_done(said.err), said.err

    def test_a_guideline_its_guard_skips_is_never_said_started(self, tmp_path: Path, cli, capsys) -> None:
        """A change of only ignored files leaves the guidelines nothing to read, and each one's guard skips it. Read
        by review on ADK 2.12.0: ADK runs the plugins' before-agent hook ahead of the guard, and no after-agent hook
        for an agent it skips, so `Progress` said each guideline started, never done, and its heartbeat kept naming
        it. A guideline its guard skips never starts."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "app.py", "x = 1\n", "base")
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "vendor/lib/run.py", "import os\nos.system(input())\n")
        assert cli.main([str(repo), "--head", "change", "--base", BASE, "--progress"]) == 0
        said = capsys.readouterr().err
        assert not any(f"{model_check(name)} started" in said for name in GUIDELINE_NAMES), said

    @pytest.mark.asyncio
    async def test_a_reservation_a_call_cut_short_left_is_dropped_when_the_next_run_starts(self) -> None:
        """A call cancelled with its run, or one an exception escapes, never reaches the after-model hook, so what it
        reserved held: found by review, past the deadline a verifier's call was checked against tokens no call had in
        flight, and could be refused for a ceiling not reached. Such a call ends its run, and the verifiers' is a run of
        its own, so each run starts with nothing reserved; the command's count of what was attempted carries on."""
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        from adk_review.judge.plugins import UsageLedger

        request = LlmRequest(config=types.GenerateContentConfig(system_instruction="x" * 400))
        ledger = UsageLedger(ceiling=10_000)
        a = SimpleNamespace(agent_name="guideline_a", state={})
        assert await ledger.before_model_callback(callback_context=a, llm_request=request) is None
        assert list(ledger.reserved) == ["guideline_a"], "reserved for the call in flight"
        await ledger.before_run_callback(invocation_context=None)  # the verifiers' run, the call never answered
        assert ledger.reserved == {}, "the run that cut the call short left nothing in flight"
        assert ledger.rows["guideline_a"]["attempted"] == 1, "the command's ceiling still holds its run's count"

    @pytest.mark.asyncio
    async def test_the_ledger_counts_the_prompt_tokens_a_provider_served_from_its_cache(self) -> None:
        """Read by review: the ledger kept a call's prompt, answer and total and left out the part the provider served
        from its prompt cache, which ADK fills on both arms, so no report could say whether a cache served a review."""
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types

        from adk_review.deliver.report import _spent
        from adk_review.judge.plugins import UsageLedger

        ledger = UsageLedger()
        a = SimpleNamespace(agent_name="guideline_a", state={})
        cached = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=4_000, cached_content_token_count=3_072, total_token_count=4_100
        )
        plain = types.GenerateContentResponseUsageMetadata(total_token_count=120)
        for usage in (cached, plain):
            await ledger.after_model_callback(callback_context=a, llm_response=LlmResponse(usage_metadata=usage))
        spent = ledger.summary()
        assert spent["cached_tokens"] == spent["by_agent"]["guideline_a"]["cached_tokens"] == 3_072, spent
        assert "4,220 tokens, 3,072 of them from the provider's prompt cache" in _spent(spent)
        assert "cache" not in _spent(UsageLedger().summary()), "a review no cache served says nothing of one"

    @pytest.mark.asyncio
    async def test_a_run_whose_session_is_gone_names_why_for_every_answer_still_out(self) -> None:
        """ADK's session service answers None for a session it does not hold: every check and guideline is then a
        hole named with why, where `.state` on None would have been an AttributeError."""
        from adk_review.judge.run import run_seeded

        class Gone:
            async def create_session(self, **_ids) -> None:
                return None

            async def get_session(self, **_ids) -> None:
                return None

        class Runner:
            app_name, session_service = "gone", Gone()

            async def run_async(self, **_message):
                for event in ():
                    yield event

        run = await run_seeded(Runner(), session_id="s", state={}, prompt="check")
        assert (
            run.state == {}
            and run.why("checks", "no answer") == "the run's session was gone when its answers were read"
        )

    def test_a_role_may_choose_its_own_model(self, monkeypatch) -> None:
        from adk_review.judge.config import build_model

        monkeypatch.setenv("REVIEW_PROVIDER", "gemini")
        monkeypatch.setenv("REVIEW_MODEL", "gemini-2.5-flash")
        monkeypatch.setenv("REVIEW_VERIFIER_MODEL", "gemini-2.5-pro")
        assert (build_model(), build_model(role="verifier")) == ("gemini-2.5-flash", "gemini-2.5-pro")

    @pytest.mark.parametrize("provider", ["gemini", "copilot"])
    def test_a_fallback_model_stands_behind_every_roles_model_on_both_arms(self, provider: str, monkeypatch) -> None:
        """ADK's `FallbackModel` moves a call to the next model on a 429 or a 5xx; on the free tier the quota is per
        model, so a second model is a second quota. Names on Gemini, LiteLLM models on Copilot, the role's own first."""
        from google.adk.models import FallbackModel

        from adk_review.judge.config import build_model

        monkeypatch.setenv("REVIEW_PROVIDER", provider)
        monkeypatch.setenv("REVIEW_MODEL", "first")
        monkeypatch.setenv("REVIEW_VERIFIER_MODEL", "strict")
        monkeypatch.setenv("REVIEW_FALLBACK_MODEL", "second")
        guidelines, verifier = build_model(), build_model(role="verifier")
        assert isinstance(guidelines, FallbackModel) and isinstance(verifier, FallbackModel)

        def names(model: FallbackModel) -> list[str]:
            return [m if isinstance(m, str) else m.model for m in model.models]

        prefix = "" if provider == "gemini" else "github_copilot/"
        assert names(guidelines) == [f"{prefix}first", f"{prefix}second"]
        assert names(verifier) == [f"{prefix}strict", f"{prefix}second"]
        monkeypatch.delenv("REVIEW_FALLBACK_MODEL")
        assert not isinstance(build_model(), FallbackModel), "no fallback named, no wrapper"

    @pytest.mark.parametrize("provider", ["gemini", "copilot"])
    def test_a_fallback_that_is_the_roles_own_model_is_not_tried_twice(self, provider: str, monkeypatch) -> None:
        """Read by review: with `REVIEW_VERIFIER_MODEL` and `REVIEW_FALLBACK_MODEL` one model, the verifier fell back
        to itself, so a 429 or a timeout asked the same model again, its attempts and its wait doubled."""
        from google.adk.models import FallbackModel

        from adk_review.judge.config import build_model

        monkeypatch.setenv("REVIEW_PROVIDER", provider)
        monkeypatch.setenv("REVIEW_MODEL", "first")
        monkeypatch.setenv("REVIEW_VERIFIER_MODEL", "second")
        monkeypatch.setenv("REVIEW_FALLBACK_MODEL", "second")
        assert isinstance(build_model(), FallbackModel), "the guidelines' model still has the fallback behind it"
        assert not isinstance(build_model(role="verifier"), FallbackModel), "one model, no wrapper"

    def test_a_model_name_no_class_claims_is_a_sentence_for_every_role_the_run_builds(self, monkeypatch) -> None:
        from adk_review.judge.config import require_ready

        monkeypatch.setenv("REVIEW_PROVIDER", "gemini")
        monkeypatch.setenv("GOOGLE_API_KEY", "x")
        monkeypatch.setenv("REVIEW_MODEL", "no-such-model")
        assert (require_ready() or "").startswith("REVIEW_MODEL:")
        monkeypatch.setenv("REVIEW_MODEL", "gemini-2.5-flash")
        monkeypatch.setenv("REVIEW_VERIFIER_MODEL", "no-such-model")
        assert require_ready() is None, "a role the run does not build is not checked"
        assert (require_ready(roles=("verifier",)) or "").startswith("REVIEW_VERIFIER_MODEL:")
        monkeypatch.setenv("REVIEW_VERIFIER_MODEL", "gemini-2.5-flash")
        monkeypatch.setenv("REVIEW_FALLBACK_MODEL", "no-such-model")
        assert (require_ready() or "").startswith("REVIEW_FALLBACK_MODEL:"), "the fallback is a model the run builds"


class TestAGuidelinesOwnModel:
    """Asked by the owner: a guideline may name the model it runs on. Its file names one per arm, `REVIEW_<ID>_MODEL`
    on a machine overrides it, and an arm the file leaves out runs on `REVIEW_MODEL`."""

    def test_the_most_specific_choice_names_a_guidelines_model(self, monkeypatch) -> None:
        from adk_review.core.guidelines import Models
        from adk_review.judge.settings import model_name, model_var

        monkeypatch.setenv("REVIEW_PROVIDER", "gemini")
        monkeypatch.setenv("REVIEW_MODEL", "everyone")
        monkeypatch.delenv("REVIEW_SECURITY_MODEL", raising=False)
        preset = Models(gemini="preset", copilot="claude-x")
        assert (model_var("security", preset), model_name("security", preset)) == (
            "guideline.security models.gemini",
            "preset",
        )
        monkeypatch.setenv("REVIEW_SECURITY_MODEL", "own")
        assert (model_var("security", preset), model_name("security", preset)) == ("REVIEW_SECURITY_MODEL", "own")
        monkeypatch.delenv("REVIEW_SECURITY_MODEL")
        assert model_name("security", Models(copilot="claude-x")) == "everyone", "an arm the file leaves out"
        monkeypatch.setenv("REVIEW_MY_CHECK_MODEL", "hyphen")
        assert model_name("my-check") == "hyphen", "a hyphen in an id is an underscore in a variable's name"

    def test_a_guideline_runs_on_the_model_its_file_names(self, monkeypatch) -> None:
        load_agent(monkeypatch)
        from adk_review.core.guidelines import Models
        from adk_review.judge.agents import build_guideline
        from adk_review.judge.guidelines.security import Security

        class Strong(Security):
            models = Models(gemini="fake.strong")

        assert build_guideline(Strong()).model == "fake.strong"
        assert build_guideline(Security()).model == "fake", "no choice of its own: everyone's model"

    def test_a_guidelines_model_the_arm_cannot_run_is_refused_before_any_call(self, monkeypatch) -> None:
        from adk_review.core.guidelines import Models
        from adk_review.judge.config import require_ready, served

        monkeypatch.setenv("REVIEW_PROVIDER", "gemini")
        monkeypatch.setenv("GOOGLE_API_KEY", "x")
        monkeypatch.setenv("REVIEW_MODEL", "gemini-2.5-flash")
        presets = {"security": Models(gemini="no-such-model")}
        assert (require_ready(roles=("security",), presets=presets) or "").startswith(
            "guideline.security models.gemini:"
        )
        assert require_ready(roles=("security",), presets={"security": Models(gemini="gemini-2.5-pro")}) is None
        assert served(("security",), {"security": Models(gemini="gemini-2.5-pro")}).endswith(
            "(security: gemini-2.5-pro)"
        ), "the report names a guideline on a model of its own"

    def test_a_review_names_and_checks_each_guidelines_own_model(self, cli, demo_repo: Path, monkeypatch, capsys):
        import importlib

        from adk_review.core.guidelines import Models

        security = importlib.import_module("adk_review.judge.guidelines.security").Security
        monkeypatch.setattr(security, "models", Models(gemini="fake.strong"))
        argv = [str(demo_repo), "--head", "feature/payments", "--base", "main", "--dry-run"]
        assert cli.main(argv) == 0
        assert "security: fake.strong" in capsys.readouterr().out


class TestTheTokenCeiling:
    """A ceiling ends the spending, never the report: the refused guideline is named, what was found is shown, and
    the exit is 3."""

    def test_a_ceiling_no_call_fits_under_refuses_every_guideline_by_name(self, demo_repo: Path, cli, capsys) -> None:
        code = run_review(cli, demo_repo, "--max-tokens", "100")
        out = capsys.readouterr().out
        assert code == 3, out
        for name in GUIDELINE_NAMES:
            assert (
                f"### guideline.{name} — FAILED: TOKEN_CEILING: this call would pass the ceiling of 100 tokens" in out
            )
        assert "### rule.secrets" in out and "hard-coded credential" in out, "what code found is still reported"
        assert (
            f"0 model call(s) completed of {len(GUIDELINE_NAMES)} attempted" in out and "- ceiling: 100 tokens" in out
        )

    def test_a_ceiling_nothing_reaches_refuses_nothing(self, demo_repo: Path, cli, capsys) -> None:
        assert run_review(cli, demo_repo, "--max-tokens", "1000000") == 1
        assert "TOKEN_CEILING" not in capsys.readouterr().out

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("cli")  # the package on the fake, so the verifiers build
    async def test_a_verifier_the_ceiling_refuses_leaves_its_finding_named_with_the_ceilings_reason(self) -> None:
        """The ceiling reaches the verifiers through the same hook: a refused verifier is its finding's reason."""

        from adk_review.core.findings import Review
        from adk_review.judge.plugins import UsageLedger
        from adk_review.judge.verify import judge_findings

        findings = Review.model_validate(CANNED_REVIEW).findings
        judged = await judge_findings(findings, "+x = 1", [UsageLedger(ceiling=100)])
        assert len(judged) == len(findings)
        refused = "TOKEN_CEILING: this call would pass the ceiling of 100 tokens"
        assert all(isinstance(reason, str) and reason.startswith(refused) for reason in judged)

    @pytest.mark.asyncio
    async def test_a_call_reserves_its_cost_first_and_releases_it_when_its_response_comes_back(self) -> None:
        """Three guidelines call at once, so the check is against tokens spent plus tokens in flight, not spent alone.
        An error response — what the error plugin puts in an exception's place — releases the reservation the
        same way, and is over rather than completed."""
        from google.adk.models.llm_request import LlmRequest
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types

        from adk_review.judge.plugins import UsageLedger

        request = LlmRequest(config=types.GenerateContentConfig(system_instruction="x" * 400))
        ledger = UsageLedger(ceiling=int(UsageLedger._estimate(request) * 1.5))
        a, b = SimpleNamespace(agent_name="guideline_a", state={}), SimpleNamespace(agent_name="guideline_b", state={})
        assert await ledger.before_model_callback(callback_context=a, llm_request=request) is None
        refused = await ledger.before_model_callback(callback_context=b, llm_request=request)
        assert refused is not None and refused.error_code == "TOKEN_CEILING", "a's reservation holds b back"
        billed = LlmResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=120))
        await ledger.after_model_callback(callback_context=a, llm_response=billed)
        assert ledger.reserved == {}
        assert await ledger.before_model_callback(callback_context=b, llm_request=request) is None, "released"
        failed = LlmResponse(error_code="RuntimeError", error_message="provider said: 429")
        await ledger.after_model_callback(callback_context=b, llm_response=failed)
        assert ledger.reserved == {}, "an error response releases it too"
        assert (ledger.rows["guideline_a"]["calls"], ledger.rows["guideline_b"]["calls"]) == (1, 0), (
            "over, not completed"
        )

    @pytest.mark.asyncio
    async def test_a_call_reserves_the_thinking_its_request_allows(self) -> None:
        """Measured by review: a call reserved its text and an answer's worth, while Gemini bills the thinking a
        guideline's budget allows and the ledger counts it in the provider's total, so `default`'s three guidelines in
        flight could pass the ceiling by about 7,000 tokens. A request carrying a budget reserves it too."""
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        from adk_review.judge.plugins import UsageLedger

        plain = LlmRequest(config=types.GenerateContentConfig(system_instruction="x" * 400))
        budget = types.ThinkingConfig(thinking_budget=4096)
        thinking = LlmRequest(config=types.GenerateContentConfig(system_instruction="x" * 400, thinking_config=budget))
        ledger = UsageLedger(ceiling=UsageLedger._estimate(plain) + 4095)
        security = SimpleNamespace(agent_name="guideline_security", state={})
        refused = await ledger.before_model_callback(callback_context=security, llm_request=thinking)
        assert refused is not None and refused.error_code == "TOKEN_CEILING", "its thinking does not fit"
        assert await ledger.before_model_callback(callback_context=security, llm_request=plain) is None

    @pytest.mark.asyncio
    async def test_in_chat_each_turn_has_the_whole_ceiling_and_the_command_one_for_its_run(self, monkeypatch) -> None:
        """Measured by review: built once for the server's life, chat's ledger refused every turn past the ceiling's
        worth, in any session, until a restart. A turn is chat's one review; the command's ceiling holds its run and its
        verifiers' together."""
        chat = load_agent(monkeypatch).app  # first: it imports the package afresh, the ledger's class with it
        from google.adk.models.llm_request import LlmRequest
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types

        from adk_review.judge.plugins import UsageLedger

        assert next(p for p in chat.plugins if isinstance(p, UsageLedger)).per_turn, "chat's ceiling is each turn's"
        request = LlmRequest(config=types.GenerateContentConfig(system_instruction="x" * 400))
        billed = LlmResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=10_000))
        for per_turn in (True, False):
            ledger = UsageLedger(ceiling=UsageLedger._estimate(request) * 2, per_turn=per_turn)
            intake = SimpleNamespace(agent_name="intake", state={})
            assert await ledger.before_model_callback(callback_context=intake, llm_request=request) is None
            await ledger.after_model_callback(callback_context=intake, llm_response=billed)
            await ledger.before_run_callback(invocation_context=None)  # chat's next turn, or the verifiers' run
            fits = await ledger.before_model_callback(callback_context=intake, llm_request=request) is None
            assert fits is per_turn, "a turn starts afresh; a run and its verifiers share one ceiling"

    @pytest.mark.asyncio
    async def test_a_prose_agent_the_ceiling_refuses_answers_why_as_a_provider_error_does(self) -> None:
        """Measured before: a chat turn whose intake the ceiling refused ended with no answer at all, where a provider's
        error answers its sentence and leaves it for the guidelines' guard. The ceiling's refusal answers the same way;
        a guideline, which answers in a schema, still answers nothing, a hole."""
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        from adk_review.judge.agents import FAILURE
        from adk_review.judge.plugins import UsageLedger

        intake = SimpleNamespace(agent_name="intake", state={})
        request = LlmRequest(config=types.GenerateContentConfig(system_instruction="x" * 400))
        refused = await UsageLedger(ceiling=100).before_model_callback(callback_context=intake, llm_request=request)
        said = "I could not reach the model: TOKEN_CEILING: this call would pass the ceiling of 100 tokens"
        assert refused.content.parts[0].text.startswith(said), refused
        assert intake.state[FAILURE].startswith("intake: TOKEN_CEILING"), intake.state


class TestTheSecondOpinion:
    """A verifier per guideline finding, before the fold, so a rule's finding is never asked and never lost — nor a
    guideline's finding that only says again what a rule found."""

    def test_a_guideline_finding_that_restates_a_rules_is_never_asked(self, demo_repo: Path, cli, capsys) -> None:
        """Measured live on Copilot: 2 of 5 verifier calls judged a guideline's reading of the key the secrets rule had
        already found as a blocker. Every canned guideline finding quotes S602's line at S602's severity."""
        assert run_review(cli, demo_repo, "--verify") == 1
        out = capsys.readouterr().out
        assert "`verifier_" not in out and "findings refuted by the verifier: 0" in out, "no verifier was asked"
        guidelines = len(GUIDELINE_NAMES)
        assert f"spend: {guidelines} model call(s) completed of {guidelines} attempted" in out
        assert f"folded into one finding: {1 + guidelines}" in out, (
            "S105 into the secrets rule's, each guideline's into S602"
        )

    def test_a_verify_that_holds_everything_folds_as_without_it(self, cli, monkeypatch) -> None:
        """Measured by review: a restatement was folded before the fold, by a rule the fold does not use, and lost
        where the fold had folded its rule into a more severe reading it did not overlap, though nothing refuted it."""
        from adk_review.core.findings import Finding
        from adk_review.core.verdict import Outcome, Sourced
        from adk_review.judge.verify import Judgement

        def found(source: str, severity: str, *lines: str) -> Sourced:
            finding = Finding(file="a.py", severity=severity, title=source, evidence="\n".join(lines), suggestion="s")
            return Sourced(source, finding)

        rules = [found("rule.lint", "major", "x = 1")]
        guidelines = [
            found("guideline.security", "blocker", "x = 1", "y = 2"),
            found("guideline.tests", "major", "w = 0", "x = 1"),
        ]

        async def holds(findings, _diff, _plugins):
            return [Judgement(holds=True, reason="r") for _finding in findings]

        verify = importlib.import_module("adk_review.judge.verify")
        monkeypatch.setattr(verify, "judge_findings", holds)
        without, verified = Outcome(kept=list(guidelines)), Outcome(kept=list(guidelines))
        order = ["rule.lint", "guideline.security", "guideline.tests"]
        cli.fold(without, rules, order)
        verify.second_opinion(verified, rules, "", [], asyncio.run)
        cli.fold(verified, rules, order)
        assert (
            [s.source for s in verified.kept]
            == [s.source for s in without.kept]
            == ["guideline.security", "guideline.tests"]
        )
        assert len(verified.folded) == len(without.folded) == 1

    def test_a_finding_the_verifier_refutes_is_dropped_and_named(
        self, demo_repo: Path, cli, fake_provider, capsys
    ) -> None:
        fake_provider.answers_when(THE_GUIDELINES, SAYS_MORE)
        fake_provider.answers_when(THE_VERIFIER, REFUTED)  # would refute anything it were asked
        code = run_review(cli, demo_repo, "--verify")
        out = capsys.readouterr().out
        assert code == 3, out  # a blocker refuted is no approval: a person reads it, as a blocker dropped
        refuted = len(GUIDELINE_NAMES)
        assert f"findings refuted by the verifier: {refuted}\n  - {refuted} at or above `--fail-on blocker`" in out
        assert (
            "- `guideline.security`: security: shell=True with request-controlled input — the lines do not show it"
            in out
        )
        assert "`guideline.security`" in out.split("### no findings from ", 1)[1], "it looked, and found nothing"
        # The rules' findings are facts: never asked, so never refuted, and a rule's reading of the line stands
        # whatever the verifier said of a guideline's.
        assert "secrets: a hard-coded credential (src/payments/config.py:4)" in out
        assert "secrets: a hard-coded credential (src/payments/config.py:5)" in out and "- **major** lint: S602" in out

    def test_the_run_and_its_second_opinion_share_one_event_loop(
        self, demo_repo: Path, cli, fake_provider, monkeypatch
    ) -> None:
        """`asyncio.run` makes a loop and closes it, so the guidelines and the verifiers each had their own, and a
        client a library kept from the first met a closed loop in the second, as ADK's cached Gemini client did
        (google/adk-python#5538; since 2.10 ADK keeps one a loop) and as a cache keyed by the loop's id still can. One
        loop serves the review from its first model call to its last."""
        import asyncio

        from conftest import FakeModel

        loops: dict[str, set[int]] = {"guideline": set(), "verifier": set()}
        answer = FakeModel.generate_content_async

        async def noted(self, llm_request, stream=False):
            role = "verifier" if THE_VERIFIER in str(llm_request.config.system_instruction) else "guideline"
            loops[role].add(id(asyncio.get_running_loop()))
            async for response in answer(self, llm_request, stream):
                yield response

        monkeypatch.setattr(FakeModel, "generate_content_async", noted)
        fake_provider.answers_when(THE_GUIDELINES, SAYS_MORE)
        assert run_review(cli, demo_repo, "--verify") == 1
        assert len(loops["guideline"]) == len(loops["verifier"]) == 1, loops
        assert loops["guideline"] == loops["verifier"], "one loop for both"

    def test_findings_the_verifier_holds_stay(self, demo_repo: Path, cli, fake_provider, capsys) -> None:
        fake_provider.answers_when(THE_GUIDELINES, SAYS_MORE)
        assert run_review(cli, demo_repo, "--verify") == 1
        out = capsys.readouterr().out
        assert "findings refuted by the verifier: 0" in out and "could not judge, kept: 0" in out
        for number in range(len(GUIDELINE_NAMES)):
            assert f"`verifier_{number}`: 1 call(s)" in out, "one verifier per guideline finding, each in the ledger"
        # Measured before: the count was read before the verifiers ran, and their calls hid the keys unnamed.
        assert f"- secrets redacted before any model call: {2 * 2 * len(GUIDELINE_NAMES)}" in out, "2 keys, 6 calls"
        assert (
            "### guideline.security\n- **blocker** security: shell=True" in out and "- **major** lint: S602" not in out
        )
        assert "  - also on these lines: `rule.lint`: lint: S602" in out, "ruff's reading, under the finding it repeats"
        counted = "### rule.lint\n- folded: 2 on lines another finding already holds, each listed under that finding"
        assert counted in out, "S105 and S602, each counted in its own section"
        assert f"folded into one finding: {1 + len(GUIDELINE_NAMES)}" in out, (
            "S105 and S602, and two guidelines into the first"
        )

    def test_a_review_its_verifiers_could_not_judge_is_never_kept(
        self, demo_repo: Path, cli, fake_provider, tmp_path: Path, capsys
    ) -> None:
        """Seen live: a `--verify` review whose verifier met Copilot's 429 decided APPROVED on a major no verifier
        judged, and a decided review was kept, so the next run would have served it without asking again. One its
        verifiers could not judge is never kept: the next run asks."""
        fake_provider.answers_when(THE_GUIDELINES, SAYS_MORE)
        fake_provider.raises_when(THE_VERIFIER, quota())
        cache = tmp_path / "cache"
        assert run_review(cli, demo_repo, "--verify", "--cache", str(cache)) == 1
        capsys.readouterr()
        assert not cache.exists() or not any(cache.iterdir()), "a review its verifiers could not judge is not kept"

    def test_a_verifier_that_fails_leaves_the_finding_and_names_why(
        self, demo_repo: Path, cli, fake_provider, capsys
    ) -> None:
        fake_provider.answers_when(THE_GUIDELINES, SAYS_MORE)
        fake_provider.raises_when(THE_VERIFIER, quota())
        assert run_review(cli, demo_repo, "--verify") == 1
        out = capsys.readouterr().out
        assert f"could not judge, kept: {len(GUIDELINE_NAMES)}" in out
        assert (
            "- `guideline.security`: security: shell=True with request-controlled input"
            " — RuntimeError: provider said: 429 quota" in out
        )
        guidelines = verifiers = len(GUIDELINE_NAMES)
        assert f"{guidelines} model call(s) completed of {guidelines + verifiers} attempted" in out, (
            "a call that failed is attempted, not completed"
        )


class TestWhatTheBranchAlreadyHolds:
    def test_a_finding_whose_suggested_code_the_branch_already_holds_is_removed_and_named(
        self, cli, fake_provider, tmp_path: Path, capsys
    ) -> None:
        """Read by review on a branch that removed a parameter at every use: six majors quoted a deleted line, read it
        as code the branch still holds, and suggested the code the branch already held. Such a finding asks for no
        change: it is removed and named, and one at the bar still holds the approval back, as a dropped one does. A
        finding that restores what the change deleted asks for a change, and stays. Each guideline's finding names
        its check, as a rule's does."""
        call = "def make(logger, model_code):\n    return build(\n        logger=logger,\n"
        call += "{}        model_code=model_code,\n    )\n"
        before = {"app.py": call.format("        correlation_id=correlation_id,\n")}
        repo = branched(tmp_path, before, {"app.py": call.format("")})
        quote = "-        correlation_id=correlation_id,\n        model_code=model_code,\n    )"
        misread = {
            "file": "app.py",
            "severity": "major",
            "title": "make passes the removed correlation_id",
            "evidence": quote,
            "why": "An unexpected argument raises a TypeError.",
            "suggestion": "Remove correlation_id from the call.",
            "code": "return build(\n    logger=logger,\n    model_code=model_code,\n)",
        }
        restores = {
            **misread,
            "severity": "minor",
            "title": "the call no longer passes correlation_id",
            "code": "return build(\n    logger=logger,\n    correlation_id=correlation_id,\n"
            "    model_code=model_code,\n)",
        }
        # Read by review: the check's name went on after the model's title was cut, past the cap every title keeps.
        long = {
            **restores,
            "title": "x" * (TITLE_MAX_CHARS + 50),
            "evidence": "def make(logger, model_code):",
            "code": "",
        }
        fake_provider.answers_when(THE_GUIDELINES, {"findings": [misread, restores, long]})
        report, comment = tmp_path / "r.json", tmp_path / "c.md"
        argv = [str(repo), "--head", "change", "--base", "main", "--check", "guideline.correctness"]
        assert cli.main([*argv, "--json", str(report), "--comment", str(comment)]) == 0
        out = capsys.readouterr().out
        data = json.loads(report.read_text(encoding="utf-8"))
        titles = [f["title"] for f in data["findings"]]
        assert titles[0] == "correctness: the call no longer passes correlation_id", titles
        assert titles[1].startswith("correctness: xxx") and titles[1] == cut_at(titles[1], TITLE_MAX_CHARS), titles
        assert [f["title"] for f in data["already_held"]] == ["correctness: make passes the removed correlation_id"]
        assert data["already_held_at_fail_on"] == 0, data
        assert "findings removed, the branch already holds their suggested code: 1" in out, out
        assert "make passes the removed" not in comment.read_text(encoding="utf-8"), "never a point to fix"
        # At the bar it holds the approval back until a person reads it: code removed it, and code can be wrong.
        assert cli.main([*argv, "--fail-on", "major", "--json", str(report)]) == 3
        capsys.readouterr()
        assert json.loads(report.read_text(encoding="utf-8"))["already_held_at_fail_on"] == 1


class TestTheFences:
    """Every untrusted section a model reads sits between two marker lines carrying a nonce only this process knows,
    and the instruction ends with a reminder after it: a sentence at the top is no boundary."""

    @pytest.mark.usefixtures("cli")  # the package loaded on the fake, so an agent builds without a key
    def test_the_guidelines_and_the_verifiers_fence_what_they_read_and_end_with_the_reminder(self) -> None:
        """And what holds a guideline's own words, a verifier's finding and chat's verdict, is fenced by a second
        nonce no guideline is shown: a guideline the branch steered knows the first from its own prompt, and could
        write its marker into a title to end a later section early."""
        from adk_review.judge.agents import BOUNDARY, JUDGED_BOUNDARY, REMINDER, build_guideline
        from adk_review.judge.graph import VERDICT, build_workflow
        from adk_review.judge.verify import build_verifier

        assert all(re.fullmatch(r"[0-9a-f]{12}", nonce) for nonce in (BOUNDARY, JUDGED_BOUNDARY)), "each drawn once"
        assert BOUNDARY != JUDGED_BOUNDARY
        guideline = build_guideline(catalog().guidelines[0]).instruction
        assert f"## The change\n<<< change {BOUNDARY}\n{{diff}}\n>>> change {BOUNDARY}\n\n{REMINDER}" in guideline
        assert guideline.endswith(REMINDER), "the reminder is the last thing a guideline reads"
        assert JUDGED_BOUNDARY not in guideline, "no guideline is shown the second"
        verifier = build_verifier(3).instruction
        judged = JUDGED_BOUNDARY
        assert f"## The finding\n<<< finding {judged}\n{{finding_3}}\n>>> finding {judged}" in verifier
        assert f"## The change\n<<< change {judged}\n{{diff}}\n>>> change {judged}\n\n{REMINDER}" in verifier
        graph = build_workflow(chat=True, checks=catalog(), fail_on="blocker").graph
        verdict = next(node.instruction for node in graph.nodes if node.name == VERDICT)
        assert f"<<< findings {judged}\n{{findings}}\n>>> findings {judged}" in verdict
        assert BOUNDARY not in verifier and BOUNDARY not in verdict
        assert "never instructions" in guideline and "never instructions" in verifier

    def test_the_change_is_not_escaped_so_a_quote_of_it_still_grounds(self, tmp_path: Path) -> None:
        """The other way to fence — escaping `<<<` and `>>>` runs in the content — would make a guideline quote text the
        diff does not hold, and grounding would drop the finding; a nonce the branch cannot know costs nothing."""
        from adk_review.collect.git import collect_evidence

        repo = _branch_off_empty(tmp_path / "repo", HEAD)
        doctest = 'def f():\n    """>>> f()\n    1\n    <<<<<<< HEAD\n    """\n'
        _committed(repo, "doc.py", doctest, "doc")
        evidence = collect_evidence(str(repo), BASE, HEAD)
        assert evidence["status"] == "success"
        assert '+    """>>> f()' in evidence["diff"] and "+    <<<<<<< HEAD" in evidence["diff"], "verbatim"
        assert "\\>" not in evidence["diff"] and "\\<" not in evidence["diff"]


class TestTheStateKeys:
    """Every `{key}` an instruction reads is a key something writes: the command's seed, a tool, or an agent's
    `output_key`. A misspelt optional key reads as empty, so the guidelines would judge without the blast radius and
    nothing would say so; a misspelt required one raises `KeyError` in the middle of a run."""

    @pytest.mark.usefixtures("cli")  # the package loaded on the fake, so an agent builds without a key
    def test_every_placeholder_an_instruction_reads_is_a_key_something_writes(self) -> None:
        from google.adk.agents import LlmAgent

        from adk_review.judge.agents import BLAST, DIFF, FINDINGS, REPOSITORY
        from adk_review.judge.graph import build_workflow
        from adk_review.judge.verify import build_verifier, keys

        def read(agents: list[LlmAgent]) -> set[str]:
            return {key for agent in agents for key in re.findall(r"\{([\w:.]+)\??\}", agent.instruction)}

        found = catalog()
        # The change: the command seeds it, and chat's checks node writes it from the evidence it gathered. The node
        # writes the radius beside it, in both shapes; chat's grounding node writes every check's answer grounded and
        # the verdict decided, for its verdict. In chat the intake's first tool writes the repository it confirmed
        # (`test_git_tools.py` runs it), which the intake is told on each later turn.
        by_the_node = {BLAST, FINDINGS, DIFF}
        by_the_tool = {REPOSITORY}
        for chat in (False, True):
            graph = build_workflow(chat=chat, checks=found, fail_on="blocker").graph
            agents = [node for node in graph.nodes if isinstance(node, LlmAgent)]
            written = by_the_node | by_the_tool | {agent.output_key for agent in agents}
            assert read(agents) - written == set(), f"chat={chat}: an instruction reads a key nothing writes"
            assert {DIFF, BLAST} <= read(agents), "the guidelines read the change and the radius beside it"
            assert chat == (FINDINGS in read(agents)), "chat's verdict reads every check's answer, grounded and decided"
            assert chat == (REPOSITORY in read(agents)), "chat's intake reads the repository its conversation confirmed"
        finding, _judgement = keys(0)
        assert read([build_verifier(0)]) == {finding, DIFF}, "a verifier reads its finding and the change, as seeded"


class TestTheChecksNode:
    """Every rule runs in one node of the graph, the same way, before the guidelines, in both shapes. Each rule's
    answer is left in state under its check's key, every answer rendered and capped for the chat's verdict, and the
    blast radius for the guidelines; the node never raises, since a node that raises ends the run."""

    @staticmethod
    async def _run(rules, state: dict, evidence: dict | None = None):
        """The checks node alone, over a session seeded with `state`; a `temp:` key in it is written in the turn, by a
        node before the checks, as chat's intake writes the scope: a session keeps none."""
        from google.adk.apps.app import App
        from google.adk.runners import InMemoryRunner
        from google.adk.workflow import FunctionNode, Workflow

        from adk_review.judge.checks import build_checks
        from adk_review.judge.run import run_seeded

        def confirmed(ctx) -> None:
            for key in [key for key in state if key.startswith("temp:")]:
                ctx.state[key] = state[key]

        seed = FunctionNode(func=confirmed, name="confirmed")
        graph = Workflow(name="checks_only", edges=[("START", seed, build_checks(rules, evidence))])
        runner = InMemoryRunner(app=App(name="checks_only", root_agent=graph))
        return await run_seeded(runner, session_id="s", state=state, prompt="check")

    @staticmethod
    def _answer(run, check: str):
        from adk_review.core.findings import Checked

        return Checked.model_validate(run.state[state_key(check)])

    @pytest.mark.usefixtures("cli")
    def test_the_checks_run_before_the_guidelines_in_both_shapes(self) -> None:
        from adk_review.judge.checks import NAME
        from adk_review.judge.graph import build_workflow

        found = catalog()
        agents = {state_key(guideline.check_id) for guideline in found.guidelines}
        # In chat the intake goes first, and a gate after it goes on to the checks only for a scope it confirmed.
        for chat, chain in ((False, ["__START__", NAME]), (True, ["__START__", "intake", "scoped", NAME])):
            edges = build_workflow(chat=chat, checks=found, fail_on="blocker").graph.edges
            way_in = {edge.to_node.name: edge for edge in edges}  # each node up to the checks has one
            walked = [NAME]
            while walked[0] in way_in:
                walked.insert(0, way_in[walked[0]].from_node.name)
            assert walked == chain and way_in[NAME].route is (True if chat else None), walked
            assert {edge.to_node.name for edge in edges if edge.from_node.name == NAME} == agents

    @pytest.mark.asyncio
    async def test_in_chat_the_node_gathers_the_evidence_for_the_commits_the_intake_confirmed(
        self, demo_repo: Path
    ) -> None:
        """For the commits, never their names again. Measured before: a head given as a sha had resolved through no
        ref, and the node, handed the ref, found no head: every rule said it did not look while the guidelines
        reviewed the change. The node writes the radius the guidelines read, and keeps each file as grounding reads it
        beside the change, which chat's grounding node reads before its verdict."""
        from adk_review.collect.git import preflight
        from adk_review.judge.agents import BLAST, QUOTABLE, SCOPE
        from adk_review.judge.rules.secrets import Secrets

        for head in (HEAD, _git_in(demo_repo, "rev-parse", HEAD)):
            run = await self._run((Secrets(),), {SCOPE: preflight(str(demo_repo), BASE, head)})
            answer = self._answer(run, rule_id("secrets"))
            assert answer.could_not_look is None and run.failure is None, (head, answer)
            assert [(f.file, f.line) for f in answer.findings] == [
                ("src/payments/config.py", 4),
                ("src/payments/config.py", 5),
            ]
        assert "\n- src/payments/charge.py: 1 module and 1 test" in run.state[BLAST]
        held = run.state[QUOTABLE]["src/payments/config.py"]
        assert any("AWS_ACCESS_KEY_ID" in line for hunk in held["quotable"] for _number, line in hunk), held
        assert (held["unread"], held["cut"]) == ("", False)

    @pytest.mark.asyncio
    async def test_a_cancelled_node_stops_its_rules_at_the_next_rule(self) -> None:
        """The rules run in a thread, which no cancel reaches. Measured: a SIGTERM one second into the rules of a
        2,683-file `--all` ended the review 6.5 s later, the loop's close waiting for every rule still to run."""
        import asyncio
        import contextlib
        import threading

        from adk_review.core.rules import ChangedFile, FileRule

        started, release, ran = threading.Event(), threading.Event(), []

        class First(FileRule):
            id, severity, title, fix = "first", "minor", "t", "f"

            def check(self, _file: ChangedFile) -> list:
                started.set()
                release.wait(10)
                return []

        class Second(FileRule):
            id, severity, title, fix = "second", "minor", "t", "f"

            def check(self, file: ChangedFile) -> list:
                ran.append(file.path)
                return []

        evidence = {"files": [_entry("a.py", "x = 1\n")], "tree": None, "blast": None}
        run = asyncio.create_task(self._run((First(), Second()), {}, evidence))
        assert await asyncio.to_thread(started.wait, 10), "the first rule began"
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run
        release.set()
        await asyncio.to_thread(time.sleep, 0.3)  # the thread's next step, were it to run on
        assert ran == [], "stopped before the next rule"

    @pytest.mark.asyncio
    async def test_with_nothing_gathered_each_check_says_it_did_not_look_and_no_radius_is_kept(self) -> None:
        from adk_review.judge.agents import BLAST, QUOTABLE

        rules = catalog().rules
        run = await self._run(rules, {BLAST: "the last scope's radius"})
        for rule in rules:
            answer = self._answer(run, rule.check_id)
            assert answer.findings == [] and answer.could_not_look == "nothing was gathered, so the rule did not look"
        assert run.state[BLAST] == "", "a scope that gathered nothing never keeps the last one's radius"
        assert run.state[QUOTABLE] == {}, "no file for chat's grounding where nothing was gathered"

    @pytest.mark.asyncio
    async def test_a_check_that_raises_is_its_answer_and_the_run_goes_on(self) -> None:
        rules = catalog().rules
        run = await self._run(rules, {}, evidence={"no": "files"})
        assert run.failure is None, "a node that raised would have ended the run"
        said = {self._answer(run, rule.check_id).could_not_look for rule in rules}
        assert said == {"KeyError: 'files', so the rule did not look"}

    @pytest.mark.asyncio
    async def test_the_commands_evidence_is_dropped_once_the_checks_have_read_it(self, demo_repo: Path) -> None:
        from adk_review.collect.git import collect_evidence
        from adk_review.judge.agents import BLAST

        evidence = collect_evidence(str(demo_repo), BASE, HEAD)
        run = await self._run(catalog().rules, {}, evidence)
        assert self._answer(run, rule_id("demo-money-in-cents")).findings, "the rules read the tree and the texts first"
        assert evidence["tree"] is None
        assert all(e["text"] is None and e["added"] is None and e["diff"] is None for e in evidence["files"])
        assert run.state[BLAST].startswith("## Blast radius"), "the radius, written before the tree went"

    def test_chats_verdict_reads_each_check_decided_one_finding_a_line_and_capped(self, monkeypatch) -> None:
        """The verdict first, as code decided it; each check in the report's order, a finding it kept one line, one the
        diff did not bear out named dropped, one that could not look saying so; each file not read; the whole capped."""
        from adk_review.core.findings import Finding
        from adk_review.core.verdict import Outcome, Sourced
        from adk_review.judge import agents

        def found(title: str, severity: str) -> Finding:
            return Finding(file="src/a.py", line=4, severity=severity, title=title, evidence="+k = 'x'", suggestion="s")

        outcome = Outcome(
            kept=[Sourced("rule.secrets", found("secrets: a key", "blocker"), (4, 4))],
            dropped=[Sourced("guideline.security", found("an invented hole", "major"))],
            errors={"rule.lint": "ruff is not here"},
            unread={"img.png": "binary: no line of it can be read or quoted"},
        )
        checks = ("rule.secrets", "rule.lint", "guideline.security", "guideline.tests")
        rendered = agents.for_the_verdict(outcome, checks, "DEGRADED")
        assert rendered == (
            "verdict: DEGRADED\n"
            "## rule.secrets\n- blocker src/a.py:4: secrets: a key — +k = 'x'\n"
            "## rule.lint — could not look: ruff is not here\n"
            "## guideline.security\n"
            "- dropped, the diff does not bear its quote out: major src/a.py:4: an invented hole\n"
            "## guideline.tests\n- no findings\n"
            "## not read: img.png — binary: no line of it can be read or quoted"
        )
        monkeypatch.setattr(agents, "FINDINGS_CAP_CHARS", 40)
        assert agents.for_the_verdict(outcome, checks, "DEGRADED").endswith("\n[… cut at 40 characters]")

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
    _branch_off_empty,
    _cached_copilot_token,
    _committed,
    _entry,
    _git_in,
    catalog,
    load_agent,
    load_command,
    quota,
    run_review,
)

from adk_review.core.profiles import rule_id, state_key

#: The heading only a verifier's instruction carries: a rule on it reaches the verifiers and no guideline.
THE_VERIFIER = "## The finding"


#: Text only a guideline's instruction carries.
THE_GUIDELINES = "only in your dimension"


#: A guideline's reading of S602's line more severe than the rule's: no restatement, so the verifier is asked.
SAYS_MORE = {**CANNED_REVIEW, "findings": [{**CANNED_REVIEW["findings"][0], "severity": "blocker"}]}


class TestRetriesOnBothArms:
    """Retries ride on the request, where both arms read them; the live proof is a 429."""

    @pytest.mark.parametrize("arm", ["gemini", "copilot"])
    def test_every_judging_request_carries_the_retry_attempts_beside_its_deadline(self, arm: str, monkeypatch) -> None:
        """Copilot's request asks for one: measured against a server that never answered, LiteLLM handed the
        attempts to the OpenAI SDK as its retries and retried around it too, three becoming seven. The SDK's own
        two retries make its three."""
        from adk_review.judge.config import REQUEST_TIMEOUT_S, RETRY_ATTEMPTS, request_config

        monkeypatch.setenv("REVIEW_PROVIDER", arm)
        options = request_config().http_options
        assert options.retry_options.attempts == (RETRY_ATTEMPTS if arm == "gemini" else 1)
        assert options.timeout == REQUEST_TIMEOUT_S * 1000

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
                choices=[{"message": {"role": "assistant", "content": '{"findings": [], "summary": "Nothing."}'}}],
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
    async def test_adk_hands_litellm_the_same_request_as_retries_and_seconds(self, monkeypatch) -> None:
        """The Copilot arm cannot run offline. This drives ADK's own LiteLLM wrapper with only the network client
        replaced, so what LiteLLM would be called with is what is asserted: the attempts as `num_retries`, the
        deadline in seconds, the temperature — one request config, both arms."""
        from google.adk.models.lite_llm import LiteLlm, LiteLLMClient
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types

        from adk_review.judge.config import REQUEST_TIMEOUT_S, request_config

        monkeypatch.setenv("REVIEW_PROVIDER", "copilot")
        called: dict = {}

        class Capture(LiteLLMClient):
            async def acompletion(self, **kwargs):
                called.update(kwargs)
                return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": "ok"}}])

        model = LiteLlm(model="github_copilot/gpt-4.1", llm_client=Capture())
        request = LlmRequest(model=model.model, contents=[types.UserContent("judge")], config=request_config())
        async for _response in model.generate_content_async(request):
            pass
        assert called["num_retries"] == 1 and called["timeout"] == REQUEST_TIMEOUT_S
        assert called["temperature"] == 0.0

    def test_a_run_past_its_deadline_is_named_and_degrades(self, demo_repo: Path, cli, monkeypatch, capsys):
        """Measured before: no run had a deadline of its own, and on the Copilot arm one guideline call could take its
        180 s seven times over. A run past the deadline is the run's failure, each guideline named with it."""
        monkeypatch.setattr(importlib.import_module("adk_review.judge.run"), "RUN_DEADLINE_S", 1e-6)
        assert run_review(cli, demo_repo) == 3
        assert "the run did not finish within 1e-06 s" in capsys.readouterr().out

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


class TestTheTokenCeiling:
    """A ceiling ends the spending, never the report: the refused guideline is named, what was found is shown, and
    the exit is 3."""

    def test_a_ceiling_no_call_fits_under_refuses_every_guideline_by_name(self, demo_repo: Path, cli, capsys) -> None:
        code = run_review(cli, demo_repo, "--max-tokens", "100")
        out = capsys.readouterr().out
        assert code == 3, out
        for name in GUIDELINE_NAMES:
            assert f"### guideline.{name} — FAILED: TOKEN_CEILING: the ceiling of 100 tokens would be passed" in out
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
        refused = "TOKEN_CEILING: the ceiling of 100 tokens"
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
        said = "I could not reach the model: TOKEN_CEILING: the ceiling of 100 tokens would be passed"
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
        assert code == 1, out  # the rules' blockers still stand
        assert f"findings refuted by the verifier: {len(GUIDELINE_NAMES)}" in out
        assert "- `guideline.security`: shell=True with request-controlled input — the lines do not show it" in out
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
        (google/adk-python#5538). One loop serves the review from its first model call to its last."""
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
        assert "### guideline.security\n- **blocker** shell=True" in out and "- **major** lint: S602" not in out
        assert "- folded into `guideline.security`'s finding on the same lines: lint: S602" in out, (
            "ruff's reading, named where it went"
        )
        assert f"folded into one finding: {1 + len(GUIDELINE_NAMES)}" in out, (
            "S105 and S602, and two guidelines into the first"
        )

    def test_a_verifier_that_fails_leaves_the_finding_and_names_why(
        self, demo_repo: Path, cli, fake_provider, capsys
    ) -> None:
        fake_provider.answers_when(THE_GUIDELINES, SAYS_MORE)
        fake_provider.raises_when(THE_VERIFIER, quota())
        assert run_review(cli, demo_repo, "--verify") == 1
        out = capsys.readouterr().out
        assert f"could not judge, kept: {len(GUIDELINE_NAMES)}" in out
        assert (
            "- `guideline.security`: shell=True with request-controlled input — RuntimeError: provider said: 429 quota"
            in out
        )
        guidelines = verifiers = len(GUIDELINE_NAMES)
        assert f"{guidelines} model call(s) completed of {guidelines + verifiers} attempted" in out, (
            "a call that failed is attempted, not completed"
        )


class TestTheFences:
    """Every untrusted section a model reads sits between two marker lines carrying a nonce only this process knows,
    and the instruction ends with a reminder after it: a sentence at the top is no boundary."""

    @pytest.mark.usefixtures("cli")  # the package loaded on the fake, so an agent builds without a key
    def test_the_guidelines_and_the_verifiers_fence_what_they_read_and_end_with_the_reminder(self) -> None:
        from adk_review.judge.agents import BOUNDARY, REMINDER, build_guideline
        from adk_review.judge.verify import build_verifier

        assert re.fullmatch(r"[0-9a-f]{12}", BOUNDARY), "a nonce, drawn once a process"
        guideline = build_guideline(catalog().guidelines[0]).instruction
        assert f"## The change\n<<< change {BOUNDARY}\n{{diff}}\n>>> change {BOUNDARY}\n\n{REMINDER}" in guideline
        assert guideline.endswith(REMINDER), "the reminder is the last thing a guideline reads"
        verifier = build_verifier(3).instruction
        assert f"## The finding\n<<< finding {BOUNDARY}\n{{finding_3}}\n>>> finding {BOUNDARY}" in verifier
        assert f"## The change\n<<< change {BOUNDARY}\n{{diff}}\n>>> change {BOUNDARY}\n\n{REMINDER}" in verifier
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

        from adk_review.judge.agents import BLAST, DIFF, REPOSITORY, RULES
        from adk_review.judge.graph import build_workflow
        from adk_review.judge.verify import build_verifier, keys

        def read(agents: list[LlmAgent]) -> set[str]:
            return {key for agent in agents for key in re.findall(r"\{([\w:.]+)\??\}", agent.instruction)}

        found = catalog()
        # The change: the command seeds it, and chat's checks node writes it from the evidence it gathered. The node
        # writes the radius beside it and every rule's answer rendered for the chat's verdict, in both shapes. In chat
        # the intake's first tool writes the repository it confirmed (`test_git_tools.py` runs it), which the intake
        # is told on each later turn.
        by_the_node = {BLAST, RULES, DIFF}
        by_the_tool = {REPOSITORY}
        for chat in (False, True):
            graph = build_workflow(chat=chat, checks=found, fail_on="blocker").graph
            agents = [node for node in graph.nodes if isinstance(node, LlmAgent)]
            written = by_the_node | by_the_tool | {agent.output_key for agent in agents}
            assert read(agents) - written == set(), f"chat={chat}: an instruction reads a key nothing writes"
            assert {DIFF, BLAST} <= read(agents), "the guidelines read the change and the radius beside it"
            assert chat == (RULES in read(agents)), "the chat's verdict reads the rules' answers, rendered and capped"
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
        reviewed the change. The node writes the radius the guidelines read, and the rules rendered for the verdict."""
        from adk_review.collect.git import preflight
        from adk_review.judge.agents import BLAST, RULES, SCOPE
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
        quoted = '+AWS_ACCESS_KEY_ID = "[REDACTED:aws_access_key_id]"'
        line = f"- blocker src/payments/config.py:4: secrets: a hard-coded credential — {quoted}"
        assert run.state[RULES].startswith(f"## rule.secrets\n{line}\n"), run.state[RULES]

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
        from adk_review.judge.agents import BLAST, RULES

        rules = catalog().rules
        run = await self._run(rules, {BLAST: "the last scope's radius"})
        for rule in rules:
            answer = self._answer(run, rule.check_id)
            assert answer.findings == [] and answer.could_not_look == "nothing was gathered, so the rule did not look"
        assert run.state[BLAST] == "", "a scope that gathered nothing never keeps the last one's radius"
        assert run.state[RULES].count("— could not look: nothing was gathered") == len(rules)

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
        assert self._answer(run, rule_id("money-in-cents")).findings, "the rules read the tree and the texts first"
        assert evidence["tree"] is None
        assert all(e["text"] is None and e["added"] is None and e["diff"] is None for e in evidence["files"])
        assert run.state[BLAST].startswith("## Blast radius"), "the radius, written before the tree went"

    def test_the_verdict_reads_the_rules_rendered_one_finding_a_line_and_capped(self, monkeypatch) -> None:
        from adk_review.core.findings import Checked, Finding
        from adk_review.judge import agents

        found = Finding(
            file="src/a.py", line=4, severity="blocker", title="secrets: a key", evidence="+k = 'x'", suggestion="s"
        )
        answers = {"rule.secrets": Checked(findings=[found]), "rule.lint": Checked(could_not_look="ruff is not here")}
        rendered = agents.for_the_verdict(answers)
        assert rendered == (
            "## rule.secrets\n- blocker src/a.py:4: secrets: a key — +k = 'x'\n"
            "## rule.lint — could not look: ruff is not here\n- no findings"
        )
        monkeypatch.setattr(agents, "RULES_CAP_CHARS", 40)
        assert agents.for_the_verdict(answers).endswith("\n[… cut at 40 characters]")

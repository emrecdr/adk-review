"""The plugins on the App, which no agent can opt out of.

A callback is per agent and is where "this agent's tools are guarded" belongs. A
plugin is registered once on the `App` and applies to every agent, which is
where "we never send a credential to a provider", "we always know what a run
cost", "a run has a budget" and "a provider error is one guideline's answer, not the
graph's death" belong. `@override` marks each hook as one ADK calls by keyword:
a misnamed parameter would build fine and fail on the first live call. The
secret shapes themselves live in `core/secrets.py`, shared with the rule.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, override

from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types
from pydantic import BaseModel, ValidationError

from ..core.secrets import scrub, scrub_value
from ..core.text import REASON_MAX_CHARS, cut_at, squash
from .agents import BOUNDARY, FAILURE, mismatch
from .config import REQUEST_TIMEOUT_S, require_ready, said

#: Room reserved for an answer on top of a request's own size, in tokens; a guideline's typed review is well under it.
ANSWER_ALLOWANCE = 1_000


def _text_parts(llm_request: Any) -> Iterator[Any]:
    """Every part of the request's conversation that carries text; the instruction sits on its config, not here."""
    return (part for content in llm_request.contents or [] for part in content.parts or [] if part.text)


def describe(error: Exception) -> str:
    """The provider's error as one readable line: its code and its own sentence where it has them, else its text.

    genai raises a `ClientError` whose `str` is the whole JSON body and whose
    `.message` is the sentence a person wants — "You exceeded your current
    quota … Please retry in 19s"; LiteLLM's errors carry `.status_code` and a
    `.message` that wraps the provider's own; `config.said` reads both, the one
    place that knows a provider's format. One line, cut at `REASON_MAX_CHARS`,
    the cut named.
    """
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    message = said(error)
    if not message.strip() and isinstance(error, TimeoutError):  # Gemini's past its deadline says nothing of itself
        message = f"no answer within the call's deadline, {REQUEST_TIMEOUT_S:.0f} s"
    return cut_at(squash(f"{code}: {message}" if code else message), REASON_MAX_CHARS)


class RedactSecretsPlugin(BasePlugin):
    """Scrubs known secret shapes from every tool result and every outgoing request. Fail-closed."""

    def __init__(self, name: str = "redact_secrets") -> None:
        super().__init__(name=name)
        self.redacted = 0

    @override
    async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> LlmResponse | None:
        """Returning a response SKIPS the model call: if scrubbing fails, nothing unscrubbed is sent."""
        try:
            for part in _text_parts(llm_request):
                part.text, count = scrub(part.text)
                self.redacted += count
            instruction = llm_request.config.system_instruction
            if isinstance(instruction, str):
                llm_request.config.system_instruction, count = scrub(instruction)
                self.redacted += count
            elif instruction is not None:  # ADK 2.9.2 writes a string: any other shape is refused, never sent unread
                raise TypeError(f"the system instruction is a {type(instruction).__name__}; only a string is scrubbed")
        except Exception as exc:  # noqa: BLE001 -- a broken scrubber must not send unscrubbed text
            return LlmResponse(error_code="REDACTION_FAILED", error_message=f"refusing to call the model: {exc}")
        return None

    @override
    async def after_tool_callback(
        self, *, tool: Any, tool_args: Any, tool_context: Any, result: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Returning a dict REPLACES the tool's result before the model sees it."""
        scrubbed, count = scrub_value(result)
        self.redacted += count
        return scrubbed if count else None


def _answer(content: Any) -> str:
    """A reply's text as ADK reads it, and as a recording keeps it to serve again: every part that is not a thought,
    joined. One reading for both, so a replay serves what the schema was checked against."""
    parts = getattr(content, "parts", None) or ()
    return "".join(part.text for part in parts if part.text and not part.thought)


class ReportProviderErrors(BasePlugin):
    """A provider error is the guideline's own answer, not the graph's death.

    Measured on ADK 2.9.2 without this: an exception a guideline let escape tore
    the whole `Workflow` down and the runner re-raised it, so a sibling still
    in flight was lost with it. ADK's model-error hook runs when a call
    raises; returning a response yields it in the exception's place — the
    path the redaction plugin's refusal already takes — so the guideline ends with
    an error event under its own name, its `output_key` stays unwritten, and
    every other guideline finishes by construction. An agent's own
    `on_model_error_callback` answers for that agent alone; this is the same
    hook, once, on the App, so no agent can opt out. A call past the request
    deadline (`config.REQUEST_TIMEOUT_S`) arrives here the same way, after
    the client's own retries are spent.
    """

    def __init__(self, name: str = "provider_errors") -> None:
        super().__init__(name=name)
        self.schemas: dict[str, type[BaseModel]] = {}  # agent name -> the schema its request asked for

    @override
    async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> LlmResponse | None:
        schema = llm_request.config.response_schema
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            self.schemas[callback_context.agent_name] = schema
        return None

    @override
    async def after_model_callback(self, *, callback_context: Any, llm_response: Any) -> LlmResponse | None:
        """A reply that does not fit the schema its request asked for is the guideline's own error, as a provider's
        is. Measured on ADK 2.9.2 without this: ADK validated the reply and raised, the run ended, every guideline
        still in flight was lost, and each was named with the first one's error. A cut reply counts too: ADK's LiteLLM
        wrapper marks one with an error code and keeps its text, and ADK validates that text all the same. The text is
        read as ADK reads it: every part that is not a thought, a code fence around the whole of it stripped."""
        schema = self.schemas.get(callback_context.agent_name)
        text = _answer(llm_response.content) if schema is not None else ""
        if not text.strip():
            return None  # nothing ADK would validate
        try:
            schema.model_validate_json(_unfenced(text))
        except ValidationError as exc:
            reason = llm_response.finish_reason
            cut = reason not in (None, types.FinishReason.STOP)
            why = f"the reply was cut short ({getattr(reason, 'name', reason)})" if cut else exc.errors()[0]["msg"]
            return LlmResponse(
                error_code="SCHEMA_MISMATCH",
                error_message=mismatch(why),
                usage_metadata=llm_response.usage_metadata,
            )
        return None

    @override
    async def on_model_error_callback(
        self, *, callback_context: Any, llm_request: Any, error: Exception
    ) -> LlmResponse | None:
        status = getattr(error, "status", None)
        kind = status if isinstance(status, str) else type(error).__name__  # ADK's own rule for an error that escapes
        return _failed(callback_context, llm_request, kind, describe(error))


def _failed(callback_context: Any, llm_request: Any, kind: str, why: str) -> LlmResponse:
    """A call that failed, or could not be made, answered in the model's place. Two answers. An agent that answers in
    a schema — a guideline — answers nothing: its key stays unwritten, a hole the code that decides names and never
    approves over, where a review with no findings would read as an approval. An agent that answers in prose — the
    intake or the verdict, in chat — answers the sentence, as an agent's own hook did before, and leaves it under
    `temp:failure` for the guidelines' guard: ADK's invocation lifetime, so a later turn starts clean. The schema is
    read off the request; an agent given tools beside an `output_schema` gets a tool instead of a response schema on
    some models, and would count as prose."""
    if llm_request.config.response_schema is not None:
        return LlmResponse(error_code=kind, error_message=why)
    reason = f"{kind}: {why}"
    callback_context.state[FAILURE] = f"{callback_context.agent_name}: {reason}"
    return LlmResponse(
        content=types.ModelContent(f"I could not reach the model: {reason}"), error_code=kind, error_message=why
    )


#: Why the arm cannot run this turn, "" where it can: ADK's `temp:` state, which lasts one turn and is never saved.
NOT_READY = "temp:not_ready"


class RefuseUnready(BasePlugin):
    """Chat's readiness check, the one the command makes before it builds anything: while the arm cannot run, each
    model call of a turn is answered with `require_ready`'s sentence in the call's place, as a provider's error is.

    Measured by review: only the command asked. Chat on Copilot with no token
    cached let LiteLLM start its own device login on the server's console —
    three minutes of polling no one saw — and keep the token it got readable
    by whoever can read its folder, where the login script keeps it to its
    owner. Asked once a turn, so a login made meanwhile counts at the next, and
    in a thread: the Gemini arm's answer can run gcloud or ask a metadata
    server, and a client google-genai could not build, let go of where a loop
    runs, schedules a close that fails on what it never made.
    """

    def __init__(self, name: str = "refuse_unready") -> None:
        super().__init__(name=name)

    @override
    async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> LlmResponse | None:
        if NOT_READY not in callback_context.state:
            callback_context.state[NOT_READY] = await asyncio.to_thread(require_ready) or ""
        why = callback_context.state[NOT_READY]
        return _failed(callback_context, llm_request, "NOT_READY", why) if why else None


#: The one turn a judging agent reads beside its instruction: the command's own, which starts its run.
ONE_TURN = "review"


class OneTurn(BasePlugin):
    """Chat's judging agents read their instruction and one fixed turn, as the command's do: every untrusted text they
    judge or report sits inside the instruction's fences, never in a turn of its own.

    Measured before: ADK hands a node in a workflow its predecessor's output as
    a user turn, and keeps the turn's other agents' replies as context, so
    chat's verdict read every guideline's findings as raw JSON, the branch's
    quoted lines among them, outside the fences its instruction holds them in,
    and each guideline read the intake's reply. Set before the redaction, the
    debug log and the recorder, so each records the request as it is sent.
    """

    def __init__(self, agents: frozenset[str], name: str = "one_turn") -> None:
        super().__init__(name=name)
        self.agents = agents

    @override
    async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> LlmResponse | None:
        if callback_context.agent_name in self.agents:
            llm_request.contents = [types.UserContent(ONE_TURN)]
        return None


#: A code fence around a whole structured reply, which ADK strips before it validates the reply.
_FENCED = re.compile(r"```\w*\s*(.*?)\s*```", re.DOTALL)


def _unfenced(text: str) -> str:
    """`text` as ADK validates it: the payload of a code fence wrapping the whole of it, or the text as it is."""
    match = _FENCED.fullmatch(text.strip())
    return match.group(1).strip() if match else text


def request_hash(llm_request: Any) -> str:
    """A request as the model read it, secrets redacted, as one hash: its instruction, with the fences' nonce, drawn
    once a process, read as a placeholder, and the text of every turn. What a recorded answer is kept under and a
    replay finds it by."""
    instruction = str(llm_request.config.system_instruction or "").replace(BOUNDARY, "<boundary>")
    turns = [[part.text for part in (content.parts or []) if part.text] for content in (llm_request.contents or [])]
    said = json.dumps({"instruction": instruction, "turns": turns}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(said.encode("utf-8", "surrogatepass")).hexdigest()


class Recorder(BasePlugin):
    """Each model answer kept in `folder`, under the hash of the request that asked for it, with the model and the
    agent: a guideline's or a verifier's answer from a live run, which a fake replays by that hash, so a change to an
    instruction is measured offline against what a model said. After the redaction, so a request is hashed as the
    model read it, and before any plugin that answers in the model's place, so the answer kept is the model's own."""

    def __init__(self, folder: Path, name: str = "recorder") -> None:
        super().__init__(name=name)
        self.folder = folder
        self.asked: dict[str, tuple[str, str]] = {}  # agent -> its call in flight: the request's hash and the model

    @override
    async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> LlmResponse | None:
        self.asked[callback_context.agent_name] = (request_hash(llm_request), str(llm_request.model or ""))
        return None

    @override
    async def after_model_callback(self, *, callback_context: Any, llm_response: Any) -> LlmResponse | None:
        asked = self.asked.pop(callback_context.agent_name, None)
        content = getattr(llm_response, "content", None)
        if asked is None or content is None:  # an error, or a refusal before the call: no answer to keep
            return None
        answer = _answer(content)
        kept = {"request": asked[0], "model": asked[1], "agent": callback_context.agent_name, "answer": answer}
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)  # the operator's alone: answers quote the code
        (self.folder / f"{asked[0][:24]}.json").write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
        return None


class Progress(BasePlugin):
    """How long each agent took, and, given `say`, a line as each one the run names starts and finishes: a person at a
    terminal sees the review move. Measured before: nothing printed until the end, the first byte at 3.4 to 4.1 s of
    runs lasting 3.6 to 4.3 s. `named` maps an agent's name to the check id a line and the JSON use; an agent it
    leaves out, a verifier or the graph itself, is timed and never announced."""

    def __init__(
        self, name: str = "progress", named: Mapping[str, str] | None = None, say: Callable[[str], None] | None = None
    ) -> None:
        super().__init__(name=name)
        self.named = dict(named or {})
        self.say = say
        self.started: dict[str, float] = {}
        self.seconds: dict[str, float] = {}

    @override
    async def before_agent_callback(self, *, agent: Any, callback_context: Any) -> types.Content | None:
        self.started[agent.name] = time.monotonic()
        if self.say is not None and agent.name in self.named:
            self.say(f"{self.named[agent.name]} started")
        return None

    @override
    async def after_agent_callback(self, *, agent: Any, callback_context: Any) -> types.Content | None:
        took = time.monotonic() - self.started.pop(agent.name, time.monotonic())
        self.seconds[agent.name] = round(took, 2)
        if self.say is not None and agent.name in self.named:
            self.say(f"{self.named[agent.name]} done in {took:.1f} s")
        return None

    def summary(self) -> dict[str, float]:
        """Each agent's seconds, for the JSON report, by the name the report uses."""
        return {self.named.get(name, name): seconds for name, seconds in sorted(self.seconds.items())}


class UsageLedger(BasePlugin):
    """Counts model calls and tokens per agent — and, given a ceiling, refuses the call that would pass it.

    Three guidelines call at once, so a ceiling checked against tokens already
    spent would let all three through and be passed by all three. Every call
    therefore reserves its own cost first — its request in characters over
    four, plus room for an answer — and releases it when its response comes
    back, an error's included: ADK hands the after-hook what the error hook
    substituted for an exception, so one hook sees every call end. A response
    carrying no usage was never billed — substituted, or refused before the
    call — and is attempted, not completed; a blocked answer carries the usage
    the provider charged, and counts. The refusal is the redaction plugin's shape: an error
    response the guideline ends with, under its own name, so the run degrades and
    reports what was found. A ceiling ends the spending, never the report.
    """

    def __init__(
        self, name: str = "usage_ledger", ceiling: int | None = None, named: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(name=name)
        self.ceiling = ceiling
        #: An agent's name to the check id a row and the JSON use, as `Progress` holds it; a verifier keeps its own.
        self.named = dict(named or {})
        self.rows: dict[str, dict[str, int]] = {}
        self.reserved: dict[str, int] = {}  # agent name -> tokens reserved for the call it has in flight

    def _row(self, context: Any) -> dict[str, int]:
        return self.rows.setdefault(context.agent_name, {"attempted": 0, "calls": 0, "tokens": 0})

    @property
    def spent(self) -> int:
        return sum(r["tokens"] for r in self.rows.values())

    @staticmethod
    def _estimate(llm_request: Any) -> int:
        """What a request will cost, before it is sent: its text over four, an answer's worth, and the thinking its
        budget allows, which Gemini bills and the provider's total counts. Measured by review: without it, `default`'s
        three guidelines in flight could pass the ceiling by about 7,000 tokens. A budget left to the model, -1, is
        no number to reserve."""
        chars = len(str(llm_request.config.system_instruction or ""))
        chars += sum(len(part.text) for part in _text_parts(llm_request))
        thinking = getattr(llm_request.config.thinking_config, "thinking_budget", None) or 0
        return chars // 4 + ANSWER_ALLOWANCE + max(thinking, 0)

    @override
    async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> LlmResponse | None:
        self._row(callback_context)["attempted"] += 1  # a call that never returns is attempted, not billed
        if self.ceiling is None:
            return None
        estimate, in_flight = self._estimate(llm_request), sum(self.reserved.values())
        if self.spent + in_flight + estimate > self.ceiling:
            return LlmResponse(
                error_code="TOKEN_CEILING",
                error_message=(
                    f"the ceiling of {self.ceiling:,} tokens would be passed: {self.spent:,} spent, "
                    f"{in_flight:,} in flight, this call about {estimate:,}; concluding with what was found"
                ),
            )
        self.reserved[callback_context.agent_name] = estimate
        return None

    @override
    async def after_model_callback(self, *, callback_context: Any, llm_response: Any) -> LlmResponse | None:
        self.reserved.pop(callback_context.agent_name, None)
        usage = llm_response.usage_metadata
        if usage is None:  # substituted for an exception, or refused before the call: over, never billed
            return None
        row = self._row(callback_context)
        row["calls"] += 1
        # The provider's own total when it reports one; the parts this code knows to read otherwise.
        prompt, candidates = usage.prompt_token_count or 0, usage.candidates_token_count or 0
        row["tokens"] += usage.total_token_count or (prompt + candidates)
        return None

    def summary(self) -> dict:
        """The spend as data, for the JSON report: the totals, each agent's row and the ceiling, if any, each row under
        the name the report uses, a check's id for its agent, since ADK names an agent by an identifier and the report
        by an id."""
        return {
            "calls": sum(r["calls"] for r in self.rows.values()),
            "attempted": sum(r["attempted"] for r in self.rows.values()),
            "tokens": self.spent,
            "ceiling": self.ceiling,
            "by_agent": {self.named.get(name, name): dict(row) for name, row in sorted(self.rows.items())},
        }

    def render(self) -> str:
        """The spend as the text report prints it, from the same numbers the JSON report carries."""
        spend = self.summary()
        calls, attempted, tokens = spend["calls"], spend["attempted"], spend["tokens"]
        lines = [f"- spend: {calls} model call(s) completed of {attempted} attempted, {tokens:,} tokens"]
        lines += [
            f"  - `{name}`: {r['calls']} call(s), {r['tokens']:,} tokens" for name, r in spend["by_agent"].items()
        ]
        if spend["ceiling"] is not None:
            lines.append(f"- ceiling: {spend['ceiling']:,} tokens")
        return "\n".join(lines)

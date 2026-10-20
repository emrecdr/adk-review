"""One seeded run through ADK's engine, guarded, which the guidelines' graph and the verifiers' share; and the
command's reading back of what the guidelines' run left in state.

A session is created with the state the agents' instructions read, one user
message starts the run, every event is drained, and the state is read back
whatever happened. An agent that failed ends with an error event under its
own name — the plugins see to that — and its output unwritten; anything that
still escapes the engine is caught here and returned as the run's failure, so
what finished is still in the state and what failed is named, never a
traceback and Python's exit 1, which is the command's code for "findings
found".
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from google.adk.runners import InMemoryRunner
from google.genai import types

from ..core.evidence import Entry, entry
from ..core.findings import Checked, Review
from ..core.profiles import state_key
from ..core.rules import AnyRule
from ..core.verdict import Outcome, Sourced, decide, drop_ungrounded, fold, from_rules, not_read
from .agents import FINDINGS, IGNORED, QUOTABLE, for_the_verdict, read_back, unjudged
from .checks import NAME as CHECKS
from .config import RUN_DEADLINE_S

if TYPE_CHECKING:
    from google.adk.agents.context import Context
    from google.adk.workflow import FunctionNode

    from .catalog import Catalog

USER = "reviewer"


@dataclass(frozen=True)
class Run:
    """What one run left behind: the state, the error each agent ended with, and the engine's own failure."""

    state: dict
    errors: dict[str, str]  # agent name -> "<code>: <message>", from the error event it ended with
    failure: str | None  # what escaped the engine, if anything did

    def why(self, agent: str, default: str) -> str:
        """Why `agent` wrote nothing: its own error event, else the engine's failure, else `default`."""
        return self.errors.get(agent) or self.failure or default


async def run_seeded(runner: InMemoryRunner, *, session_id: str, state: dict, prompt: str) -> Run:
    """One run over a session seeded with `state`, started by `prompt`."""
    ids = {"app_name": runner.app_name, "user_id": USER, "session_id": session_id}
    await runner.session_service.create_session(**ids, state=state)
    message = types.UserContent(prompt)
    errors: dict[str, str] = {}
    failure: str | None = None
    try:
        # Past it, the run's failure: every guideline still out is named with it.
        async with asyncio.timeout(RUN_DEADLINE_S):
            async for event in runner.run_async(user_id=USER, session_id=session_id, new_message=message):
                if event.error_code:
                    errors[event.author] = f"{event.error_code}: {event.error_message}"
    except TimeoutError:
        failure = f"the run did not finish within {RUN_DEADLINE_S:g} s"
    except Exception as exc:  # noqa: BLE001 -- the engine itself; an agent's own failure never gets here
        failure = f"{type(exc).__name__}: {exc}"
    session = await runner.session_service.get_session(**ids)
    if session is None:  # the service lost the session it made: every guideline still out is named with it
        return Run({}, errors, failure or "the run's session was gone when its answers were read")
    return Run(dict(session.state), errors, failure)


def read_checks(run: Run, rules: Sequence[AnyRule]) -> tuple[list[Sourced], dict[str, str]]:
    """What the checks node left in state, read back as each guideline's review is: each rule's answer, or, as a
    guideline that did not answer is, why there is none."""
    answers = {}
    for rule in rules:
        answer = read_back(run.state, state_key(rule.check_id), Checked, run.why(CHECKS, "the check left no answer"))
        answers[rule.check_id] = Checked(could_not_look=answer) if isinstance(answer, str) else answer
    return from_rules(answers)


def read_guidelines(run: Run, files: list[Entry], guidelines: Sequence[str], holes: Mapping[str, str]) -> Outcome:
    """The guidelines' answers read back and grounded, each narrowing counted: what a model said that the diff bears
    out. `holes` are the checks that could not look before any model ran, named beside the guidelines that could not.

    A guideline that wrote nothing is named with why: the error event it ended
    with — its provider raised or timed out, the ceiling refused its call, the
    redaction plugin refused it — or, failing that, what escaped the engine,
    which names every guideline that wrote nothing.
    """
    outcome = Outcome(unread=not_read(files, guidelines), errors=dict(holes))
    quotable = {f["path"]: f["quotable"] for f in files}
    framing = {f["path"]: f["framing"] for f in files}
    for check in guidelines:  # a guideline the profile left off did not fail: it was never asked
        key = state_key(check)
        answer = read_back(run.state, key, Review, run.why(key, "the guideline produced no output"))
        if isinstance(answer, str):
            outcome.errors[check] = answer
            continue
        kept, dropped = drop_ungrounded(answer, quotable, framing)
        outcome.kept += [Sourced(check, f, lines) for f, lines in kept]
        outcome.dropped += [Sourced(check, f) for f in dropped]
    return outcome


def build_grounding(checks: Catalog, fail_on: str) -> FunctionNode:
    """Chat's node before its verdict, reading what the checks and the guidelines left as the command reads it once a
    run ends: each rule's answer, each guideline's grounded in the diff, the fold, and the verdict decided in code, all
    rendered under `FINDINGS` for the verdict agent, which writes them up and decides nothing. The turn's own state and
    each agent's error event of this turn stand in for a run's. Measured by review: chat's verdict agent read each
    guideline's answer as its model gave it and decided the verdict itself, a blocker quoting no line of the change
    reported as one, where the command dropped it and held the verdict back."""
    from google.adk.workflow import FunctionNode

    async def ground(ctx: Context) -> None:
        # Only what this turn wrote: a guideline's answer stays in the session from one turn to the next, and read by
        # review, one that failed in a later turn read as its earlier answer, and code approved what no guideline
        # judged that turn.
        turn = [event for event in ctx.session.events if event.invocation_id == ctx.invocation_id]
        errors = {e.author: f"{e.error_code}: {e.error_message}" for e in turn if e.error_code}
        written = {key for event in turn for key in event.actions.state_delta or {}}
        state = {key: value for key, value in ctx.state.to_dict().items() if key in written}
        # And a guideline its guard skipped could not look: read by review, its answer, a review naming no finding,
        # read as nothing found, and code approved with no guideline having judged anything.
        if why := unjudged(ctx.state):
            for guideline in checks.guidelines:
                state.pop(key := state_key(guideline.check_id), None)
                errors[key] = why
        ctx.state[FINDINGS] = decided(state, errors, checks, fail_on)

    return FunctionNode(func=ground, name="grounded")


def decided(state: Mapping, errors: Mapping[str, str], checks: Catalog, fail_on: str) -> str:
    """Every check's answer as the command reads it from `state` and the agents' `errors`: each rule's, each
    guideline's grounded in the files `QUOTABLE` holds, the fold, and the verdict decided at `fail_on`, rendered for
    chat's verdict agent."""
    run = Run(dict(state), dict(errors), None)
    files = []
    for path, held in (state.get(QUOTABLE) or {}).items():
        made = entry(path, "M", None, [])
        made.update(quotable=held["quotable"], framing=held["framing"], unread=held["unread"], cut=held["cut"])
        files.append(made)
    rules, holes = read_checks(run, checks.rules)
    outcome = read_guidelines(run, files, tuple(guideline.check_id for guideline in checks.guidelines), holes)
    fold(outcome, rules, checks.ids(), checks.tidies())
    verdict, _code = decide(outcome, fail_on)
    return for_the_verdict(outcome, checks.ids(), verdict, state.get(IGNORED) or ())

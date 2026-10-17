"""One seeded run through ADK's engine, guarded, and what it left in state read back: what the guidelines' graph and
the verifiers' share.

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

from google.adk.runners import InMemoryRunner
from google.genai import types

from ..core.evidence import Entry
from ..core.findings import Checked, Review
from ..core.profiles import state_key
from ..core.rules import AnyRule
from ..core.verdict import Outcome, Sourced, drop_ungrounded, from_rules, not_read
from .agents import read_back
from .checks import NAME as CHECKS
from .config import RUN_DEADLINE_S

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
    quotable, framing = ({f["path"]: f[key] for f in files} for key in ("quotable", "framing"))
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

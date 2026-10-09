"""One seeded run through ADK's engine, guarded: what the guidelines' graph and the verifiers' share.

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
from dataclasses import dataclass

from google.adk.runners import InMemoryRunner
from google.genai import types

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

"""Every call the front end makes to Azure DevOps, and the login it makes them with: the person's own Azure CLI login,
its token asked of `az` and kept in memory alone. `azure.py` holds what is sent and where.

Nothing here runs unless the person asks: a comment is posted on their click, after their edit and a confirmation. No
secret is written to a file, a log or an error's text. No text a person, a repository or a model wrote is an argument
of `az`: on Windows it is `az.cmd`, which runs through cmd.exe, and Python escapes nothing for that shell."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from typing import Protocol

from adk_review.collect.git import on_path

#: Azure DevOps' own application in Microsoft Entra: the resource `az` is asked a token for.
RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
#: `az` starts a Python of its own, and may refresh the login over the network.
AZ_TIMEOUT_S = 30
#: A token is asked again this long before it expires, so none expires on its way.
_EARLY_S = 120


class AzureError(Exception):
    """What went wrong and what to do next, as sentences for the person. Its text never holds a token."""


class Credential(Protocol):
    """A login the client asks for each call's `Authorization` header."""

    def __call__(self) -> str:
        """The header's value."""
        ...

    def forget(self) -> None:
        """Azure DevOps refused the login: the next call asks for it afresh."""
        ...


class AzLogin:
    """The login `az login` made, as a `Credential`: a token from `az account get-access-token`, kept until two
    minutes before it expires. A plain class, never a dataclass: its text names no field, and so no token."""

    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._run, self._clock = run, clock
        self._token, self._until = "", 0.0

    def __call__(self) -> str:
        if self._clock() >= self._until:
            self._token, self._until = self._asked()
        return f"Bearer {self._token}"

    def forget(self) -> None:
        self._token, self._until = "", 0.0

    def _asked(self) -> tuple[str, float]:
        """A token and the time to ask for the next one, from `az` by its full path, found on PATH's absolute
        entries alone as the reviewer finds git: Windows looks for a bare name in the current folder first."""
        az = on_path("az")
        if az == "az":
            raise AzureError("The Azure CLI is not on PATH. Install it, then run `az login`.")
        argv = [az, "account", "get-access-token", "--resource", RESOURCE, "--output", "json"]
        try:
            done = self._run(
                argv,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,  # a login that needs the person asks in their own terminal
                timeout=AZ_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            said = f"The Azure CLI did not answer within {AZ_TIMEOUT_S} s. Run `az account show` to check it."
            raise AzureError(said) from None
        except OSError as exc:
            raise AzureError(f"The Azure CLI could not start: {exc.strerror or type(exc).__name__}.") from None
        return _token(done)


def _token(done: subprocess.CompletedProcess[str]) -> tuple[str, float]:
    """The token `az` printed and when to ask again, or the sentence that names the login. What `az` said is never
    repeated: it is the one text here that can hold a token."""
    try:
        answer = json.loads(done.stdout or "") if done.returncode == 0 else None
    except ValueError:
        answer = None
    token = answer.get("accessToken") if isinstance(answer, dict) else None
    if not isinstance(token, str) or not token.isascii() or not token.isprintable() or " " in token or not token:
        raise AzureError("The Azure CLI gave no token. Run `az login`, then post again.")
    expires = answer.get("expires_on") if isinstance(answer, dict) else None
    return token, (expires - _EARLY_S if type(expires) is int else 0.0)

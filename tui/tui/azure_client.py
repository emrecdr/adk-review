"""Every call the front end makes to Azure DevOps, and the login it makes them with: the person's own Azure CLI login,
its token asked of `az` and kept in memory alone. `azure.py` holds what is sent and where.

Nothing here runs unless the person asks: a comment is posted on their click, after their edit and a confirmation. No
secret is written to a file, a log or an error's text. No text a person, a repository or a model wrote is an argument
of `az`: on Windows it is `az.cmd`, which runs through cmd.exe, and Python escapes nothing for that shell."""

from __future__ import annotations

import json
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
from http import HTTPStatus
from typing import Protocol
from urllib.parse import urlencode

import truststore

from adk_review.collect.git import on_path

from .azure import Place, PullRequest, marked, thread_payload

#: Azure DevOps' own application in Microsoft Entra: the resource `az` is asked a token for.
RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
#: `az` starts a Python of its own, and may refresh the login over the network.
AZ_TIMEOUT_S = 30
#: A token is asked again this long before it expires, so none expires on its way.
_EARLY_S = 120
#: The REST version every call names: Azure DevOps answers each version in its own shape.
API_VERSION = "7.1"
HTTP_TIMEOUT_S = 20
#: The longest wait a 429 is given before the person is told instead: Azure DevOps delays a call up to 30 s itself.
RETRY_AFTER_MAX_S = 30
#: The changes of an iteration, asked this many a page: the most Azure DevOps gives.
_PAGE = 2000
#: A Microsoft Entra tenant's id, as Azure DevOps names the organization's in `X-VSS-ResourceTenant`. Only this
#: shape is repeated to the person: the header is a server's text, and the sentence names a command to run.
_TENANT = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
#: Where a call may go: Azure DevOps over HTTPS, or this machine's own address, where the tests' fake answers.
_HTTPS, _LOCAL = "https://", "http://127.0.0.1:"
#: A server's own words in a sentence: one line, this long at most.
_SAID_MAX_CHARS = 200


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """No redirect is followed: urllib sends every header again to a redirect's target, `Authorization` among them,
    and a sign-in page is one redirect away, on another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl) -> None:  # noqa: ARG002 -- urllib's own
        return None


class Client:
    """One pull request of Azure DevOps Services, over HTTPS: certificates verified through the operating system's
    store, as the reviewer verifies its own, so a company proxy's root the machine trusts is trusted here. Each
    refusal is an `AzureError` whose text names what to do next."""

    def __init__(self, pr: PullRequest, credential: Credential, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._pr, self._credential, self._sleep = pr, credential, sleep
        secure = urllib.request.HTTPSHandler(context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        self._opener = urllib.request.build_opener(secure, _NoRedirect())
        self._changed: dict[int, dict[str, int]] = {}  # an iteration's files, read once: its changes never change

    def status(self) -> str:
        """Whether the pull request is `active`, `completed` or `abandoned`."""
        return str(self._call("GET", self._pr.api()).get("status") or "unknown")

    def iterations(self) -> list[tuple[int, str]]:
        """Each push the pull request holds, oldest first: its number and its source commit."""
        pushed = []
        for entry in _dicts(self._call("GET", self._pr.api("iterations")).get("value")):
            commit = (entry.get("sourceRefCommit") or {}).get("commitId")
            if type(entry.get("id")) is int and isinstance(commit, str):
                pushed.append((entry["id"], commit))
        return sorted(pushed)

    def change_of(self, iteration: int, path: str) -> int | None:
        """The id Azure DevOps tracks `path`'s change by across pushes, or None where the pull request does not
        change the file: a thread on a file's line must name it."""
        if iteration not in self._changed:
            self._changed[iteration] = self._changes(iteration)
        return self._changed[iteration].get("/" + path.lstrip("/"))

    def _changes(self, iteration: int) -> dict[str, int]:
        found: dict[str, int] = {}
        skip = 0
        while True:
            page = {"$top": str(_PAGE), "$skip": str(skip)}
            answer = self._call("GET", self._pr.api("iterations", str(iteration), "changes"), query=page)
            for entry in _dicts(answer.get("changeEntries")):
                changed = (entry.get("item") or {}).get("path")
                if isinstance(changed, str) and type(entry.get("changeTrackingId")) is int:
                    found[changed] = entry["changeTrackingId"]
            onward = answer.get("nextSkip")
            if type(onward) is not int or onward <= skip:  # no later page, or one that does not move on
                return found
            skip = onward

    def thread_of(self, fingerprint: str) -> int | None:
        """A thread whose comment carries this finding's mark, anyone's: a teammate's post of it is a duplicate
        too. A deleted thread or comment is none."""
        if not fingerprint:
            return None
        for thread in _dicts(self._call("GET", self._pr.api("threads")).get("value")):
            if thread.get("isDeleted") or type(thread.get("id")) is not int:
                continue
            for comment in _dicts(thread.get("comments")):
                said = comment.get("content")
                if not comment.get("isDeleted") and isinstance(said, str) and marked(said) == fingerprint:
                    return thread["id"]
        return None

    def post(self, payload: dict) -> int:
        """`payload` posted as a new thread: its id."""
        thread = self._call("POST", self._pr.api("threads"), body=payload).get("id")
        if type(thread) is not int:
            raise AzureError("Azure DevOps answered with no thread. Check the pull request before you post again.")
        return thread

    def _call(self, method: str, url: str, *, body: dict | None = None, query: Mapping[str, str] | None = None) -> dict:
        """One call's answer, a JSON object. A 429 is waited out once, as `Retry-After` says: Azure DevOps refused
        the call and did nothing. Nothing is sent again after a failure of the network, where a post may have
        arrived."""
        full = f"{url}?{urlencode({**(query or {}), 'api-version': API_VERSION})}"
        if not full.startswith((_HTTPS, _LOCAL)):  # the second lock: a pasted address is HTTPS by the time it is read
            raise AzureError(
                "This is not Azure DevOps' own address over HTTPS. Paste the pull request's address again."
            )
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": self._credential(), "Accept": "application/json", "User-Agent": "adk-review-tui"}
        if data is not None:
            headers["Content-Type"] = "application/json"  # UTF-8, as JSON is by its own standard
        for waited in (False, True):
            request = _request(full, data, headers, method)
            try:
                with self._opener.open(request, timeout=HTTP_TIMEOUT_S) as answer:
                    return self._answered(answer.status, answer.read())
            except urllib.error.HTTPError as refused:
                self._sleep(self._wait(refused, waited=waited))
            except (urllib.error.URLError, OSError) as failed:  # no route, no answer in time, a certificate
                raise AzureError(_unreached(failed)) from None
        raise AssertionError("a second 429 is a sentence")

    def _answered(self, status: int, raw: bytes) -> dict:
        """A 2xx's JSON object. A 203, or any other body, is Azure DevOps' sign-in page: it answers a login it does
        not accept with one, where another service would answer 401."""
        try:
            answer = json.loads(raw) if status != HTTPStatus.NON_AUTHORITATIVE_INFORMATION else None
        except ValueError:
            answer = None
        if not isinstance(answer, dict):
            raise self._no_login(None)
        return answer

    def _wait(self, refused: urllib.error.HTTPError, *, waited: bool) -> float:
        """The seconds to wait before one more try, or the refusal as its sentence."""
        code = refused.code
        if code == HTTPStatus.UNAUTHORIZED or HTTPStatus.MULTIPLE_CHOICES <= code < HTTPStatus.BAD_REQUEST:
            raise self._no_login(refused.headers) from None  # refused, or sent elsewhere to sign in
        if code == HTTPStatus.FORBIDDEN:
            said = 'Your account may not comment here. Ask for the "Contribute to pull requests" permission.'
            raise AzureError(said) from None
        if code == HTTPStatus.NOT_FOUND:  # or not this account's to see
            said = "Azure DevOps found no such pull request. Check the address, and that your account can read it."
            raise AzureError(said) from None
        if code == HTTPStatus.TOO_MANY_REQUESTS:
            asked = (refused.headers.get("Retry-After") or "").strip()
            seconds = int(asked) if asked.isascii() and asked.isdigit() else 60
            if waited or seconds > RETRY_AFTER_MAX_S:
                raise AzureError(f"Azure DevOps asked to wait {seconds} s. Post again after that.") from None
            return float(seconds)
        raise AzureError(f"Azure DevOps answered {code}: {_said(refused)}. Nothing was posted.") from None

    def _no_login(self, headers: Message | None) -> AzureError:
        """The sentence for a login Azure DevOps did not accept, the token forgotten: the person logs in again, and
        the one kept in memory is the one just refused. The organization's tenant is named where the answer names
        one, since a login in another tenant is the usual cause."""
        self._credential.forget()
        tenant = (headers.get("X-VSS-ResourceTenant") or "").strip().lower() if headers is not None else ""
        named = f" --tenant {tenant}" if _TENANT.fullmatch(tenant) and tenant.strip("0-") else ""
        return AzureError(f"Azure DevOps did not accept your login. Run `az login{named}`, then post again.")


def _request(full: str, data: bytes | None, headers: Mapping[str, str], method: str) -> urllib.request.Request:
    """The request for `full`, which `_call` has read as one of the two roads a token may take. Each is spelled with
    its scheme as written here, so no address, whatever built it, opens a file or another scheme."""
    if full.startswith(_HTTPS):
        return urllib.request.Request(
            f"https://{full.removeprefix(_HTTPS)}", data=data, headers=dict(headers), method=method
        )
    return urllib.request.Request(
        f"http://127.0.0.1:{full.removeprefix(_LOCAL)}", data=data, headers=dict(headers), method=method
    )


@dataclass(frozen=True)
class Posted:
    """A finding's thread in the pull request: its id, and whether it was there already, so nothing was posted."""

    thread: int
    already: bool


def post_comment(client: Client, *, text: str, place: Place, commit: str, fingerprint: str) -> Posted:
    """`text` posted at `place` in the pull request, once: the checks that cost one call first, each refusal an
    `AzureError` and nothing posted. The commit reviewed must be the pull request's latest push: the
    lines are counted in it, and a commit names its repository too, so no remote is compared. A thread that carries
    the finding's mark already is answered instead of a second one."""
    status = client.status()
    if status != "active":
        raise AzureError(f"The pull request is {' '.join(status.split())[:40]}. Nothing was posted.")
    pushed = client.iterations()
    if not pushed or pushed[-1][1] != commit:
        if any(held == commit for _number, held in pushed):
            raise AzureError(
                f"The pull request has a newer push, {pushed[-1][1][:7]}. Fetch, review again, then comment."
            )
        raise AzureError(f"The pull request never held commit {commit[:7]}. Check the address and the branch reviewed.")
    iteration = pushed[-1][0]
    if (thread := client.thread_of(fingerprint)) is not None:
        return Posted(thread, already=True)
    if (change := client.change_of(iteration, place.path)) is None:
        raise AzureError(f"The pull request does not change {place.path}. No line can hold the comment.")
    return Posted(client.post(thread_payload(text, place, iteration, change)), already=False)


def _dicts(value: object) -> list[dict]:
    """The objects of a list Azure DevOps answered, whatever else it holds."""
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def _said(refused: urllib.error.HTTPError) -> str:
    """Azure DevOps' own message for a refusal, else the status's words: one line, cut."""
    try:
        told = json.loads(refused.read())
    except ValueError, OSError:
        told = None
    message = told.get("message") if isinstance(told, dict) else None
    words = " ".join((message if isinstance(message, str) and message.strip() else refused.reason).split())
    return words[:_SAID_MAX_CHARS].rstrip(".")


def _unreached(failed: OSError) -> str:
    """The sentence for a call that got no answer, with what to check."""
    reason = getattr(failed, "reason", failed)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return (
            "Azure DevOps' certificate was not trusted. "
            "Set SSL_CERT_FILE to a PEM bundle that holds your proxy's root, then press c again."
        )
    why = f"no answer within {HTTP_TIMEOUT_S} s" if isinstance(reason, TimeoutError) else " ".join(str(reason).split())
    return f"Azure DevOps did not answer: {why[:_SAID_MAX_CHARS]}. Check the network and the proxy, then press c again."

"""Every call the front end makes to Azure DevOps, and the login it makes them with: the person's own Azure CLI login,
its token asked of `az` and kept in memory alone. `azure.py` holds what is sent and where.

Nothing here runs unless the person asks: a comment is posted on their click, after their edit and a confirmation. No
secret is written to a file, a log or an error's text. No text a person, a repository or a model wrote is an argument
of `az`: on Windows it is `az.cmd`, which runs through cmd.exe, and Python escapes nothing for that shell."""

from __future__ import annotations

import http.client
import json
import os
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
from adk_review.core.text import squash
from adk_review.deliver.threads import OPEN_STATUSES, OpenThread

from .azure import Place, PullRequest, posted_as, rooted, thread_payload
from .git_sync import bounded

#: Azure DevOps' own application in Microsoft Entra: the resource `az` is asked a token for.
RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
#: `az` starts a Python of its own, and may refresh the login over the network.
AZ_TIMEOUT_S = 30
#: A token is asked again this long before it expires, so none expires on its way.
_EARLY_S = 120
#: How long a token is kept where `az` names no second it expires at, as an older one names none: `az` gives no token
#: with less than five minutes left.
_UNDATED_S = 180
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
    """What went wrong and what to do next, as sentences for the person. Its text never holds a token. `unsure`
    says a post may have arrived all the same: its answer did not come back whole, or a gateway answered for Azure
    DevOps. The screen then never says "not posted"."""

    def __init__(self, said: str, *, unsure: bool = False) -> None:
        super().__init__(said)
        self.unsure = unsure


class Credential(Protocol):
    """A login the client asks for each call's `Authorization` header."""

    def __call__(self) -> str:
        """The header's value."""
        ...

    def forget(self) -> None:
        """Azure DevOps refused the login: the next call asks for it afresh."""
        ...


def _az(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """The Azure CLI run with the person's own environment and nothing to read, and stopped whole past its timeout:
    `az` starts a Python of its own, which a stop of `az.cmd` alone leaves running, the screen waiting on it."""
    return bounded(argv, dict(os.environ), AZ_TIMEOUT_S, stdin=subprocess.DEVNULL)


class AzLogin:
    """The login `az login` made, as a `Credential`: a token from `az account get-access-token`, kept until two
    minutes before it expires. A plain class, never a dataclass: its text names no field, and so no token."""

    def __init__(
        self,
        *,
        run: Callable[[list[str]], subprocess.CompletedProcess[str]] = _az,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._run, self._clock = run, clock
        self._token, self._until = "", 0.0

    def __call__(self) -> str:
        if self._clock() >= self._until:
            self._token, expires = self._asked()
            self._until = self._clock() + _UNDATED_S if expires is None else expires - _EARLY_S
        return f"Bearer {self._token}"

    def forget(self) -> None:
        self._token, self._until = "", 0.0

    def _asked(self) -> tuple[str, int | None]:
        """A token and the second it expires at, where `az` names one, from `az` by its full path, found on PATH's
        absolute entries alone as the reviewer finds git: Windows looks for a bare name in the current folder first."""
        az = on_path("az")
        if az == "az":
            raise AzureError("The Azure CLI is not on PATH. Install it, then run `az login`.")
        argv = [az, "account", "get-access-token", "--resource", RESOURCE, "--output", "json"]
        try:
            done = self._run(argv)  # a login that needs the person asks in their own terminal, never here
        except subprocess.TimeoutExpired:
            said = f"The Azure CLI did not answer within {AZ_TIMEOUT_S} s. Run `az account show` to check it."
            raise AzureError(said) from None
        except OSError as exc:
            raise AzureError(f"The Azure CLI could not start: {exc.strerror or type(exc).__name__}.") from None
        return _token(done)


def _token(done: subprocess.CompletedProcess[str]) -> tuple[str, int | None]:
    """The token `az` printed and the second it expires at, or the sentence that names the login. What `az` said is
    never repeated: it is the one text here that can hold a token."""
    answer = (_object(done.stdout or "") if done.returncode == 0 else None) or {}
    token = answer.get("accessToken")
    if not isinstance(token, str) or not token.isascii() or not token.isprintable() or " " in token or not token:
        raise AzureError("The Azure CLI gave no token. Run `az login`, then post again.")
    expires = answer.get("expires_on")
    return token, (expires if type(expires) is int else None)


def _opener() -> urllib.request.OpenerDirector:
    """What makes each call: HTTP and HTTPS through the machine's own proxy settings, and nothing else urllib would
    add by itself. No handler follows a redirect, so a 3xx is an error like any other: urllib sends every header
    again to a redirect's target, `Authorization` among them, and a sign-in page is one redirect away, on another
    host. None opens a file, an FTP address or a `data:` one. Certificates are verified through the operating
    system's store, as the reviewer verifies its own, so a company proxy's root the machine trusts is trusted here."""
    opener = urllib.request.OpenerDirector()
    trusted = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # As `ssl.create_default_context` does for the reviewer's own calls: without it `SSL_CERT_FILE` names a bundle on
    # Linux alone, and the sentence that tells a person to set it is no help on macOS or Windows.
    trusted.load_default_certs()
    secure = urllib.request.HTTPSHandler(context=trusted)
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.HTTPHandler(),
        secure,
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


@dataclass(frozen=True)
class PullRequestHead:
    """What a review of a pull request reads of it: its branches, the commit its last push brought, its status, and
    whether its source is a fork's branch, in another repository."""

    source: str
    target: str
    commit: str
    status: str
    fork: bool


def _author(comment: dict) -> str:
    """A comment's author by the name Azure DevOps shows."""
    name = (comment.get("author") or {}).get("displayName")
    return name if isinstance(name, str) else ""


def _place(context: object) -> tuple[str, int | None]:
    """The file a thread is on, without the leading `/` Azure DevOps writes, and its line on the new side of the
    diff, else on the old; an empty file and no line for a thread on the whole pull request."""
    if not isinstance(context, dict) or not isinstance(context.get("filePath"), str):
        return "", None
    lines = [(context.get(side) or {}).get("line") for side in ("rightFileStart", "leftFileStart")]
    line = next((n for n in lines if type(n) is int and n > 0), None)
    return context["filePath"].lstrip("/"), line


def _branch(ref: object) -> str | None:
    """A branch's name, as git names it under `refs/heads/`, or None for any other ref."""
    return ref.removeprefix("refs/heads/") if isinstance(ref, str) and ref.startswith("refs/heads/") else None


class Client:
    """One pull request of Azure DevOps Services, over HTTPS. Each refusal is an `AzureError` whose text names what
    to do next."""

    def __init__(self, pr: PullRequest, credential: Credential, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._pr, self._credential, self._sleep = pr, credential, sleep
        self._opener: urllib.request.OpenerDirector | None = None  # built at the first call, off the screen's loop
        self._changed: dict[int, dict[str, int]] = {}  # an iteration's files, read once: its changes never change

    def pull_request(self) -> PullRequestHead:
        """The pull request's source and target branches, the commit its last push brought, its status, and whether
        it comes from a fork: what a review of it reads. A branch is named as git names it under `refs/heads/`."""
        answer = self._call("GET", self._pr.api())
        source, target = (_branch(answer.get(key)) for key in ("sourceRefName", "targetRefName"))
        if source is None or target is None:
            raise AzureError("Azure DevOps named no branch this pull request merges. Choose its branches by hand.")
        commit = (answer.get("lastMergeSourceCommit") or {}).get("commitId")
        return PullRequestHead(
            source,
            target,
            commit if isinstance(commit, str) else "",
            str(answer.get("status") or "unknown"),
            bool(answer.get("forkSource")),
        )

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
        return self._changed[iteration].get(rooted(path))

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

    def threads(self) -> list[tuple[int, list[str]]]:
        """The pull request's live threads, each its number and its comments' words in order: a deleted thread or
        comment is none. `azure.posted_as` says which is a finding's."""
        live = []
        for thread in _dicts(self._call("GET", self._pr.api("threads")).get("value")):
            if not thread.get("isDeleted") and type(thread.get("id")) is int:
                kept = [comment for comment in _dicts(thread.get("comments")) if not comment.get("isDeleted")]
                live.append((thread["id"], [one["content"] for one in kept if isinstance(one.get("content"), str)]))
        return live

    def open_threads(self) -> list[OpenThread]:
        """The pull request's threads still open, active or pending, as people wrote them: each comment not deleted by
        its author's name, and the file and line it is on, from either side of the diff, none for one on the whole pull
        request. A resolved or deleted thread, and one of the system's own, a vote or a push, are left out."""
        found = []
        for thread in _dicts(self._call("GET", self._pr.api("threads")).get("value")):
            if thread.get("isDeleted") or thread.get("status") not in OPEN_STATUSES:
                continue
            said = tuple(
                (_author(comment), comment["content"])
                for comment in _dicts(thread.get("comments"))
                if not comment.get("isDeleted")
                and comment.get("commentType") != "system"
                and isinstance(comment.get("content"), str)
            )
            if said:
                found.append(OpenThread(*_place(thread.get("threadContext")), str(thread["status"]), said))
        return found

    def post(self, payload: dict) -> int:
        """`payload` posted as a new thread: its id."""
        thread = self._call("POST", self._pr.api("threads"), body=payload).get("id")
        if type(thread) is not int:
            raise AzureError("Azure DevOps answered with no thread.", unsure=True)
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
        posting = data is not None  # a call that sends something: what it sent may have arrived, answer or none
        if posting:
            headers["Content-Type"] = "application/json"  # UTF-8, as JSON is by its own standard
        for waited in (False, True):
            request = _request(full, data, headers, method)
            try:
                if self._opener is None:
                    self._opener = _opener()
                with self._opener.open(request, timeout=HTTP_TIMEOUT_S) as answer:
                    return self._answered(answer.status, answer.read(), answer.headers, posting=posting)
            except urllib.error.HTTPError as refused:
                self._sleep(self._wait(refused, waited=waited, posting=posting))
            except (OSError, http.client.HTTPException) as failed:
                # No route, no answer in time, a certificate, or an answer cut short or no HTTP at all, which is
                # none of the network's own errors. Uncaught, it ended the screen with this call's headers shown.
                # A post sent this way may have arrived.
                raise AzureError(_unreached(failed), unsure=posting) from None
        raise AssertionError("a second 429 is a sentence")

    def _answered(self, status: int, raw: bytes, headers: Message, *, posting: bool) -> dict:
        """A 2xx's JSON object. A 203 is Azure DevOps' sign-in page: it answers a login it does not accept with one,
        where another service would answer 401. Any other body that is no object is not known to be its own: read
        by review, a company's proxy answers 200 with its own page, and called a refused login it cost a good token
        and sent the person to log in to no effect. A post answered so may have arrived. A 203 names the
        organization's tenant as a 401 does, read by an audit: it was left out, and the sentence named none."""
        if status == HTTPStatus.NON_AUTHORITATIVE_INFORMATION:
            raise self._no_login(headers)
        answer = _object(raw)
        if answer is None:
            said = (
                "Azure DevOps answered with no answer of its own. A proxy or a sign-in page may stand before it. "
                "Check the network, or run `az login`, then post again."
            )
            raise AzureError(said, unsure=posting)
        return answer

    def _wait(self, refused: urllib.error.HTTPError, *, waited: bool, posting: bool) -> float:
        """The seconds to wait before one more try, or the refusal as its sentence. A post a server failed on, a
        gateway before Azure DevOps among them, may have arrived: Azure DevOps' own refusals, the 4xx, are sure."""
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
        # A post a server failed on may have arrived: the screen says so, and what to press. A read posted nothing.
        unsure = posting and code >= HTTPStatus.INTERNAL_SERVER_ERROR
        ended = "" if unsure else " Nothing was posted."
        raise AzureError(f"Azure DevOps answered {code}: {_said(refused)}.{ended}", unsure=unsure) from None

    def _no_login(self, headers: Message) -> AzureError:
        """The sentence for a login Azure DevOps did not accept, the token forgotten: the person logs in again, and
        the one kept in memory is the one just refused. The organization's tenant is named where the answer names
        one, since a login in another tenant is the usual cause."""
        self._credential.forget()
        tenant = (headers.get("X-VSS-ResourceTenant") or "").strip().lower()
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
        raise AzureError(f"The pull request is {squash(status)[:40]}. Nothing was posted.")
    pushed = client.iterations()
    if not pushed or pushed[-1][1] != commit:
        if any(held == commit for _number, held in pushed):
            raise AzureError(
                f"The pull request has a newer push, {pushed[-1][1][:7]}. Fetch, review again, then comment."
            )
        raise AzureError(f"The pull request never held commit {commit[:7]}. Check the address and the branch reviewed.")
    iteration = pushed[-1][0]
    if (thread := posted_as(client.threads(), fingerprint, text)) is not None:
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
        message = (_object(refused.read()) or {}).get("message")
    except OSError, http.client.HTTPException:  # a body cut short
        message = None
    words = squash(message if isinstance(message, str) and message.strip() else refused.reason)
    return words[:_SAID_MAX_CHARS].rstrip(".")


def _object(raw: str | bytes) -> dict | None:
    """`raw` as the JSON object it is, or None where it is no JSON or no object: a page, a list, nothing."""
    try:
        answer = json.loads(raw)
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


def _unreached(failed: Exception) -> str:
    """The sentence for a call that got no whole answer, with what to check."""
    if isinstance(failed, http.client.HTTPException):  # its text is the other side's own bytes: not repeated
        return "Azure DevOps did not answer whole. Check the network and the proxy, then post again."
    reason = getattr(failed, "reason", failed)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return (
            "Azure DevOps' certificate was not trusted. "
            "Set SSL_CERT_FILE to a PEM bundle that holds your proxy's root, then post again."
        )
    why = f"no answer within {HTTP_TIMEOUT_S} s" if isinstance(reason, TimeoutError) else squash(str(reason))
    return f"Azure DevOps did not answer: {why[:_SAID_MAX_CHARS]}. Check the network and the proxy, then post again."

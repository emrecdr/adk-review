"""Azure DevOps as the front end reads and writes it, with no call made here: a pull request's address, a finding's
links, the comment drafted from a finding, and the thread it is posted as. `azure_client.py` makes the calls.

Every part of a link comes from the address the person pasted, the report's own commit and the diff's own path, each
encoded once: no text a model wrote goes into a URL. The login's token is sent to the hosts this file reads and to no
other, so an address on any other host is refused here, before a token is asked for."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

#: Azure DevOps Services' host, and its older one with the organization as its first label.
_HOST = "dev.azure.com"
_OLD_HOST = re.compile(r"[a-z0-9][a-z0-9-]*\.visualstudio\.com")
#: A pull request's number as its address spells it: ASCII digits, since `str.isdigit` takes any script's.
_NUMBER = re.compile(r"[1-9][0-9]{0,9}")
#: A commit as git names it whole, SHA-1 or SHA-256: what a report's `head_sha` holds.
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

_NOT_ONE = (
    "This is not a pull request's address. "
    "Paste one like https://dev.azure.com/<organization>/<project>/_git/<repository>/pullrequest/<number>."
)
_SERVER = " Azure DevOps Server is not read yet."


def _quoted(name: str) -> str:
    """One part of an address, encoded once and whole: a `/`, a `#` or a `&` in it opens no other part."""
    return quote(name, safe="")


@dataclass(frozen=True)
class PullRequest:
    """A pull request of Azure DevOps Services, as its address names it: each name decoded, and encoded once where an
    address is built from it."""

    #: The organization's root: `https://dev.azure.com/contoso`, or `https://contoso.visualstudio.com`.
    origin: str
    project: str
    repository: str
    number: int

    @property
    def repository_web(self) -> str:
        """The repository's page."""
        return f"{self.origin}/{_quoted(self.project)}/_git/{_quoted(self.repository)}"

    @property
    def web(self) -> str:
        """The pull request's page: what the state keeps, never the text a person pasted."""
        return f"{self.repository_web}/pullrequest/{self.number}"

    def api(self, *tail: str) -> str:
        """The REST address of the pull request, or of what `tail` names under it, with no query."""
        parts = (self.origin, _quoted(self.project), "_apis/git/repositories", _quoted(self.repository))
        return "/".join((*parts, "pullRequests", str(self.number), *tail))


def parse_pr_url(text: str) -> PullRequest:
    """The pull request a pasted address names, or a `ValueError` whose text says what to paste. The refusal never
    repeats the paste: a clone's address can hold a token before its host."""
    try:
        parts = urlsplit(text.strip())
        plain = parts.scheme == "https" and parts.username is None and parts.password is None and parts.port is None
    except ValueError:  # a port that is no number, a bracket that closes nothing
        raise ValueError(_NOT_ONE) from None
    host = (parts.hostname or "").lower()
    names = [unquote(part) for part in parts.path.strip("/").split("/")]
    marks = [index for index, name in enumerate(names) if name.lower() == "_git"]
    ours = host == _HOST or _OLD_HOST.fullmatch(host) is not None
    if not plain or not ours or len(marks) != 1 or any(_no_name(name) for name in names):
        raise ValueError(_NOT_ONE + (_SERVER if plain and not ours and marks else ""))
    before, after = names[: marks[0]], names[marks[0] + 1 :]
    if len(after) != 3 or after[1].lower() != "pullrequest" or not _NUMBER.fullmatch(after[2]):
        raise ValueError(_NOT_ONE)
    if host == _HOST:
        if not before:
            raise ValueError(_NOT_ONE)
        origin, before = f"https://{_HOST}/{_quoted(before[0])}", before[1:]
    else:
        origin = f"https://{host}"
        before = before[1:] if before and before[0].lower() == "defaultcollection" else before
    if len(before) > 1:
        raise ValueError(_NOT_ONE)
    return PullRequest(origin, before[0] if before else after[0], after[0], int(after[2]))


def _no_name(name: str) -> bool:
    """Whether a part of an address's path is none Azure DevOps gives a project or a repository."""
    return name in ("", ".", "..") or "/" in name or "\\" in name


def line_link(pr: PullRequest, commit: str, path: str, first: int, last: int) -> str:
    """The repository's file view at `commit` with lines `first` to `last` chosen whole: the range ends where the
    next line starts, as the page's own links end one. The commit is pinned, since a branch moves with its next push,
    and its lines with it."""
    if not _COMMIT.fullmatch(commit):
        raise ValueError("no link: a commit is 40 or 64 hex digits")
    query = f"path={_path(path)}&version=GC{commit}&line={first}&lineEnd={last + 1}"
    return f"{pr.repository_web}?{query}&lineStartColumn=1&lineEndColumn=1&_a=contents"


def files_link(pr: PullRequest, path: str) -> str:
    """The pull request's Files tab at `path`: no parameter of that page takes a line."""
    return f"{pr.web}?_a=files&path={_path(path)}"


def thread_link(pr: PullRequest, thread: int) -> str:
    """The pull request's page at one comment thread."""
    return f"{pr.web}?discussionId={thread}"


def _path(path: str) -> str:
    """A repository's path as these pages take it: from the root, encoded whole."""
    return _quoted("/" + path.lstrip("/"))

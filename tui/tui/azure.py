"""Azure DevOps as the front end reads and writes it, with no call made here: a pull request's address, a finding's
links, the comment drafted from a finding, and the thread it is posted as. `azure_client.py` makes the calls.

Every part of a link comes from the address the person pasted, the report's own commit and the diff's own path, each
encoded once: no text a model wrote goes into a URL. The login's token is sent to the hosts this file reads and to no
other, so an address on any other host is refused here, before a token is asked for."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

from adk_review.deliver.report import prose

from .findings import COMMIT, FindingRow

#: Azure DevOps Services' host, and its older one with the organization as its first label.
_HOST = "dev.azure.com"
_OLD_HOST = re.compile(r"[a-z0-9][a-z0-9-]*\.visualstudio\.com")
#: A pull request's number as its address spells it: ASCII digits, since `str.isdigit` takes any script's.
_NUMBER = re.compile(r"[1-9][0-9]{0,9}")

#: A comment's last line, which finds it again: the check and the finding's identity across reviews. Plain words and
#: no HTML, which Azure DevOps renders in wiki pages alone; read wherever it stands in a comment, since the person
#: may frame it as they like.
MARK = re.compile(r"adk-review, [\w.-]+, finding ([0-9a-f]{16})")
#: A fence opens a code block in a comment, and closes the one it stands in.
_FENCE = "```"

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
    if not COMMIT.fullmatch(commit):
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


def mark(row: FindingRow) -> str:
    """The line that says a tool drafted the comment and finds it again, or nothing where `row` holds no identity
    `marked` could read back."""
    line = f"*adk-review, {row.check}, finding {row.fingerprint}*"
    return line if MARK.fullmatch(line[1:-1]) else ""


def marked(text: str) -> str | None:
    """The finding a comment's text is marked with, by its fingerprint, or None."""
    found = MARK.search(text)
    return found.group(1) if found else None


def suggests(row: FindingRow) -> bool:
    """Whether `row`'s corrected code is drafted as a `suggestion` block, which the author applies with one click:
    only where the finding stands on lines the change added, as the owner chose, and its code holds no fence, which
    would close the block early."""
    return bool(row.code.strip()) and row.on_added_line is True and row.span() is not None and _FENCE not in row.code


def draft(row: FindingRow) -> str:
    """The comment drafted from `row`'s own fields, for the person to edit: its severity and title, its reason, what
    to change, its corrected code, and its mark. Each line of prose is the reviewer's `prose`, so `@name`, `#12` and
    `!7` notify no one and link nothing. The code is never changed: "Apply changes" writes it into the file."""
    parts = [f"**{prose(row.severity)}**: {prose(row.title) or prose(row.check)}"]
    if why := prose(row.why):
        parts.append(why)
    if fix := prose(row.suggestion):
        parts.append(f"Suggested fix: {fix}")
    if code := row.code.strip("\n"):
        parts.append(_block(code, applies=suggests(row)))
    if sign := mark(row):
        parts.append(sign)
    return "\n\n".join(parts)


def _block(code: str, *, applies: bool) -> str:
    """`code` as a block of a comment: one the author can apply, one to read, or, where it holds a fence itself,
    indented, which needs none."""
    if applies:
        return f"{_FENCE}suggestion\n{code}\n{_FENCE}"
    if _FENCE in code:
        return "Corrected code:\n\n" + "\n".join(f"    {line}" for line in code.split("\n"))
    return f"Corrected code:\n\n{_FENCE}\n{code}\n{_FENCE}"


@dataclass(frozen=True)
class Place:
    """Where a comment goes: lines `first` to `last` of `path`, on the new side, and the last line's own text, which
    alone says where the place ends."""

    path: str
    first: int
    last: int
    last_line: str


def thread_payload(text: str, place: Place, iteration: int, change: int) -> dict:
    """One comment as the thread it is posted as, at `place`, in the shape Microsoft's own sample sends. The place
    covers its lines whole: it ends one past the last line's last character, counted in UTF-16 units as Azure DevOps
    counts. A place that ends where it starts is empty, and "Apply changes" then puts a suggestion before the line
    and keeps the old one. Both sides of the iteration context name `iteration`: the change since the branches'
    common commit, which is what the review read."""
    end = len(place.last_line.removesuffix("\r").encode("utf-16-le")) // 2 + 1
    return {
        "comments": [{"parentCommentId": 0, "content": text, "commentType": 1}],
        "status": 1,
        "threadContext": {
            "filePath": "/" + place.path.lstrip("/"),
            "leftFileStart": None,
            "leftFileEnd": None,
            "rightFileStart": {"line": place.first, "offset": 1},
            "rightFileEnd": {"line": place.last, "offset": end},
        },
        "pullRequestThreadContext": {
            "changeTrackingId": change,
            "iterationContext": {"firstComparingIteration": iteration, "secondComparingIteration": iteration},
        },
    }

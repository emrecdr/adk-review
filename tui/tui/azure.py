"""Azure DevOps as the front end reads and writes it, with no call made here: a pull request's address, a finding's
links, the comment drafted from a finding, and the thread it is posted as. `azure_client.py` makes the calls.

Every part of a link comes from the address the person pasted, the report's own commit and the diff's own path, each
encoded once: no text a model wrote goes into a URL. The login's token is sent to the hosts this file reads and to no
other, so an address on any other host is refused here, before a token is asked for."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

from adk_review.collect.git import OBJECT_ID
from adk_review.deliver.report import prose

from .findings import FindingRow

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
#: What opens a block the pull request's author applies with one click: it replaces the lines its comment stands on.
SUGGESTION = f"{_FENCE}suggestion"
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

    def names(self) -> tuple[str, str, str]:
        """Its organization, project and repository, as `remote_names` reads a clone's address: in lower case, since
        Azure DevOps reads names whatever their case."""
        host = urlsplit(self.origin).hostname or ""
        organization = host.split(".")[0] if _OLD_HOST.fullmatch(host) else self.origin.rstrip("/").rpartition("/")[2]
        return unquote(organization).lower(), self.project.lower(), self.repository.lower()


#: A clone's address over SSH, as Azure DevOps writes it: `git@ssh.dev.azure.com:v3/org/project/repo`, its older host
#: `org@vs-ssh.visualstudio.com`, and the same as a URL, `ssh://git@ssh.dev.azure.com/v3/org/project/repo`.
_SSH = re.compile(
    r"(?:ssh://)?[^@/\s]+@(?:ssh\.dev\.azure\.com|vs-ssh\.visualstudio\.com)(?::\d+)?[:/]v3/([^/]+)/([^/]+)/([^/]+?)/?",
    re.IGNORECASE,
)


def remote_names(url: str) -> tuple[str, str, str] | None:
    """The organization, project and repository a clone's address names, in lower case, or None for an address of no
    Azure DevOps repository: what a remote is matched to a pull request by, with no call made. A project of the
    repository's own name may be left out of an address over HTTPS, as Azure DevOps leaves it out."""
    text = url.strip()
    if (ssh := _SSH.fullmatch(text)) is not None:
        organization, project, repository = (unquote(name) for name in ssh.groups())
    else:
        try:
            parts = urlsplit(text)
        except ValueError:
            return None
        host = (parts.hostname or "").lower()
        names = [unquote(name) for name in parts.path.strip("/").split("/")]
        if parts.scheme.lower() != "https" or "_git" not in [name.lower() for name in names]:
            return None
        at = [name.lower() for name in names].index("_git")
        before, after = names[:at], names[at + 1 :]
        if host == _HOST and before:
            organization, before = before[0], before[1:]
        elif _OLD_HOST.fullmatch(host):
            organization = host.split(".")[0]
            before = before[1:] if before and before[0].lower() == "defaultcollection" else before
        else:
            return None
        if len(after) == 2 and after[0].lower() in ("_optimized", "_full"):  # a clone of limited refs, or all of them
            after = after[1:]
        if len(after) != 1 or len(before) > 1:
            return None
        repository = after[0]
        project = before[0] if before else repository.lower().removesuffix(".git")
    return organization.lower(), project.lower(), repository.lower().removesuffix(".git")


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
    return name in ("", ".", "..") or "/" in name or "\\" in name or not name.isprintable()


def line_link(pr: PullRequest, commit: str, path: str, first: int, last: int) -> str:
    """The repository's file view at `commit` with lines `first` to `last` chosen whole: the range ends where the
    next line starts, as the page's own links end one. The commit is pinned, since a branch moves with its next push,
    and its lines with it."""
    if not OBJECT_ID.fullmatch(commit):
        raise ValueError("no link: a commit is 40 or 64 hex digits")
    query = f"path={_path(path)}&version=GC{commit}&line={first}&lineEnd={last + 1}"
    return f"{pr.repository_web}?{query}&lineStartColumn=1&lineEndColumn=1&_a=contents"


def files_link(pr: PullRequest, path: str) -> str:
    """The pull request's Files tab at `path`: no parameter of that page takes a line."""
    return f"{pr.web}?_a=files&path={_path(path)}"


def thread_link(pr: PullRequest, thread: int) -> str:
    """The pull request's page at one comment thread."""
    return f"{pr.web}?discussionId={thread}"


def rooted(path: str) -> str:
    """A repository's path as Azure DevOps names a file: from the root, with its `/`. One spelling for a link, a
    thread's place and the change it is looked up by, which must be the same file."""
    return "/" + path.lstrip("/")


def _path(path: str) -> str:
    """A repository's path as these pages take it: from the root, encoded whole."""
    return _quoted(rooted(path))


def mark(row: FindingRow) -> str:
    """The line that says a tool drafted the comment and finds it again, or nothing where `row` holds no identity
    `marked` could read back."""
    line = f"*adk-review, {row.check}, finding {row.fingerprint}*"
    return line if MARK.fullmatch(line[1:-1]) else ""


def marked(text: str) -> str | None:
    """The finding a comment's text is marked with, by its fingerprint, or None."""
    found = MARK.search(text)
    return found.group(1) if found else None


def posted_as(threads: Sequence[tuple[int, Sequence[str]]], fingerprint: str, text: str = "") -> int | None:
    """The thread that is this finding's already, among a pull request's live `threads`, each its number and its
    comments' words: one whose comment carries the finding's mark, anyone's, since a teammate's post of it is a
    duplicate too, or one that opens with the very words of `text`. Read by review: the person may take the mark
    out, and a post that arrived with no answer was then posted again. A reply that says the same is no thread of
    its own."""
    words = text.strip()
    for number, said in threads:
        marks = fingerprint and any(marked(one) == fingerprint for one in said)
        if marks or (words and said and said[0].strip() == words):
            return number
    return None


def suggests(row: FindingRow) -> bool:
    """Whether `row`'s corrected code is drafted as a `suggestion` block, which the author applies with one click:
    where the report says the code can stand on the finding's lines. Only the report knows: whether the change added
    each line, whether the quote holds a deleted one, and whether its own safety rules left the code the model's.
    Read by three reviews: guessed here from what the code looked like, each guess had a case it missed. A fence in
    the code would close the block early, which is this file's own to know."""
    return row.suggestable and _FENCE not in row.code


def draft(row: FindingRow) -> str:
    """The comment drafted from `row`'s own fields, for the person to edit: its severity and title, its reason, what
    to change, its corrected code, and its mark. Each line of prose is the reviewer's `prose`, so `@name`, `#12` and
    `!7` notify no one and link nothing. The code is drawn as the report holds it and never changed here: "Apply
    changes" writes it into the file."""
    parts = [f"**{prose(row.severity)}**: {prose(row.title) or prose(row.check)}"]
    if why := prose(row.why):
        parts.append(why)
    if fix := prose(row.suggestion):
        parts.append(f"Suggested fix: {fix}")
    if row.code.strip():  # a blank line that opens the code is the code's own: only what trails it goes
        parts.append(_block(row.code.rstrip("\n"), applies=suggests(row)))
    if sign := mark(row):
        parts.append(sign)
    return "\n\n".join(parts)


def _block(code: str, *, applies: bool) -> str:
    """`code` as a block of a comment: one the author can apply, one to read, or, where it holds a fence itself,
    indented, which needs none."""
    if applies:
        return f"{SUGGESTION}\n{code}\n{_FENCE}"
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
            "filePath": rooted(place.path),
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

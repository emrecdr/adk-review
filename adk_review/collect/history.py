"""What a push sends, read commit by commit for the pre-push hook: its scope, each commit's message and the lines it
added, and a tag's message. Read-only git, as `git.py` runs it, and nothing a review of the change as a whole reads:
`push.py` alone asks here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence

from .diff import file_added, readable
from .git import _PLAIN, GitError, _empty_tree, _git, _git_raw, _sha, _text


def push_scope(repo: str, remote: str | None, head: str, held: str | None) -> tuple[str, list[str]] | None:
    """What a push of `head` asks a review of, as git answers it: the commit pushed, and the commits the remote holds
    that its new ones grow from, their bases, none where the remote holds none of the history, so the whole project is
    new; None where it sends nothing to review — no commit the remote lacks, or commits that change nothing against a
    base: a change and its revert, or a first commit holding no file. New is what neither `held`, the remote's commit
    of the ref where this clone has it, nor any of `remote`'s branches hold; with no remote, a range a person named,
    `held` is the base as named.

    Several bases are a merge's, each holding what it brought: one no other of them holds is reviewed against apart, and
    a line new to the remote is new to each of them. Measured by review: of a merge of the remote's newer `main` into a
    branch it already held, and of a branch it held into `main`, one base was reviewed alone, whichever git listed
    first, and each review covered the other side's own changes, a teammate's token or picture among them, and stopped
    a clean push. Measured before, a review's own words decided, and a first push, a branch over local commits of
    `main` and a branch sharing no history went unreviewed, each read as "no change to review". `GitError` where git
    cannot answer, or where a name is no commit here."""
    pushed = _commit(repo, head)
    if remote is None:
        bases = _held(repo, None, held)
    else:
        _code, out = _git(repo, "rev-list", "--boundary", pushed, "--not", *_held(repo, remote, held))
        listed = out.split()
        if all(commit.startswith("-") for commit in listed):
            return None
        bases = [commit[1:] for commit in listed if commit.startswith("-")]
        if len(bases) > 1:
            bases = _git(repo, "merge-base", "--independent", *bases)[1].split()
    for base in bases or [_empty_tree(repo)]:
        if _git(repo, "diff-tree", "--quiet", base, pushed, ok=(0, 1))[0] == 0:
            return None
    return pushed, bases


def _held(repo: str, remote: str | None, held: str | None) -> list[str]:
    """What the remote holds, as `rev-list` reads it after `--not`: its commit of the ref, where this clone has it,
    and each of its branches as this clone last fetched them; with no remote, a range a person named, its base as
    named, or nothing."""
    if remote is None:
        return [_commit(repo, held)] if held else []
    known = _sha(repo, held) if held else None
    return [*([known] if known else []), f"--remotes={remote}"]


def _commit(repo: str, ref: str) -> str:
    """The commit `ref` names, or `GitError`: a name that is no commit here, a tree's or a blob's, is a hole."""
    if (sha := _sha(repo, ref)) is None:
        raise GitError(f"{ref} names no commit in this repository")
    return sha


#: How many commits one `diff-tree` reads of a push's history: measured over a long real history, 2,000 commits took
#: 1.6 s and 26.7 MB of patch, so a batch stays well inside a call's timeout and its memory, whatever the push.
HISTORY_BATCH = 500
_A_COMMIT = re.compile(rb"^([0-9a-f]{40}|[0-9a-f]{64})\n", re.MULTILINE)


def sent(
    repo: str, remote: str | None, head: str, held: str | None, read: Sequence[str] = ()
) -> Iterator[tuple[str, str, list[dict]]]:
    """Each commit a push of `head` sends, newest first, as `(commit, message, files)`: what it says, and each file it
    adds lines to against its first parent, `{"path", "added"}` as a rule reads a change. What one commit adds and a
    later one takes out is in the history the remote receives, though the change as a whole no longer holds it. The
    commits are `push_scope`'s new ones, less those a head in `read` reaches, which the same push read for another of
    its refs; with no remote, those of the range a person named. Read as a review reads a diff: plumbing, no
    attributes but none, the branch's own `.gitattributes` unread, so no file it marks hides its lines. A merge's patch
    is no one commit's, so it adds no file here, only its message. Read a batch at a time, and let go of before the
    next: a first push sends a project's whole history. `GitError` where git cannot answer."""
    pushed = _commit(repo, head)
    apart = [*_held(repo, remote, held), *read]
    _, listed = _git(
        repo, "rev-list", "--no-commit-header", "--format=%x00%H%x00%B", pushed, "--not", *apart, strip=False
    )
    fields = listed.split("\0")[1:]
    said = dict(zip(fields[::2], (message.rstrip("\n") for message in fields[1::2]), strict=True))
    commits, attributes = list(said), f"--attr-source={_empty_tree(repo)}"
    for start in range(0, len(commits), HISTORY_BATCH):
        batch = commits[start : start + HISTORY_BATCH]
        added = _added_by(repo, batch, attributes)
        yield from ((commit, said[commit], added.get(commit, [])) for commit in batch)


def _added_by(repo: str, commits: Sequence[str], attributes: str) -> dict[str, list[dict]]:
    """Each commit's files with the lines it added, a first commit's against the empty tree, in one call:
    `diff-tree --stdin` names each commit on a line of its own before its patch, and a merge not at all."""
    found: dict[str, list[dict]] = {}
    stdin = "".join(f"{commit}\n" for commit in commits).encode()
    patch = _git_raw(repo, attributes, "diff-tree", "--stdin", "-r", "-p", "--root", *_PLAIN, stdin=stdin).stdout
    parts = _A_COMMIT.split(patch)  # what came before the first commit's line, then each commit and its patch
    for commit, body in zip(parts[1::2], parts[2::2], strict=True):
        files = found.setdefault(commit.decode(), [])
        for piece in body.split(b"\ndiff --git "):
            if (file := file_added(readable(_text(piece)))) is not None:
                files.append(file)
    return found


def tag_message(repo: str, ref: str) -> str | None:
    """The message of the annotated tag `ref` names, or None where it names no tag: what a pushed tag says, which no
    commit holds. `GitError` where git cannot answer."""
    if (named := _sha(repo, ref, "tag")) is None:  # a branch, a commit or a light tag
        return None
    return _git(repo, "cat-file", "tag", named, strip=False)[1].partition("\n\n")[2].rstrip("\n")

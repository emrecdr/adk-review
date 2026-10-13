"""Read-only git, gathered without a model.

Git runs as an argv list with a timeout, never through a shell, only with
verbs that read, decoded as UTF-8. `preflight` and `collect_evidence` answer
as envelopes, for the driver and the tools; the helpers between them return
bare values, for code. `collect_evidence` runs the same calls in plain Python
so the driver can gather everything the rules and the guidelines read
before any model is called. Nothing here knows ADK: the model-facing tools
that wrap these live in `judge/tools.py`.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import string
import subprocess
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core.blast import Blast, Radius, blast_radius
from ..core.rules import Tree
from ..core.text import cut_at, lines_of, squash

GIT_TIMEOUT_S = 30
#: Every string the model reads is capped in characters, and the cut is named. Measured live on Copilot at 4,000: a
#: 7,529-character file's planted shell call went unread, a hole, and so did 3 of a 17-file change's files; at 16,000
#: each was read, the call named in both runs, for a fifth more tokens.
DIFF_CAP_CHARS = 16_000
#: And the block of diffs the guidelines read, in all: a file past it is left out whole, and named. Measured with none,
#: `--all` over 4,826 files gave each guideline and each verifier 14.9 MB; this repository's whole project, 275,688
#: characters, was 80,000 tokens a guideline, answered on Copilot; the planted branch is 2,205.
BLOCK_CAP_CHARS = 250_000
_HUNK_START = re.compile(r"^@@ -\S+ \+(\d+)(,0\b)?")
#: The tree the blast radius reads is bounded, and what is not read is counted: over a partial tree a count is a
#: floor, and says so. Measured at 80 to 142 ms a megabyte to parse and resolve (ADK's 6.3 MB, LiteLLM's 32.3);
#: a file cap alone let a branch of 5,000 one-megabyte files in. The largest real file seen was 0.8 MB.
MAX_TREE_BYTES = 20_000_000
MAX_FILE_BYTES = 1_000_000
#: The tree of no files, by the repository's hash: git knows it without storing it. A whole-project review diffs the
#: head against it, so every file reads as added and goes through the rules, the guidelines and grounding as a change
#: does.
EMPTY_TREE = {
    "sha1": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
    "sha256": "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321",
}


class GitError(Exception):
    """git could not answer: a reason to name, never an empty answer to read."""


#: What a glob pathspec reads as a pattern, each escaped where a path is only a name.
_GLOB = re.compile(r"[*?[\\]")
#: Settings that change what git prints, what it reads or what it runs, fixed for every call: set through the
#: environment, they outrank every config file, the repository's own included. No attributes file but the
#: repository's, and no file-system monitor: `core.fsmonitor` names a program git starts beside a diff. Measured,
#: `diff-tree` started the one the reviewed repository's own config named, in the review's environment, keys and all,
#: and `core.fsmonitor=true` started git's own daemon, which wrote into `.git`.
_SETTLED = {
    "core.precomposeUnicode": "false",
    "diff.suppressBlankEmpty": "false",
    "core.attributesFile": os.devnull,
    "core.fsmonitor": "false",
}


def _environment(repo: str) -> dict[str, str]:
    """git's environment for a review: the caller's with every `GIT_*` variable left out, as git's own test suite
    leaves them out, and then only what a review needs: no config but the repository's own and what is fixed here,
    no attributes but the repository's, nothing fetched (a partial clone would fetch what it lacks, into the
    repository), no object replaced, no graft.

    Measured, each before: `GIT_DIR` reviewed another repository under the requested path's name, and git 2.54's
    `GIT_REFERENCE_BACKEND`, which a list of such variables had missed, read another repository's refs;
    `GIT_DIFF_OPTS=-u0` in a shell, or `diff.context=0` in a person's own config, took every context line out of a
    diff, and `*.py -diff` in their attributes file made every Python file binary; a replace ref swapped the key's
    file for a harmless one; macOS's precomposed names found no file committed in NFD; and `suppressBlankEmpty` put
    the key a line early. With a person's own config unread, so is their list of safe directories: the repository
    the operator named is the one this review trusts, as git's command-line scope allows since 2.38.
    """
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env |= {"GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull}
    env |= {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1"}
    settled = _SETTLED | {"safe.directory": Path(os.path.realpath(repo)).as_posix()}
    env["GIT_CONFIG_COUNT"] = str(len(settled))
    for index, (key, value) in enumerate(settled.items()):
        env[f"GIT_CONFIG_KEY_{index}"], env[f"GIT_CONFIG_VALUE_{index}"] = key, value
    return env


def on_path(name: str) -> str:
    """`name` by its full path, as PATH finds it, never from the current directory: Windows looks there first, and
    the current directory can be the checkout under review, whose own `git.exe` must never run. Not found, the name
    alone, which is never launched: Windows would look for a bare name in the current directory too."""
    return _found(name, os.environ.get("PATH", ""))


@functools.cache
def _found(name: str, path: str) -> str:
    """Once per PATH: on Windows each lookup tries every PATHEXT. Only PATH's absolute entries are searched: an empty
    entry or a relative one names the current directory. Measured, `PATH=.:…` ran a `git` from the folder the review
    started in."""
    os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"  # read by `shutil.which` on Windows, ignored elsewhere
    absolute = os.pathsep.join(entry for entry in path.split(os.pathsep) if os.path.isabs(entry))
    return shutil.which(name, path=absolute) or name


def _run(argv: list[str], env: dict[str, str], stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    """One command, bytes in and out, stopped whole past `GIT_TIMEOUT_S`. Git for Windows' `git.exe` runs the real
    git as its child, which holds the output open; `subprocess.run` kills only the first and then, on Windows, reads
    the output to its end, so a git past its timeout hung the review. There the whole tree is stopped first."""
    with subprocess.Popen(
        argv, stdin=None if stdin is None else subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    ) as process:
        try:
            out, err = process.communicate(stdin, timeout=GIT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            if os.name == "nt":  # the tree, then the output to its end, which Windows reads on threads
                taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
                subprocess.run([taskkill, "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
                process.communicate()
            process.kill()
            raise
    return subprocess.CompletedProcess(argv, process.returncode, out, err)


def _git(repo: str, *args: str, strip: bool = True, ok: tuple[int, ...] = (0,)) -> tuple[int, str]:
    """One read-only git command: an argv list, no shell, a timeout, no fetch, stdout captured, decoded as UTF-8.

    An exit code outside `ok` raises `GitError` with git's own first line, and so does a call past the timeout,
    killed, naming the limit, for the envelope to answer: some answers below are read without their exit
    code, and a failure read as an empty answer would read as nothing there. `ok` names the codes a caller
    reads as an answer: 1 is `rev-parse`'s for a ref that does not exist. `strip` is off for a file's content,
    where the line numbers must survive, and for a folder's path, whose name may end in a space.
    """
    done = _git_raw(repo, *args, ok=ok)
    return done.returncode, _text(done.stdout, strip)


def _text(raw: bytes, strip: bool = True) -> str:
    """git's answer decoded here, never in text mode, which reads a lone `\\r` inside a line as a break, and trimmed of
    ASCII whitespace alone: measured by review, `str.strip` cut a branch's closing U+2028 and read it as another."""
    out = raw.decode("utf-8", "replace")
    return out.strip(string.whitespace) if strip else out


def _git_raw(
    repo: str, *args: str, stdin: bytes | None = None, ok: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[bytes]:
    """`_git`'s call with its answer left as bytes, what a batch read counts its sizes in."""
    if (git := on_path("git")) == "git":  # not on PATH: Git for Windows can be installed for Git Bash alone
        raise GitError(f"git {_verb(args)} could not start: git is not on PATH")
    try:
        done = _run([git, "-C", repo, *args], _environment(repo), stdin)
    except subprocess.TimeoutExpired:
        raise GitError(f"git {_verb(args)} did not answer within {GIT_TIMEOUT_S} s") from None
    except OSError as exc:  # found, and still could not start: no permission to run it, say
        raise GitError(f"git {_verb(args)} could not start: {exc.strerror or exc}") from None
    if done.returncode not in ok:
        said = done.stderr.decode("utf-8", "replace")
        # git's advice (`hint: ...`) comes before the error at times, and is never the reason.
        lines = [line.strip() for line in said.splitlines() if line.strip() and not line.startswith("hint:")]
        why = lines[0] if lines else f"exit {done.returncode}"
        raise GitError(f"git {_verb(args)} failed: {why}")
    return done


def _verb(args: tuple[str, ...]) -> str:
    """The git command a call runs, past git's own options before it (`--attr-source`): what an error names."""
    return next((arg for arg in args if not arg.startswith("-")), "")


def _sha(repo: str, ref: str, kind: str = "commit") -> str | None:
    """The commit a ref names, or the object of another `kind`, or None. `--end-of-options` is the one place a user
    string reaches git as an argument: a ref spelled `-x` must be a ref, never a flag. `^{commit}` names the commit
    an annotated tag tags, never the tag itself; `^{tag}` names a tag object, and nothing for a branch or a commit."""
    code, out = _git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{{kind}}}", ok=(0, 1))
    return out if code == 0 else None


def _namesakes(repo: str, ref: str) -> list[str]:
    """Every full ref a name could mean, however it is spelled after (`main~0` still names `main`), and, when one has
    the name and it is hex, every object it abbreviates. Git prefers a tag to a branch, and a ref to an abbreviated
    object, and `--quiet` hides its warning: a fork's tag once made the base the head. More than one is refused,
    never resolved."""
    name = ref
    for suffix in ("~", "^", ":", "@{"):
        name = name.partition(suffix)[0]
    means = [
        f"refs/{name}",
        f"refs/tags/{name}",
        f"refs/heads/{name}",
        f"refs/remotes/{name}",
        f"refs/remotes/{name}/HEAD",
    ]
    # Broken at "\n" alone: a ref's name may hold U+2028, which `splitlines` breaks at.
    _, out = _git(repo, "for-each-ref", "--format=%(refname)", *means)
    found = [full for full in lines_of(out) if full in means]
    if found and 4 <= len(name) <= 40 and all(c in "0123456789abcdef" for c in name):
        _, objects = _git(repo, "rev-parse", f"--disambiguate={name}")  # a tag named as a short sha shadowed it
        found += [f"the object {sha[:12]}" for sha in objects.split()]
    return found


def same_folder(one: str, other: str) -> bool:
    """Whether two paths name one folder however each is spelled: a letter's case on a file system that ignores it, a
    link, a Windows short name. Measured: compared as strings, a repository typed in another case was refused."""
    try:
        return os.path.samefile(one, other)
    except OSError:  # a folder this process cannot read is no match
        return False


def _refused(repo: str, root: str) -> dict | None:
    """Why `repo` is no repository to review, as an error envelope, or None: no `.git` in it, or a `.git` that is no
    repository's top. Measured: a `.git` that is no repository, an empty folder say, sent git to the repository around
    it, and a review read the outer repository's commits under the inner folder's name. Raises `GitError` as `_git`
    does."""
    if not is_repository(root):
        return {"status": "error", "error_message": f"{repo} is not a git repository (no .git directory)"}
    top = _git(root, "rev-parse", "--show-toplevel", strip=False)[1].removesuffix("\n")
    if not same_folder(top, root):
        return {"status": "error", "error_message": f"{repo} is no repository's top: git answers for {top}"}
    return None


def _empty_tree(root: str) -> str:
    """The empty tree's id in the repository's own hash: what a whole project, or a first commit's index, is read
    against."""
    _, form = _git(root, "rev-parse", "--show-object-format")
    return EMPTY_TREE[form]


def preflight(repo: str, base: str | None, head: str) -> dict:
    """The scope of a review — the repository, both shas and their merge base — or an error envelope. With no base,
    the whole project at the head: the empty tree stands for the base and the merge base, and a shallow clone
    serves, since no merge base is looked for; so does one whose base is one of the head's own commits."""
    root = os.path.expanduser(repo)  # not Path.expanduser, which raises on a `~name` this machine does not know
    try:
        if refused := _refused(repo, root):
            return refused
        full: dict[str, str] = {}  # the one full ref each name resolved through, if a ref: a report names a tag
        for label, ref in (("base branch", base), ("head branch", head)):
            names = _namesakes(root, ref) if ref is not None else []
            full[label] = names[0] if names else ""
            if len(names) > 1:
                why = f"{label} {ref!r} is ambiguous: {' and '.join(names)}; name one in full"
                return {"status": "error", "error_message": why}
        base_sha, head_sha = _sha(root, base) if base is not None else "", _sha(root, head)
        if base_sha is None:
            return {"status": "error", "error_message": f"base branch {base!r} does not exist in {repo}"}
        if head_sha is None:
            return {"status": "error", "error_message": f"head branch {head!r} does not exist in {repo}"}
        # From here on git only sees commit hashes, which cannot be mistaken for options.
        if base is None:
            base_sha = merge_base = _empty_tree(root)
            span = head_sha
        else:
            # Cut at a depth, as CI checks out, a clone can name the wrong merge base, unless the base is one of the
            # head's own commits: then it is the merge base, whatever lies past the cut. Measured by review: every
            # push from a shallow clone was stopped, its base always one of the head's commits.
            if (
                _git(root, "rev-parse", "--is-shallow-repository")[1] == "true"
                and _git(root, "merge-base", "--is-ancestor", base_sha, head_sha, ok=(0, 1))[0] != 0
            ):
                why = "a merge base found in one can be the wrong one: fetch its history first, git fetch --unshallow"
                return {"status": "error", "error_message": f"{repo} is a shallow clone, and {why}"}
            code, merge_base = _git(root, "merge-base", base_sha, head_sha, ok=(0, 1))
            if code == 1:  # git's answer for two commits with no ancestor in common
                why = f"{base!r} and {head!r} share no history: no change to review"
                return {"status": "error", "error_message": why}
            span = f"{merge_base}..{head_sha}"
        _, ahead = _git(root, "rev-list", "--count", span)
    except GitError as exc:
        return {"status": "error", "error_message": str(exc)}
    return {
        "status": "success",
        "repo": root,
        "base": base or "",
        "base_sha": base_sha,
        "head": head,
        "head_sha": head_sha,
        "merge_base": merge_base,
        "commits_ahead": int(ahead),
        "base_ref": full["base branch"],
        "head_ref": full["head branch"],
    }


def is_repository(repo: str) -> bool:
    """A repository of its own, never a folder inside one, whose git would answer for the repository around it."""
    return (Path(os.path.expanduser(repo)) / ".git").exists()


def checked_out(repo: str) -> str | None:
    """The branch the repository has checked out, or None where none is: a detached head, as CI checks out."""
    code, out = _git(repo, "symbolic-ref", "--short", "--quiet", "HEAD", ok=(0, 1))
    return out if code == 0 else None


def default_base(repo: str) -> str | None:
    """What a change is compared with when no base is named: `main`, or `master` where there is no `main`; else the
    branch the remote's HEAD names, as a clone that checked out a detached commit has it, by its full name, so a tag
    or a branch of the same short name cannot stand in. Measured before: such a clone, which holds no local `main`,
    was refused with no base at all."""
    local = next((name for name in ("main", "master") if _sha(repo, f"refs/heads/{name}") is not None), None)
    if local is not None:
        return local
    code, remote = _git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", ok=(0, 1))
    named = code == 0 and remote.startswith("refs/remotes/origin/") and _sha(repo, remote) is not None
    return remote if named else None


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
#: The escapes git writes in a path it quotes, beside a byte's three octal digits.
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
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
            if (file := _file_added(readable(_text(piece)))) is not None:
                files.append(file)
    return found


def _file_added(diff: str) -> dict | None:
    """One file's part of a commit's patch as `{"path", "added"}`, or None where it adds no line: a deleted file, a
    binary one, a rename or a mode alone. The path is the new one, as its `+++` line spells it, quoted or not."""
    to = next((line[4:] for line in _header(diff).split("\n") if line.startswith("+++ ")), "/dev/null")
    added = added_lines(diff)
    if to == "/dev/null" or not added:
        return None
    return {"path": _unquoted(to.removesuffix("\t")).removeprefix("b/"), "added": added}


def _unquoted(spelled: str) -> str:
    """A path as git spells it in a patch's header: in double quotes, C's escapes inside, where a byte needs one."""
    if not (len(spelled) > 1 and spelled.startswith('"') and spelled.endswith('"')):
        return spelled
    out, chars = bytearray(), iter(spelled[1:-1])
    for char in chars:
        if char != "\\":
            out += char.encode()
        elif (escaped := next(chars, "")) in _ESCAPES:
            out.append(_ESCAPES[escaped])
        else:  # a byte, in three octal digits
            out.append(int(escaped + next(chars, "") + next(chars, ""), 8))
    return out.decode("utf-8", "replace")


def tag_message(repo: str, ref: str) -> str | None:
    """The message of the annotated tag `ref` names, or None where it names no tag: what a pushed tag says, which no
    commit holds. `GitError` where git cannot answer."""
    if (named := _sha(repo, ref, "tag")) is None:  # a branch, a commit or a light tag
        return None
    return _git(repo, "cat-file", "tag", named, strip=False)[1].partition("\n\n")[2].rstrip("\n")


def in_scope(path: str, scope: Sequence[str]) -> bool:
    """Whether a changed file is one of `scope`'s, or lies under one of its folders; no scope holds every file."""
    return not scope or any(path == folder or path.startswith(f"{folder}/") for folder in scope)


def preflight_staged(repo: str) -> dict:
    """The scope of a review of what is staged: the index, as the next commit would hold it, against `HEAD`, or the
    empty tree before a first commit. Read through plumbing alone — `diff-index --cached`, `ls-files -s`, `cat-file` —
    which writes nothing, no index refreshed and no lock taken; a file not staged, untracked or changed in the working
    tree only, is not in the next commit and not reviewed."""
    root = os.path.expanduser(repo)
    try:
        if refused := _refused(repo, root):
            return refused
        head = _sha(root, "HEAD") or _empty_tree(root)  # no commit yet: the index against the empty tree
    except GitError as exc:
        return {"status": "error", "error_message": str(exc)}
    return {
        "status": "success",
        "repo": root,
        "base": "HEAD",
        "base_sha": head,
        "head": "the index",
        "head_sha": "staged",
        "merge_base": head,
        "commits_ahead": 0,
        "base_ref": "",
        "head_ref": "",
        "staged": True,
    }


def list_changed(resolved: dict) -> list[dict[str, str]]:
    """`[{"path", "status"}]` for the change, from NUL-separated records so odd paths survive; a rename also carries
    its `old` path, `-M` finding renames whatever the reviewer's `diff.renames` says."""
    if resolved.get("staged"):  # the index against the base: what the next commit would change
        _, out = _git(
            resolved["repo"], "diff-index", "--cached", "-r", "--name-status", "-z", "-M", resolved["merge_base"]
        )
    else:
        _, out = _git(
            resolved["repo"],
            "diff-tree",
            "-r",
            "--name-status",
            "-z",
            "-M",
            resolved["merge_base"],
            resolved["head_sha"],
        )
    parts = iter(p for p in out.split("\0") if p)
    files: list[dict[str, str]] = []
    for field in parts:
        status = field[0]
        # A rename (`R<score>`) or a copy (`C<score>`) names the old path first; the new one exists.
        old = {"old": next(parts)} if status in "RC" else {}
        files.append({"path": next(parts), "status": status, **old})
    return sorted(files, key=lambda f: f["path"])


def _literally(*paths: str) -> list[str]:
    """Pathspecs naming exactly these files: each path literal, and what lies inside a folder of that name left out,
    never the entry itself: measured, excluding `<name>/` hid a submodule, which git lists as a folder. From the
    top: without it Git for Windows reads a `\\` in a name as a folder's separator, and `c:x.py` as a drive."""
    specs = []
    for name in filter(None, paths):
        specs += [f":(top,literal){name}", f":(top,exclude,glob){_GLOB.sub(lambda m: '\\' + m.group(), name)}/**"]
    return specs


#: How every patch is read — `diff_of`, `whole_diffs` and a push's history: no colour, no external diff, no text
#: conversion, renames found.
_PLAIN = ("--no-color", "--no-ext-diff", "--no-textconv", "-M")


def diff_of(resolved: dict, path: str, old: str = "") -> str:
    """One file's whole unified diff, as git prints it for no one's settings and with "\\n" its only line break: the
    path taken literally, never as a pattern; the merge base's attributes, never the branch's own; no colour, no
    external diff, no text conversion.
    Measured: each of those once hid a planted key, the branch's `-diff` or a path spelled `:config.py` among them.
    `--attr-source` is git 2.41's. A rename is diffed with its `old` path too, so it reads as the lines it moved
    and changed; by the new path alone, it read as a whole file added. Each path is a file and never a folder:
    measured, `a` renamed to `b.py` beside a new `a/x.py` brought `a/x.py`'s key into `b.py`'s diff."""
    repo, base, head = resolved["repo"], resolved["merge_base"], resolved["head_sha"]
    paths = _literally(old, path)
    if resolved.get("staged"):  # the index's text, against the base, as the next commit would hold it
        _, diff = _git(repo, f"--attr-source={base}", "diff-index", "--cached", "-p", *_PLAIN, base, "--", *paths)
    else:
        _, diff = _git(repo, f"--attr-source={base}", "diff-tree", "-r", "-p", *_PLAIN, base, head, "--", *paths)
    return readable(diff)


def whole_diffs(resolved: dict, entries: list[dict[str, str]], scope: Sequence[str]) -> list[str]:
    """Under `--all`, each entry's diff as `diff_of` reads it, from one call split at each `diff --git` opening a line.

    Every file is added, so each has one such header, and no line of a file can open one: each is marked `+`.
    Measured over 4,826 files: 9.4 s a call a file, eight at a time; 0.86 s in one."""
    repo, base, head = resolved["repo"], resolved["merge_base"], resolved["head_sha"]
    specs = ("--", *(f":(top,literal){folder}" for folder in scope))
    _, names = _git(repo, "diff-tree", "-r", "-z", "--name-only", "-M", base, head, *specs, strip=False)
    paths = names.split("\0")[:-1]
    # Split as bytes, each part decoded alone: one str of the whole patch is as wide as its widest character.
    args = (f"--attr-source={base}", "diff-tree", "-r", "-p", *_PLAIN, base, head, *specs)
    # Every line of an added file opens with `+`, so a line opening with `diff --git ` is a file's header: a literal
    # split, where a pattern tested every line's start (measured: 45 ms against 5 on a 15 MB patch).
    pieces = _git_raw(repo, *args).stdout.split(b"\ndiff --git ")  # the whole, freed once split
    first = [pieces[0]] if pieces[0].startswith(b"diff --git ") else []
    parts = first + [b"diff --git " + piece for piece in pieces[1:]]
    if len(parts) != len(paths) or sorted(paths) != [entry["path"] for entry in entries]:
        raise GitError("git diff-tree's one patch did not split into the files it lists")
    pairs = sorted(zip(paths, parts, strict=True), key=lambda pair: pair[0])  # as `list_changed` sorts git's order
    return [readable(_text(part)) for _, part in pairs]


#: How many git processes run at once where a change asks one per file. Measured on a branch of 224 files: its
#: diffs took 1,347 ms one at a time and 292 ms eight at a time, and sixteen did no better. git's own batch for
#: diffs, `diff-pairs`, would take 25 ms, but it came with git 2.50, and this reviewer asks for 2.41.
GIT_WORKERS = 8


def _listed(resolved: dict) -> list[tuple[str, str, str, str, str]]:
    """The head's tree, one `(mode, kind, sha, size, path)` an entry, NUL-separated so any path holds; raises
    `GitError`, never an empty tree. Staged, the head is the index: `ls-files -s` names each entry and its blob, and
    one `cat-file --batch-check` gives each blob's size."""
    if resolved.get("staged"):
        return _index_listed(resolved)
    _, listing = _git(resolved["repo"], "ls-tree", "-r", "-l", "-z", resolved["head_sha"], strip=False)
    entries = []
    for record in filter(None, listing.split("\0")):
        meta, _, path = record.partition("\t")
        mode, kind, sha, size = meta.split()
        entries.append((mode, kind, sha, size, path))
    return entries


def untracked(resolved: dict) -> list[str]:
    """What git neither tracks nor ignores, an untracked folder once, as `git status` names them: under `--staged`,
    not in the next commit, so named in the report, never reviewed and never a hole. `ls-files` reads no file's
    content; the files changed and not staged are not listed, since measured, `diff-files` ran a clean filter the
    repository's own config named, on an entry as old as the index, `--attr-source` notwithstanding, as
    `info/attributes` is read regardless."""
    _, listed = _git(
        resolved["repo"], "ls-files", "--others", "--exclude-standard", "--directory", "--no-empty-directory", "-z",
        strip=False,
    )  # fmt: skip
    return sorted(filter(None, listed.split("\0")))


def _index_listed(resolved: dict) -> list[tuple[str, str, str, str, str]]:
    """The index as `_listed` gives a tree: `(mode, kind, sha, size, path)` an entry, a submodule's a commit with no
    size, as `ls-tree -l` gives one."""
    _, staged = _git(resolved["repo"], "ls-files", "-s", "-z", strip=False)
    entries = []
    for record in filter(None, staged.split("\0")):
        meta, _, path = record.partition("\t")
        mode, sha, _stage = meta.split()
        entries.append((mode, "commit" if mode == "160000" else "blob", sha, "-", path))
    blobs = [sha for _mode, kind, sha, _size, _path in entries if kind == "blob"]
    if not blobs:
        return entries
    sized = _git_raw(resolved["repo"], "cat-file", "--batch-check", stdin="".join(f"{sha}\n" for sha in blobs).encode())
    sizes = {}
    for line in sized.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 3:  # `<sha> blob <size>`, else `<sha> missing`
            sizes[parts[0]] = parts[2]
    return [(mode, kind, sha, sizes.get(sha, "-"), path) for mode, kind, sha, _size, path in entries]


def _blobs(resolved: dict, shas: Sequence[str]) -> list[str | None]:
    """Each blob's text, in order, from one `cat-file --batch`; None for one git did not give back, as a partial
    clone's missing blob, never fetched. Asked by object name, so no path splits the input, and read as bytes, since
    git counts sizes in bytes and text mode would rewrite CRLF. A failed call raises `GitError`, as `_git`'s does."""
    out = _git_raw(resolved["repo"], "cat-file", "--batch", stdin="".join(f"{sha}\n" for sha in shas).encode()).stdout
    texts: list[str | None] = [None] * len(shas)
    at = 0
    for index in range(len(shas)):
        end = out.find(b"\n", at)
        if end < 0:  # the answer stopped early: what it did not reach was not read
            break
        header, at = out[at:end].split(), end + 1
        if len(header) == 3:  # else `<name> missing`, and no content follows
            size = int(header[2])
            texts[index] = out[at : at + size].decode("utf-8", errors="replace")
            at += size + 1
    return texts


def head_listing(resolved: dict) -> list[tuple[str, str, str, str, str]] | None:
    """The head's tree as `ls-tree -l` lists it, or None where git could not list it: never an empty list, which would
    read as a head that holds nothing. Taken once a review, for the tree and the changed files' texts both."""
    try:
        return _listed(resolved)
    except GitError:
        return None


def tree_at_head(resolved: dict, listing: list[tuple[str, str, str, str, str]] | None) -> Tree | None:
    """The head's tree: every path, every Python file read and parsed once, and the Python files not read; None when
    the tree could not be listed.

    Two processes whatever the tree's size: `ls-tree -l` names each blob with its size, so a file over
    its cap or past the tree's costs nothing, and one batch reads the rest. A link is skipped uncounted: its target
    is read under its own name. A failed or timed-out listing is None, never an empty tree: measured with its exit
    code ignored, every changed file read as "a leaf". A batch past its timeout reads nothing, and every file it
    was asked for is named unread; so is a blob a partial clone lacks, which git lists with no size. `listing` is
    `head_listing`'s, taken once for the tree and the texts: a tree git could not list is None here too, never listed
    a second time.
    """
    if listing is None:
        return None
    paths = [path for _mode, _kind, _sha, _size, path in listing]
    python = ((path, sha, size) for path, sha, size in _plain(listing) if path.endswith(".py"))
    return Tree(paths, *_texts(resolved, python))


def _plain(listing: list[tuple[str, str, str, str, str]]) -> Iterator[tuple[str, str, str]]:
    """Each plain file of the listing as `(path, sha, size)`: a blob and no link, whose text is its own. A link's
    target is read under its own name, and a submodule's commits are another repository's."""
    return ((path, sha, size) for mode, kind, sha, size, path in listing if kind == "blob" and mode != "120000")


def _budgeted(blobs: Iterable[tuple[str, str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
    """Which of the `(path, sha, size)` blobs to read, each within its cap and all within the tree's budget, in
    order; and the paths left unread — one over its cap, past the budget, or sized "BAD", as a partial clone lists
    a blob it lacks, which nothing fetches to size."""
    wanted: list[tuple[str, str]] = []
    unread: list[str] = []
    budget = MAX_TREE_BYTES
    for path, sha, size_field in blobs:
        size = int(size_field) if size_field.isdigit() else None
        if size is None or size > min(MAX_FILE_BYTES, budget):
            unread.append(path)
        else:
            wanted.append((path, sha))
            budget -= size
    return wanted, unread


def _texts(resolved: dict, blobs: Iterable[tuple[str, str, str]]) -> tuple[dict[str, str], list[str]]:
    """The text of each `(path, sha, size)` blob within the caps, by path, from one batch; and the paths not read —
    over a cap or past the tree's budget, not given back by the batch, or read by no batch at all, one that failed
    or timed out — for the caller to name unread."""
    wanted, unread = _budgeted(blobs)
    try:
        texts = _blobs(resolved, [sha for _path, sha in wanted]) if wanted else []
    except GitError:
        texts = [None] * len(wanted)
    read = {path: text for (path, _sha), text in zip(wanted, texts, strict=True) if text is not None}
    return read, unread + [path for path, _sha in wanted if path not in read]


def read_texts(resolved: dict, files: list[dict], listing: list[tuple[str, str, str, str, str]] | None) -> None:
    """Each changed file's text at the head, for the file rules, set on its entry as `text`: a plain file the head
    holds within the caps, read in one batch. `unread_text` names one the head holds that was not read — over its
    cap, past the tree's budget, a blob the batch did not give back, or a tree that could not be listed — a hole for
    a rule that reads it whole; `shown` holds the lines its diff shows of it, by number, the ones added and those kept
    around them, for a rule that reads no more. A file the head does not hold, a binary, a link or a submodule has
    neither: nothing for a file rule to read, and the report names a binary under not read already."""
    for entry in files:
        entry["text"], entry["unread_text"] = None, None
    if listing is None:
        unread = [*files]
        for entry in files:
            entry["unread_text"] = "was not read: the head's tree could not be listed"
    else:
        blobs = {path: (sha, size) for path, sha, size in _plain(listing)}
        changed = [entry for entry in files if entry["path"] in blobs and not _BINARY.search(_header(entry["diff"]))]
        texts, _unread = _texts(resolved, ((entry["path"], *blobs[entry["path"]]) for entry in changed))
        unread = []
        for entry in changed:
            entry["text"] = texts.get(entry["path"])
            if entry["text"] is None:
                entry["unread_text"] = "was not read whole"
                unread.append(entry)
    for entry in unread:
        entry["shown"] = {number: line[1:] for number, line in _hunk_lines(entry["diff"]) if line[:1] != "-"}


def radius_of(tree: Tree | None, files: list[dict]) -> Blast:
    """What at the head depends on each changed file, from the tree `tree_at_head` gave, so the head is parsed once
    for the radius and the tree rules alike; None, a tree not listed, is a floor."""
    if tree is None:
        return blast_radius({}, files, listed=False)
    return blast_radius(tree.parsed, files, len(tree.unread))


#: What a reader, or `str.splitlines`, breaks a line at besides "\n": a lone `\r`, a form feed, U+2028 among them.
_BREAKS = re.compile("[\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")
_BINARY = re.compile(r"^Binary files .* differ$", re.MULTILINE)
_SUBMODULE = re.compile(r"^(?:new file mode|index \S+) 160000$", re.MULTILINE)


def readable(diff: str) -> str:
    """The diff with "\\n" its only line break: a CRLF file's `\\r` dropped, every other break shown as its escape.
    Measured: a lone `\\r` in an added line split it, so the key after it was no added line for the rule; and text
    after a form feed, with no diff marker, read as the prompt's own."""
    return _BREAKS.sub(lambda m: m.group().encode("unicode_escape").decode(), diff.replace("\r\n", "\n"))


def _header(diff: str) -> str:
    """A file's diff before its first hunk, where git prints a submodule's mode and "Binary files … differ": measured,
    searching whole diffs for them took 600 ms over a 66 MB `--all`, the headers alone 12."""
    return diff.partition("\n@@")[0]


def unread(entry: dict, diff: str, cut: bool, fits: bool, cap: int) -> str:
    """Why the guidelines could not read all of a changed file, or "" when they could: named, never read as "found
    nothing". A deleted file adds nothing: what it removes was reviewed when it came in. `fits` is False for a
    file past the block's cap."""
    if entry["status"] == "D":
        return ""
    if not diff.strip():
        return "git showed none of it"
    if _SUBMODULE.search(header := _header(diff)):
        return "a submodule: its commits are another repository's, not read here"
    if _BINARY.search(header):
        return "binary: no line of it can be read or quoted"
    if not fits:
        return f"past the {BLOCK_CAP_CHARS:,} characters the guidelines read in all; --path narrows a review"
    if cut:
        return f"cut at {cap:,} characters: the guidelines read only its start"
    return ""


def _fence(text: str) -> str:
    """A code fence longer than any run of backticks in `text`: measured, a markdown file's own fence of three,
    a context line of the change, closed the fence of three the change was written in."""
    return "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))


def _hunks(diff: str, aside: list[str] | None = None) -> Iterator[list[tuple[int, str]]]:
    """Each hunk of the diff, as `(new line number, line)` for every line inside it — added, removed or context,
    marker kept — and every line no hunk holds into `aside`, where one is given.

    The one place unified-diff syntax is read. Git's file headers come before
    the first `@@`, and inside a hunk anything that is not a change or context
    line — the next hunk's header, git's `\\ No newline at end of file`, the
    cap's cut marker — is not a line of the file. A removed line carries the
    number of the new line before it, 0 when there is none. Git names an empty
    range by the line before it: measured, `+0,0`, a deleted file's hunk, read
    as the line after it numbered every removed line -1.
    """
    hunk: list[tuple[int, str]] | None = None
    new_line = 0
    for raw in diff.split("\n"):  # git's line break, and no other
        if match := _HUNK_START.match(raw):
            if hunk:
                yield hunk
            new_line, hunk = int(match.group(1)) - (match.group(2) is None), []
        elif hunk is not None and raw[:1] in ("+", "-", " "):
            if raw[:1] != "-":  # a removed line is not in the new file
                new_line += 1
            hunk.append((new_line, raw))
            continue
        if aside is not None:
            aside.append(raw)
    if hunk:
        yield hunk


def _around_hunks(diff: str) -> list[str]:
    """The lines of `diff` no hunk holds, as git printed them, and each hunk's header again without git's guess at the
    function around it, which a quote may leave off."""
    aside: list[str] = []
    for _hunk in _hunks(diff, aside):  # read through for what it sets aside
        pass
    return aside + [line[: line.index(" @@", 2) + 3] for line in aside if _HUNK_START.match(line) and " @@ " in line]


def _hunk_lines(diff: str) -> Iterator[tuple[int, str]]:
    """`(new line number, line)` for every line inside the diff's hunks, one hunk after another."""
    for hunk in _hunks(diff):
        yield from hunk


def added_lines(diff: str) -> list[tuple[int, str]]:
    """`(new line number, text)` for every line the change added: a rule that needs "what was added, and where"
    takes this list, never the diff."""
    return [(number, raw[1:]) for number, raw in _hunk_lines(diff) if raw[:1] == "+"]


def quotable(diff: str) -> list[list[tuple[int, str]]]:
    """Each hunk's `(new line number, line)` pairs, the lines a guideline may quote from one file's diff as it read it,
    markers kept, hunk by hunk, since a quote stands in one hunk and never across two: nothing git or the cap wrote
    around them, which a finding could otherwise quote and pass as grounded."""
    return list(_hunks(diff))


def framed(path: str, shown: str) -> tuple[str, list[str]]:
    """A file's diff as each guideline reads it — under its heading, in a fence its own backticks cannot close — and
    the lines it was framed in there: the heading, the fence, and what git and the cap wrote around and inside its
    hunks. A guideline quotes a file as it was shown it, so grounding looks past these, and only these: measured by
    review with a list of such lines kept by hand beside this rendering, a file with no final newline and a file cut at
    the cap, each quoted whole, were dropped, git's `\\ No newline at end of file` and the cut's marker on no list."""
    fence = _fence(shown)
    heading, opening = f"### {squash(path)}", f"{fence}diff"
    return f"{heading}\n{opening}\n{shown}\n{fence}", [heading, opening, *_around_hunks(shown), fence]


def capped(diff: str, cap: int = DIFF_CAP_CHARS) -> tuple[str, bool]:
    """The diff as a model may read it: cut at the cap, with the cut named on a line of its own, and never inside a
    credential, as `cut_at` never cuts. The cut comes before the scrub, and half a credential no longer matches its
    shape: measured with the cut at the cap whatever it split, 30 of an AWS secret's 40 characters and four lines of a
    key's body reached every guideline."""
    return cut_at(diff, cap, sep="\n"), len(diff) > cap


def collect_evidence(
    repo: str,
    base: str | None,
    head: str,
    scope: Sequence[str] = (),
    *,
    staged: bool = False,
    tree_rules: bool = True,
    radius: bool = True,
    diff_cap: int = DIFF_CAP_CHARS,
    skipped: Callable[[str], str | None] | None = None,
) -> dict:
    """Everything the rules and the guidelines read, gathered in plain Python before any model runs: `preflight`, or
    `preflight_staged` for the index, then `gather`, with the options it takes.

    `files` carries each changed file with its WHOLE diff and its numbered
    added lines, for the rules, its text at the head, for the file rules, and the lines of its capped diff a guideline
    may quote, numbered too, for grounding; `diff` is the rendered block the guidelines read —
    one `### <path>` heading and a fenced diff per file, in path order, each
    capped and the block too, what it leaves out counted at its end, each path
    flattened because a newline in one forged a heading —
    the same block chat's checks node gathers for the scope its intake
    confirmed; `blast` is what at the head depends on each changed file, which the
    guidelines read beside that block and never inside it; `tree` is the head as the tree rules read it, or None where
    it could not be listed. One `git diff-tree -p` per file
    of a branch, never one for all of them split afterwards: git documents no reliable way to
    split a patch by file, and measured, a header can name two paths ambiguously
    and a type change prints two headers for one file; under `--all`, where every
    file is added, `whole_diffs` splits one safely. A branch's diffs run
    `GIT_WORKERS` at a time, which a profile with no guideline, and so no model to
    wait on, made worth it. With no `base`, every file at the head is the
    change, and `whole` says so; with a `scope`, only what lies under its folders is, and
    `outside` counts what was left out, for the report to name.
    """
    resolved = preflight_staged(repo) if staged else preflight(repo, base, head)
    if resolved["status"] != "success":
        return resolved
    return gather(resolved, scope, tree_rules=tree_rules, radius=radius, diff_cap=diff_cap, skipped=skipped)


def gather(
    resolved: dict,
    scope: Sequence[str] = (),
    *,
    tree_rules: bool = True,
    radius: bool = True,
    diff_cap: int = DIFF_CAP_CHARS,
    skipped: Callable[[str], str | None] | None = None,
) -> dict:
    """The evidence `collect_evidence` describes, for a scope `preflight` resolved: what the chat's checks node gathers
    for the commits the intake confirmed, never resolving their names again — measured, a head given as a sha resolved
    through no ref, and the rules reviewed nothing while the guidelines reviewed the change."""
    base, head = resolved["base"] or None, resolved["head"]
    files: list[dict] = []
    blocks: list[str] = []
    try:
        listed = list_changed(resolved)
        entries = [entry for entry in listed if in_scope(entry["path"], scope)]
        if base is None:  # every file is added: one call reads them all
            diffs = whole_diffs(resolved, entries, scope)
        else:
            with ThreadPoolExecutor(max_workers=GIT_WORKERS) as pool:  # git processes to wait on: threads suffice
                diffs = list(pool.map(lambda entry: diff_of(resolved, entry["path"], entry.get("old", "")), entries))
        room, left_out, excluded = BLOCK_CAP_CHARS, 0, {}
        for entry, diff in zip(entries, diffs, strict=True):
            file = {
                **entry,
                "diff": diff,
                "added": added_lines(diff),
                "quotable": [],
                "framing": [],
                "unread": "",
                "cut": False,
            }
            files.append(file)
            # A file the operator's skip list names: the rules read it as any other, the guidelines are not shown it,
            # and it is named and counted, never a hole. The operator chose it, in the tool's own config: a branch
            # cannot, and code it hides behind such a name is still every rule's to read.
            if skipped is not None and (glob := skipped(entry["path"])):
                excluded[entry["path"]] = glob
                continue
            shown, cut = capped(diff, diff_cap)
            block, framing = framed(entry["path"], shown)
            fits = len(block) <= room
            # The guidelines' limits alone: the rules read every added line.
            file.update(unread=unread(entry, diff, cut, fits, diff_cap), cut=cut or not fits)
            if fits:  # a file no guideline read, none may quote
                file.update(quotable=quotable(shown), framing=framing)
                blocks.append(block)
                room -= len(block) + 2  # and the blank line joining it to the next
            else:
                left_out += 1
        notes = []
        if left_out:  # named to the guidelines too: a test left out is no test missing
            notes.append(f"[… {left_out} more file(s) not shown: past {BLOCK_CAP_CHARS:,} characters in all]")
        if excluded:
            notes.append(f"[… {len(excluded)} more file(s) not shown: the operator's skip list leaves them out]")
        blocks.extend(notes)
        away = untracked(resolved) if resolved.get("staged") else None
    except GitError as exc:  # the change half read is not the change: named, never reviewed as it stands
        return {"status": "error", "error_message": str(exc)}
    if not files:  # measured: a branch already merged, or a base equal to its head, approved a review of nothing
        return {"status": "error", "error_message": _nothing(head, base, scope if listed else ())}
    listing = head_listing(resolved)
    # The head read whole, every Python file parsed, only for what reads it: a tree rule, or the radius a guideline
    # is shown, which measures changed Python files alone. The changed files' own texts are read either way, for the
    # file rules.
    read = tree_rules or (radius and any(Radius(entry["path"]).measured for entry in files))
    tree = tree_at_head(resolved, listing) if read else None
    read_texts(resolved, files, listing)
    for entry in files:  # the added lines' numbers, which the report reads after the rules have let the lines go
        entry["added_at"] = frozenset(number for number, _text in entry["added"] or ())
    if read:
        blast = radius_of(tree, files)
    elif radius:  # asked of files it does not measure, it names each one with no tree to read
        blast = blast_radius({}, files)
    else:
        blast = Blast((), (), 0, asked=False)
    return {
        "status": "success",
        "preflight": resolved,
        "files": files,
        "diff": "\n\n".join(blocks),
        "blast": blast,
        "tree": tree,
        "whole": base is None,
        "scope": list(scope),
        "skipped": excluded,
        "untracked": away,
        "outside": len(listed) - len(entries),
    }


def _nothing(head: str, base: str | None, scope: Sequence[str]) -> str:
    """Why there is nothing to review: the head holds no file, adds nothing to the base, or nothing under the scope."""
    if scope:
        what = f"no file at {head!r}" if base is None else f"nothing {head!r} changes"
        return f"{what} lies under {', '.join(scope)}: no change to review"
    if base is None:
        return f"{head!r} holds no file: nothing to review"
    return f"{head!r} adds nothing to {base!r}: no change to review"

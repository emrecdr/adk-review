"""Read-only git, gathered without a model.

Git runs as an argv list with a timeout, never through a shell, only with
verbs that read, decoded as UTF-8. `preflight` and `collect_evidence` answer
as envelopes, for the driver and the tools; the helpers between them return
bare values, for code. `collect_evidence` runs the same calls in plain Python
so the driver can gather everything the rules and the guidelines read
before any model is called, the `Evidence` that `core/evidence.py` spells;
what is decided from the diffs alone is `change.py`'s, and what a push sends,
commit by commit, `history.py`'s. Nothing here knows ADK: the model-facing
tools that wrap these live in `judge/tools.py`.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import shutil
import string
import subprocess
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.blast import Radius
from ..core.evidence import LINK_UNREAD, Entry, Evidence, Failure, unreleased
from ..core.paths import Links, UnfollowedError, in_review, matches
from ..core.rules import PYTHON_SUFFIXES, Tree
from ..core.text import lines_of
from .change import (
    BINARY_UNREAD,
    POINTER_UNREAD,
    lfs_pointer,
    no_change,
    pointer_text,
    radius,
    shown_to_guidelines,
    widens,
)
from .diff import (
    BINARY,
    DIFF_CAP_CHARS,
    LINK_MODE,
    SUBMODULE_MODE,
    headers,
    hunk_lines,
    readable,
    unguessed,
)

GIT_TIMEOUT_S = 30
#: The tree the blast radius reads is bounded, and what is not read is counted: over a partial tree a count is a
#: floor, and says so. Measured at 80 to 142 ms a megabyte to parse and resolve (ADK's 6.3 MB, LiteLLM's 32.3);
#: a file cap alone let a branch of 5,000 one-megabyte files in. The largest real file seen was 0.8 MB.
MAX_TREE_BYTES = 20_000_000
MAX_FILE_BYTES = 1_000_000
#: Git reads a file as binary where a NUL lies in its first 8,000 bytes, and the rules read a link's target so too.
BINARY_BYTES = 8_000
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


def _environment(repo: str, settings: Mapping[str, str] | None = None) -> dict[str, str]:
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
    # A call's own `settings` first, so that none of them can move what is fixed for every call.
    settled = dict(settings or {}) | _SETTLED | {"safe.directory": Path(os.path.realpath(repo)).as_posix()}
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


def _git(
    repo: str,
    *args: str,
    strip: bool = True,
    ok: tuple[int, ...] = (0,),
    settings: Mapping[str, str] | None = None,
) -> tuple[int, str]:
    """One read-only git command: an argv list, no shell, a timeout, no fetch, stdout captured, decoded as UTF-8.

    An exit code outside `ok` raises `GitError` with git's own first line, and so does a call past the timeout,
    killed, naming the limit, for the envelope to answer: some answers below are read without their exit
    code, and a failure read as an empty answer would read as nothing there. `ok` names the codes a caller
    reads as an answer: 1 is `rev-parse`'s for a ref that does not exist. `strip` is off for a file's content,
    where the line numbers must survive, and for a folder's path, whose name may end in a space. `settings` are the
    call's own, set as the fixed ones are, through `_environment`.
    """
    done = _git_raw(repo, *args, ok=ok, settings=settings)
    return done.returncode, _text(done.stdout, strip)


def _text(raw: bytes, strip: bool = True) -> str:
    """git's answer decoded here, never in text mode, which reads a lone `\\r` inside a line as a break, and trimmed of
    ASCII whitespace alone: measured by review, `str.strip` cut a branch's closing U+2028 and read it as another."""
    out = raw.decode("utf-8", "replace")
    return out.strip(string.whitespace) if strip else out


def _git_raw(
    repo: str,
    *args: str,
    stdin: bytes | None = None,
    ok: tuple[int, ...] = (0,),
    settings: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """`_git`'s call with its answer left as bytes, what a batch read counts its sizes in."""
    if (git := on_path("git")) == "git":  # not on PATH: Git for Windows can be installed for Git Bash alone
        raise GitError(f"git {_verb(args)} could not start: git is not on PATH")
    try:
        done = _run([git, "-C", repo, *args], _environment(repo, settings), stdin)
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


class _ScopeError(Exception):
    """A scope `preflight` refuses, and why: answered as the error envelope a `GitError` is, from one place."""


def _refused(repo: str, root: str) -> None:
    """`_ScopeError` where `repo` is no repository to review: no `.git` in it, or a `.git` that is no repository's top.
    Measured: a `.git` that is no repository, an empty folder say, sent git to the repository around it, and a review
    read the outer repository's commits under the inner folder's name. Raises `GitError` as `_git` does."""
    if not os.path.exists(root):
        raise _ScopeError(f"{repo} does not exist")
    if not is_repository(root):
        raise _ScopeError(f"{repo} is not a git repository (no .git directory)")
    top = _git(root, "rev-parse", "--show-toplevel", strip=False)[1].removesuffix("\n")
    if not same_folder(top, root):
        raise _ScopeError(f"{repo} is no repository's top: git answers for {top}")


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
        _refused(repo, root)
        base_ref, head_ref = _full_ref(root, "base branch", base), _full_ref(root, "head branch", head)
        base_sha, head_sha = _sha(root, base) if base is not None else "", _sha(root, head)
        if base_sha is None:
            raise _ScopeError(f"base branch {base!r} does not exist in {repo}")
        if head_sha is None:
            raise _ScopeError(f"head branch {head!r} does not exist in {repo}")
        # From here on git only sees commit hashes, which cannot be mistaken for options.
        if base is None:
            base_sha = merge_base = _empty_tree(root)
            span = head_sha
        else:
            merge_base = _merge_base(repo, root, (base, base_sha), (head, head_sha))
            span = f"{merge_base}..{head_sha}"
        _, ahead = _git(root, "rev-list", "--count", span)
    except (GitError, _ScopeError) as exc:
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
        "base_ref": base_ref,
        "head_ref": head_ref,
    }


def _full_ref(root: str, label: str, ref: str | None) -> str:
    """The one full ref `ref` resolved through, "" where it names none, so a report names a tag; `_ScopeError` where
    the name could mean more than one."""
    names = _namesakes(root, ref) if ref is not None else []
    if len(names) > 1:
        raise _ScopeError(f"{label} {ref!r} is ambiguous: {' and '.join(names)}. Name one in full")
    return names[0] if names else ""


def _merge_base(repo: str, root: str, base: tuple[str, str], head: tuple[str, str]) -> str:
    """The merge base of `base` and `head`, each `(name, sha)`; `_ScopeError` where none can be trusted."""
    (base_name, base_sha), (head_name, head_sha) = base, head
    # Cut at a depth, as CI checks out, a clone can name the wrong merge base, unless the base is one of the head's own
    # commits: then it is the merge base, whatever lies past the cut. Measured by review: every push from a shallow
    # clone was stopped, its base always one of the head's commits.
    if (
        _git(root, "rev-parse", "--is-shallow-repository")[1] == "true"
        and _git(root, "merge-base", "--is-ancestor", base_sha, head_sha, ok=(0, 1))[0] != 0
    ):
        why = "a merge base found in one can be the wrong one: fetch its history first, git fetch --unshallow"
        raise _ScopeError(f"{repo} is a shallow clone, and {why}")
    code, merge_base = _git(root, "merge-base", base_sha, head_sha, ok=(0, 1))
    if code == 1:  # git's answer for two commits with no ancestor in common
        raise _ScopeError(f"{base_name!r} and {head_name!r} share no history: no change to review")
    return merge_base


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
        _refused(repo, root)
        head = _sha(root, "HEAD") or _empty_tree(root)  # no commit yet: the index against the empty tree
        # What names the index as a commit names its tree: each entry `ls-files -s` lists, its mode, blob and path.
        # Read by review: named by a constant, a change staged outside the diffs reviewed served a stale verdict.
        _, listed = _git(root, "ls-files", "-s", "-z", strip=False)
    except (GitError, _ScopeError) as exc:
        return {"status": "error", "error_message": str(exc)}
    return {
        "status": "success",
        "repo": root,
        "base": "HEAD",
        "base_sha": head,
        "head": "the index",
        "head_sha": "staged",
        "index_sha": hashlib.sha256(listed.encode()).hexdigest(),
        "merge_base": head,
        "commits_ahead": 0,
        "base_ref": "",
        "head_ref": "",
        "staged": True,
    }


def list_changed(resolved: dict) -> list[dict[str, str]]:
    """`[{"path", "status", "mode"}]` for the change, from NUL-separated records so odd paths survive: `mode` the
    file's at the head, `120000` a link's, known from the change itself, where a link moved whole shows no mode in its
    diff and the head's tree may not be listed; a rename also carries its `old` path, `-M` finding renames whatever
    the reviewer's `diff.renames` says."""
    if resolved.get("staged"):  # the index against the base: what the next commit would change
        _, out = _git(resolved["repo"], "diff-index", "--cached", "-r", "--raw", "-z", "-M", resolved["merge_base"])
    else:
        _, out = _git(
            resolved["repo"], "diff-tree", "-r", "--raw", "-z", "-M", resolved["merge_base"], resolved["head_sha"]
        )
    parts = iter(p for p in out.split("\0") if p)
    files: list[dict[str, str]] = []
    for field in parts:
        # `:<mode> <mode> <sha> <sha> <status>`, the base's then the head's; a rename (`R<score>`) or a copy
        # (`C<score>`) names the old path first, and the new one exists.
        _was, mode, _old, _new, status = field.removeprefix(":").split(" ")
        old = {"old": next(parts)} if status[0] in "RC" else {}
        files.append({"path": next(parts), "status": status[0], "mode": mode, **old})
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
#: Where a Python function or class begins, as git's own `python` diff driver says: `--function-context` widens each
#: hunk to the one around it. Set for git's `default` driver, the one a file with no diff attribute has, through the
#: environment as the fixed settings are, and only on a Python file's diff, so the repository's own attributes still
#: choose and no attributes file is written.
PYTHON_FUNCTION = "^[ \t]*((class|(async[ \t]+)?def)[ \t].*)$"


def diff_of(resolved: dict, path: str, old: str = "", *, functions: bool = False) -> str:
    """One file's whole unified diff, as git prints it for no one's settings and with "\\n" its only line break: the
    path taken literally, never as a pattern; the merge base's attributes, never the branch's own; no colour, no
    external diff, no text conversion; with `functions`, each hunk widened to the Python function or class around it.
    Measured: each of those once hid a planted key, the branch's `-diff` or a path spelled `:config.py` among them.
    `--attr-source` is git 2.41's. A rename is diffed with its `old` path too, so it reads as the lines it moved
    and changed; by the new path alone, it read as a whole file added. Each path is a file and never a folder:
    measured, `a` renamed to `b.py` beside a new `a/x.py` brought `a/x.py`'s key into `b.py`'s diff."""
    repo, base, head = resolved["repo"], resolved["merge_base"], resolved["head_sha"]
    paths = _literally(old, path)
    settings = {"diff.default.xfuncname": PYTHON_FUNCTION} if functions else None
    shown = (*_PLAIN, "--function-context") if functions else _PLAIN
    attrs = f"--attr-source={base}"
    if resolved.get("staged"):  # the index's text, against the base, as the next commit would hold it
        _, diff = _git(repo, attrs, "diff-index", "--cached", "-p", *shown, base, "--", *paths, settings=settings)
    else:
        _, diff = _git(repo, attrs, "diff-tree", "-r", "-p", *shown, base, head, "--", *paths, settings=settings)
    return unguessed(readable(diff))


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
    asked = {entry["path"] for entry in entries}
    if len(parts) != len(paths) or not asked <= set(paths):
        raise GitError("git diff-tree's one patch did not split into the files it lists")
    # Only the files asked for: a third-party folder's are in git's patch, never read. Left out by git's own pathspec
    # instead, a glob git reads otherwise than `matches` could leave out a file of the project's own.
    pairs = sorted(((path, part) for path, part in zip(paths, parts, strict=True) if path in asked), key=lambda p: p[0])
    return [unguessed(readable(_text(part))) for _, part in pairs]


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
        entries.append((mode, "commit" if mode == SUBMODULE_MODE else "blob", sha, "-", path))
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


class _Binary(str):
    """A blob's text that git reads as binary, a NUL in its first `BINARY_BYTES` bytes, found in the bytes the batch
    gave before they were decoded: decoded still, for a caller that reads every file as text, and known by its kind
    for one that asks. Read by review, the NUL was looked for in the decoded text, which counts characters, or bytes
    again after a U+FFFD stood for one byte git read or three: a file git read as text was binary, or the reverse."""

    __slots__ = ()


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
            raw = out[at : at + size]
            text = raw.decode("utf-8", errors="replace")
            texts[index] = _Binary(text) if b"\x00" in raw[:BINARY_BYTES] else text
            at += size + 1
    return texts


def head_listing(resolved: dict) -> list[tuple[str, str, str, str, str]] | None:
    """The head's tree as `ls-tree -l` lists it, or None where git could not list it: never an empty list, which would
    read as a head that holds nothing. Taken once a review, for the tree and the changed files' texts both."""
    try:
        return _listed(resolved)
    except GitError:
        return None


def tree_at_head(
    resolved: dict, listing: list[tuple[str, str, str, str, str]] | None, third_party: Sequence[str] = ()
) -> Tree | None:
    """The head's tree: every path, every Python file read and parsed once, and the Python files not read; None when
    the tree could not be listed.

    Two processes whatever the tree's size: `ls-tree -l` names each blob with its size, so a file over
    its cap or past the tree's costs nothing, and one batch reads the rest. A link is skipped uncounted: its target
    is read under its own name. A failed or timed-out listing is None, never an empty tree: measured with its exit
    code ignored, every changed file read as "a leaf". A batch past its timeout reads nothing, and every file it
    was asked for is named unread; so is a blob a partial clone lacks, which git lists with no size. `listing` is
    `head_listing`'s, taken once for the tree and the texts: a tree git could not list is None here too, never listed
    a second time. A module under a folder `third_party` names is another project's: listed, never read or parsed.
    """
    if listing is None:
        return None
    paths = [path for _mode, _kind, _sha, _size, path in listing]
    python = (
        (path, sha, size)
        for path, sha, size in _plain(listing)
        if path.endswith(".py") and not matches(path, *third_party)
    )
    return Tree(paths, *_texts(resolved, python))


def _plain(listing: list[tuple[str, str, str, str, str]]) -> Iterator[tuple[str, str, str]]:
    """Each plain file of the listing as `(path, sha, size)`: a blob and no link, whose text is its own. A link's
    target is read under its own name, and a submodule's commits are another repository's."""
    return ((path, sha, size) for mode, kind, sha, size, path in listing if kind == "blob" and mode != LINK_MODE)


def _links(listing: list[tuple[str, str, str, str, str]] | None) -> dict[str, tuple[str, str]]:
    """Each symbolic link at the head as `(sha, size)` by its path: a link is known by its mode, which a link moved
    whole prints in no diff."""
    return {path: (sha, size) for mode, kind, sha, size, path in listing or () if kind == "blob" and mode == LINK_MODE}


def _followed(
    resolved: dict, files: list[Entry], listing: list[tuple[str, str, str, str, str]], blobs: Mapping[str, object]
) -> list[tuple[Entry, str]]:
    """Each changed link with the plain file at the head it runs as, as `Links.follow` follows it: every link at the
    head read in one batch, the changed ones first within the tree's budget. One whose way is not known keeps its
    hole, `unread_text` naming why; one naming no file here has none, and no text: there is nothing here to run."""
    links = _links(listing)
    starts = [entry for entry in files if entry["path"] in links]
    if not starts:
        return []
    first = {entry["path"] for entry in starts}
    order = sorted(links, key=lambda path: path not in first)
    read, _unread = _texts(resolved, ((path, *links[path]) for path in order))
    modules = (path for _mode, kind, _sha, _size, path in listing if kind == "commit")
    head = Links(blobs, {path: read.get(path) for path in links}, modules)
    through = []
    for entry in starts:
        try:
            target = head.follow(entry["path"])
        except UnfollowedError as why:
            entry["unread_text"] = f"was not followed to the file it names: {why}"
            continue
        if target is None:  # it names no file here: nothing to run, and nothing unread
            entry["unread_text"] = None
        else:
            through.append((entry, target))
    return through


def _read_first(blob: tuple[str, str, str]) -> tuple[int, int]:
    """Where a changed file's text comes in the budget: Python's, then a file with no suffix, a script maybe, then the
    rest, each the smallest first; one git gave no size for, last."""
    path, _sha, size = blob
    return _kind(path), int(size) if size.isdigit() else MAX_TREE_BYTES


def _kind(path: str) -> int:
    """0 for Python's own file, 1 for one with no suffix, a script maybe, each parsed and linted alone; else 2."""
    suffix = PurePosixPath(path).suffix
    return 0 if suffix in PYTHON_SUFFIXES else 1 if not suffix else 2


def _not_read(entry: Entry, why: str, skipped: Collection[str]) -> None:
    """`entry` named not read, `why` the report's sentence, unless the operator's skip list, `skipped`, names it:
    that stays its choice."""
    if entry["path"] not in skipped:
        entry["unread"] = why


def _as_linked(through: list[tuple[Entry, str]], texts: Mapping[str, str], skipped: Collection[str]) -> None:
    """Each link's text, `texts` holding it under the link's path, read as the file it runs: a binary one, as git reads
    one by its first 8,000 bytes, or a Git LFS pointer, its file stored elsewhere, named not read as that file is, and
    no hole of the rules'; one not read a hole still, its why named; else every line of it added under the link's
    name, for the report as for the rules, and no hole."""
    for entry, target in through:
        text = texts.get(entry["path"])
        entry["target"] = target
        if text is None:
            entry["unread_text"] = "was not read whole"
            continue
        entry["unread_text"] = None
        # Named not read as the file itself is, unless the skip list names it. Read by review: a link to a binary file
        # was read as its own path and approved, with no model, where the file was a hole; and one to a pointer, a
        # hole for each rule, stopped every push where a plain pointer of its kind was no hole.
        if isinstance(text, _Binary):
            _not_read(entry, BINARY_UNREAD, skipped)
        elif pointer_text(text):
            _not_read(entry, POINTER_UNREAD, skipped)
        else:
            entry["text"], entry["added_at"] = text, frozenset(range(1, len(lines_of(text)) + 1))


def _split(
    entries: list[dict], scan: Sequence[str], third_party: Sequence[str]
) -> tuple[list[dict], list[dict], list[str]]:
    """The changed files a review reads; the project's own of every other kind, for the credential rules alone, never
    a submodule or another project's code; and the paths of every file the review ignores. A file moved or copied out
    of one its readers never saw is read whole, as one the change adds: no review read it where it lay."""
    own = [entry for entry in entries if _reviewed(entry, scan, third_party)]
    kept = {entry["path"] for entry in own}
    others = [
        entry
        for entry in entries
        if entry["path"] not in kept and entry["mode"] != SUBMODULE_MODE and not matches(entry["path"], *third_party)
    ]
    _added_whole(own, lambda old: not in_review(old, scan, third_party))
    _added_whole(others, lambda old: matches(old, *third_party))
    return own, others, sorted({entry["path"] for entry in entries} - kept)


def _added_whole(entries: list[dict], unseen: Callable[[str], bool]) -> None:
    """Each entry moved or copied out of a path `unseen` names, read as one the change adds."""
    for entry in entries:
        if "old" in entry and unseen(entry["old"]):
            del entry["old"]
            entry["status"] = "A"


def _reviewed(entry: Mapping[str, str], scan: Sequence[str], third_party: Sequence[str]) -> bool:
    """Whether a review reads a changed file, as `in_review` says; a submodule, another repository's commit and a hole,
    by its folder alone. Read by review: one named `nanopb-0.4.7` read as a file with the suffix `.7`, ignored."""
    if entry["mode"] == SUBMODULE_MODE:
        return not matches(entry["path"], *third_party)
    return in_review(entry["path"], scan, third_party)


def _links_unread(files: list[Entry], skipped: Mapping[str, str], read: Callable[[str], bool]) -> None:
    """A symbolic link runs as the file it names, which its diff never shows: for the guidelines a hole, named on the
    link, unless the change showed them that file whole, as it shows a file it adds, or the file is a kind the review
    `read`s not, which no guideline is shown. Read by review: `run.py`, a link to a skip-listed image holding Python,
    was approved, read by no one; a file the change only modified counted as shown whole, one hunk of it shown; a link
    moved whole, which prints no mode, was not known for one; and a link to an ignored script was a hole for good."""
    whole = {
        entry["path"]
        for entry in files
        if entry["status"] == "A" and not entry["unread"] and entry["path"] not in skipped and not entry.get("link")
    }
    for entry in files:
        target = entry.get("target")
        if entry.get("link") and not entry["unread"] and target not in whole and (target is None or read(target)):
            _not_read(
                entry,
                "a symbolic link: the guidelines read the path it names, and were not shown that file whole",
                skipped,
            )


def _budgeted(blobs: Iterable[tuple[str, str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
    """Which of the `(path, sha, size)` blobs to read, each within its cap and all within the tree's budget, in
    order; and the paths left unread — one over its cap, past the budget, or sized "BAD", as a partial clone lists a
    blob it lacks, which nothing fetches to size. A blob read once is charged once, but for each path parsed and linted
    as its own, Python's or a script's: read by review, 24 links to one config spent the budget and the last were
    holes, where 24 links to one module would be parsed and linted 24 times."""
    wanted: list[tuple[str, str]] = []
    unread: list[str] = []
    budget = MAX_TREE_BYTES
    taken: set[str] = set()
    for path, sha, size_field in blobs:
        size = int(size_field) if size_field.isdigit() else None
        if sha in taken and _kind(path) == 2:  # read once for another path, and read here as text alone
            wanted.append((path, sha))
        elif size is None or size > min(MAX_FILE_BYTES, budget):
            unread.append(path)
        else:
            wanted.append((path, sha))
            taken.add(sha)
            budget -= size
    return wanted, unread


def _texts(resolved: dict, blobs: Iterable[tuple[str, str, str]]) -> tuple[dict[str, str], list[str]]:
    """The text of each `(path, sha, size)` blob within the caps, by path, from one batch, each blob read once however
    many paths hold it; and the paths not read —
    over a cap or past the tree's budget, not given back by the batch, or read by no batch at all, one that failed
    or timed out — for the caller to name unread."""
    wanted, unread = _budgeted(blobs)
    shas = list(dict.fromkeys(sha for _path, sha in wanted))  # a blob read once, however many paths hold it
    try:
        texts = dict(zip(shas, _blobs(resolved, shas), strict=True)) if shas else {}
    except GitError:
        texts = {}
    read = {path: text for path, sha in wanted if (text := texts.get(sha)) is not None}
    return read, unread + [path for path, _sha in wanted if path not in read]


def _no_text(diff: str) -> bool:
    """Whether the file the diff leaves holds no text a rule could read as it: binary, or a Git LFS pointer, whose
    lines are not the file. Measured by review: ruff read a pointer committed as `tool.py` as Python, and four major
    findings of invalid syntax stood on its three lines."""
    return BINARY.search(headers(diff)) is not None or lfs_pointer(diff)


def read_texts(
    resolved: dict,
    files: list[Entry],
    listing: list[tuple[str, str, str, str, str]] | None,
    skipped: Collection[str] = (),
    others: Sequence[Entry] = (),
) -> None:
    """Each changed file's text at the head, for the file rules, set on its entry as `text`: a plain file the head holds
    within the caps, read in one batch, and a link as the file it runs. `unread_text` names one the head holds that was
    not read — over its cap, past the tree's budget, a blob the batch did not give back, a tree that could not be
    listed, a link whose way could not be followed — a hole for a rule that reads it whole, and for a link one for
    each line rule too: a link is one from its birth, until the file it names is read as text, names no file here, or
    is binary or a Git LFS pointer, which the report names not read; `shown` holds the lines its diff shows of
    it, by number, the ones added and those kept around them, for a rule that reads no more. A file the head does not
    hold, a binary, a Git LFS pointer, a link naming no text file there or a submodule has neither: nothing for a file
    rule to read, and the report names a binary and a pointer under not read already. A pointer only its text shows, its
    diff showing none of its opening, is named here, unless the operator's skip list, `skipped`, names it. `others` are
    the links `scan` leaves out, read for the credential rules alone: last in the budget, and a hole for them where
    their file was not read, unless the skip list names them. Read by review: one past the size limits, made no hole,
    approved the key in the file it named, where a plain file of its kind is read through its own diff."""
    for entry in [*files, *others]:
        # A link's lines are the file it names: a hole for each rule that reads it until that file is read as text.
        # Read by review, round after round, a link one step of the read passed over was read as its path and approved.
        entry["text"], entry["unread_text"] = None, LINK_UNREAD if entry.get("link") else None
    if listing is None:
        unread = [*files]
        for entry in [*files, *others]:
            entry["unread_text"] = "was not read: the head's tree could not be listed"
    else:
        blobs = {path: (sha, size) for path, sha, size in _plain(listing)}
        changed = [entry for entry in files if entry["path"] in blobs and not _no_text(unreleased(entry["diff"]))]
        # A link runs as the file it names, so the rules read that file under the link's name. Read by review: a link
        # had no text, and `run.py` naming an image that held Python was linted by no rule.
        through = _followed(resolved, [*files, *others], listing, blobs)
        wanted = [(entry["path"], *blobs[entry["path"]]) for entry in changed]
        wanted += [(entry["path"], *blobs[target]) for entry, target in through]
        # What a rule reads whole first, Python's, then a file with no suffix, a script, then the rest, each the
        # smallest first: read by review, in their paths' order, 20 MB of JSON fixtures in `data/` spent the budget
        # before `src/app.py`, which lint then could not read, and a first push stopped. The links `scan` leaves out
        # come last: read by review, 24 links to pages took the budget from a config the review reads.
        aside = {entry["path"] for entry in others}
        texts, _unread = _texts(resolved, sorted(wanted, key=lambda blob: (blob[0] in aside, *_read_first(blob))))
        _as_linked(through, texts, skipped)
        unread = []
        for entry in changed:
            entry["text"] = texts.get(entry["path"])
            if entry["text"] is None:
                entry["unread_text"] = "was not read whole"
                unread.append(entry)
            elif pointer_text(entry["text"]):  # read by review: one its diff showed no opening of was linted as Python
                entry["text"] = None
                _not_read(entry, POINTER_UNREAD, skipped)
    for entry in unread:
        entry["shown"] = {number: line[1:] for number, line in hunk_lines(unreleased(entry["diff"])) if line[:1] != "-"}
    for entry in others:  # the operator's skip list clears a link the review ignores, as it clears a binary file
        if entry["path"] in skipped:
            entry["unread_text"] = None


def collect_evidence(
    repo: str, base: str | None, head: str, scope: Sequence[str] = (), *, staged: bool = False, **options: Any
) -> Evidence | Failure:
    """Everything the rules and the guidelines read, gathered in plain Python before any model runs: `preflight`, or
    `preflight_staged` for the index, then `gather`, with the `options` it takes, declared there alone.

    `core/evidence.py` spells what comes back: each changed file, the block the guidelines read, the blast radius and
    the head's tree, each key once. One `git diff-tree -p` per file of a branch, never one for all of them split
    afterwards: git documents no reliable way to split a patch by file, and measured, a header can name two paths
    ambiguously and a type change prints two headers for one file; under `--all`, where every file is added,
    `whole_diffs` splits one safely. A branch's diffs run `GIT_WORKERS` at a time, which a profile with no guideline,
    and so no model to wait on, made worth it. With no `base`, every file at the head is the change, and `whole` says
    so; with a `scope`, only what lies under its folders is, and `outside` counts what was left out, for the report to
    name.
    """
    resolved = preflight_staged(repo) if staged else preflight(repo, base, head)
    if resolved["status"] != "success":
        return {"status": "error", "error_message": resolved["error_message"]}
    return gather(resolved, scope, **options)


def gather(
    resolved: dict,
    scope: Sequence[str] = (),
    *,
    tree_rules: bool = True,
    guidelines: bool = True,
    diff_cap: int = DIFF_CAP_CHARS,
    skip: Sequence[str] = (),
    scan: Sequence[str] = (),
    third_party: Sequence[str] = (),
) -> Evidence | Failure:
    """The evidence `collect_evidence` describes, for a scope `preflight` resolved: what the chat's checks node gathers
    for the commits the intake confirmed, never resolving their names again — measured, a head given as a sha resolved
    through no ref, and the rules reviewed nothing while the guidelines reviewed the change. A changed file `in_review`
    does not take, neither Python's nor a config's or another project's code, is ignored: never diffed, read or shown,
    only listed; a change of nothing else is one of no file reviewed, never "no change", which would stop its push."""
    base, head = resolved["base"] or None, resolved["head"]
    try:
        listed = list_changed(resolved)
        entries = [entry for entry in listed if in_scope(entry["path"], scope)]
        own, others, ignored = _split(entries, scan, third_party)
        files, block, excluded = _read_change(resolved, own, scope, guidelines=guidelines, diff_cap=diff_cap, skip=skip)
        unscanned, _block, aside = _read_change(resolved, others, scope, guidelines=False, diff_cap=diff_cap, skip=skip)
        away = untracked(resolved) if resolved.get("staged") else None
    except GitError as exc:  # the change half read is not the change: named, never reviewed as it stands
        return {"status": "error", "error_message": str(exc)}
    if not files and not ignored:  # measured: a branch already merged, or a base equal to its head, approved nothing
        return no_change(head, base, scope if listed else ())
    listing = head_listing(resolved)
    # The head read whole, every Python file parsed, only for what reads it: a tree rule, or the radius a guideline
    # is shown, which measures changed Python files alone. The changed files' own texts are read either way, for the
    # file rules.
    read = tree_rules or (guidelines and any(Radius(entry["path"]).measured for entry in files))
    tree = tree_at_head(resolved, listing, third_party) if read else None
    # A link the scan leaves out is followed too: the credential rules read it, as the file it names. Read by review,
    # `notes.txt`, a link to a file holding a key, was read as its own path and approved.
    read_texts(resolved, files, listing, {**excluded, **aside}, [entry for entry in unscanned if entry.get("link")])
    if guidelines:
        _links_unread(files, excluded, lambda path: in_review(path, scan, third_party))
    return {
        "status": "success",
        "preflight": resolved,
        "files": files,
        "diff": block,
        "blast": radius(tree, files, read=read, asked=guidelines),
        "tree": tree,
        "whole": base is None,
        "scope": list(scope),
        "skipped": excluded,
        "unscanned": unscanned,
        "ignored": ignored,
        "untracked": away,
        "outside": len(listed) - len(entries),
    }


def _read_change(
    resolved: dict, entries: list[dict], scope: Sequence[str], *, guidelines: bool, diff_cap: int, skip: Sequence[str]
) -> tuple[list[Entry], str, dict[str, str]]:
    """Each entry's diff, as the rules read it, and the block the guidelines read, as `shown_to_guidelines` builds it.
    Where a guideline reads the change, each Python hunk `widens` names is read within the function or class around
    it, as Claude Code's review reads the enclosing function of each hunk: git's three lines showed an edit deep in a
    method without its `def`, its arguments or its other branches. The context never costs a file its reading, as the
    block counts it: a file widening pushes past `diff_cap` is read again as git prints it, and so is every widened
    file where the block leaves a file out. Measured over this repository's last 31 commits, the widened Python diffs
    were 1.7 times git's and one file of 191 went past the cap; measured by review, a total of the raw diffs, a
    skip-listed lockfile's among them and the framing uncounted, read whole changes as git prints them that the
    widened block held. The added lines, all the rules read, are the same either way."""
    wide = [at for at, entry in enumerate(entries) if guidelines and widens(entry, skip)]
    diffs = _diffs(resolved, entries, scope, frozenset(wide))
    over = {at for at in wide if len(diffs[at]) > diff_cap}
    _narrowed(resolved, entries, diffs, sorted(over))
    files, block, excluded, left_out = shown_to_guidelines(entries, diffs, diff_cap, skip, shown=guidelines)
    if left_out and (rest := [at for at in wide if at not in over]):
        _narrowed(resolved, entries, diffs, rest)
        files, block, excluded, _left_out = shown_to_guidelines(entries, diffs, diff_cap, skip, shown=guidelines)
    return files, block, excluded


def _diffs(resolved: dict, entries: list[dict], scope: Sequence[str], wide: frozenset[int]) -> list[str]:
    """Each entry's whole diff, in order, those `wide` numbers within the function around each hunk: under `--all`
    one call for them all, every file added and so whole already, else one call a file, `GIT_WORKERS` at a time."""
    if not resolved["base"]:  # every file is added: one call reads them all
        return whole_diffs(resolved, entries, scope)
    with ThreadPoolExecutor(max_workers=GIT_WORKERS) as pool:  # git processes to wait on: threads suffice
        return list(pool.map(lambda at: _diff(resolved, entries[at], at in wide), range(len(entries))))


def _narrowed(resolved: dict, entries: list[dict], diffs: list[str], at: Sequence[int]) -> None:
    """The diffs `at` numbers read again as git prints them, in place, `GIT_WORKERS` at a time."""
    if at:
        with ThreadPoolExecutor(max_workers=GIT_WORKERS) as pool:
            for index, diff in zip(at, pool.map(lambda index: _diff(resolved, entries[index], False), at), strict=True):
                diffs[index] = diff


def _diff(resolved: dict, entry: dict, functions: bool) -> str:
    """`diff_of` for one listed entry."""
    return diff_of(resolved, entry["path"], entry.get("old", ""), functions=functions)

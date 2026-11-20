"""Git discovery, the person's own fetch, and branch states for the TUI.

The screen reads a repository as the reviewer does: each read runs under a review's own environment (`read_only`),
where a repository's config names no program for git to start, and takes no lock, and the change a review would
read is read by the review's own collector (`read_change`). Three commands write, each only
where the person acts: `git fetch --no-prune` as they pick a repository, which adds the branches its remote got since
and removes none, unless `fetch_on_select = false`; `git fetch --prune` at F5; and `git branch -f` at the question a
branch behind its remote raises, never on a branch a working tree holds. They run with the person's own environment,
since a fetch needs their remotes, credentials and proxy, and with no terminal to prompt on: a session of their own,
or on Windows no console."""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from adk_review.collect.git import (
    GitError,
    checked_out,
    default_base,
    list_changed,
    on_path,
    preflight,
    preflight_staged,
    read_only,
)
from adk_review.core.text import lines_of, short_ref

SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".mypy_cache", ".ruff_cache"})
_AHEAD = re.compile(r"\bahead (\d+)\b")
_BEHIND = re.compile(r"\bbehind (\d+)\b")

type SyncKind = Literal["up_to_date", "ahead", "behind", "diverged", "remote_only", "local_only"]


@dataclass(frozen=True)
class ProjectInfo:
    """A discovered local Git repository under a configured root directory."""

    name: str
    path: str
    root_path: str
    branch: str
    dirty: bool

    def label(self) -> str:
        """Display label for the repository selector."""
        state = "dirty" if self.dirty else "clean"
        return f"{self.name}  ({self.branch}, {state})"


@dataclass(frozen=True)
class BranchInfo:
    """A local or remote Git branch with its upstream tracking state."""

    name: str
    is_remote: bool
    remote_name: str
    local_name: str
    upstream: str
    ahead: int = 0
    behind: int = 0

    def label(self) -> str:
        """Display label for `--head` and `--base` branch selectors."""
        if self.is_remote:
            return f"[remote] {self.name}"
        status = _sync_badge(self.upstream, self.ahead, self.behind)
        return f"[local]  {self.name}  {status}"


@dataclass(frozen=True)
class FetchResult:
    """Outcome of a `git fetch` on a selected repository: F5's, with `--prune`, or a pick's, with `--no-prune`."""

    ok: bool
    message: str


@dataclass(frozen=True)
class SyncStatus:
    """Comparison between a selected branch and its remote tracking ref."""

    kind: SyncKind
    local_branch: str
    upstream_branch: str
    ahead: int = 0
    behind: int = 0


@dataclass(frozen=True)
class SyncOutcome:
    """Result of moving a local branch to its remote's commit, or of leaving it and naming the ref to review."""

    ok: bool
    resolved_ref: str
    message: str


def _sync_badge(upstream: str, ahead: int, behind: int) -> str:
    if not upstream:
        return "(local only)"
    if ahead == 0 and behind == 0:
        return "(up to date)"
    if ahead > 0 and behind == 0:
        return f"(ahead {ahead})"
    if ahead == 0 and behind > 0:
        return f"(behind {behind})"
    return f"(ahead {ahead}, behind {behind})"


def _git_env() -> dict[str, str]:
    """The person's own git environment, for a command they asked for, with every prompt off so the screen never
    hangs."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["LC_ALL"] = "C"
    return env


def _run_git(repo: str, args: list[str], timeout_s: int = 15) -> subprocess.CompletedProcess[str]:
    """One command the person asked for, a fetch or a branch moved, in `repo`, with their own environment. A read goes
    through `_read`, never through here."""
    if (git := on_path("git")) == "git":
        raise OSError("git is not on PATH")
    return bounded([git, "-C", repo, *args], _git_env(), timeout_s)


#: Windows' flag for a process with no console: `subprocess` holds it there alone.
NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


@dataclass
class _Running:
    """A fetch of one repository in its thread, and its answer once it ends."""

    prune: bool
    ended: threading.Event = field(default_factory=threading.Event)
    answer: FetchResult | None = None


#: The fetch running for each repository, by its common git folder: a linked worktree shares its repository's refs.
_RUNNING: dict[str, _Running] = {}
_RUNNING_GUARD = threading.Lock()


def _fetch_key(repo: str) -> str:
    """`repo`'s common git folder, however its path is spelled, the same for each of its linked worktrees: read by
    review, keyed on the worktree's own path, two fetches into one set of refs raced for its locks again."""
    found = _read(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    folder = found[1].strip() if found is not None and found[1].strip() else repo
    return os.path.normcase(os.path.abspath(folder))


def bounded(
    argv: list[str], env: dict[str, str], timeout_s: int, *, stdin: int | None = None
) -> subprocess.CompletedProcess[str]:
    """`argv` run with `env`, decoded as UTF-8, and stopped whole past `timeout_s`: on Windows its process tree, as Git
    for Windows' `git.exe` runs the real git as its child, and elsewhere its process group, which git's own children
    join, ssh or its https helper. Reproduced by review: git killed alone left such a child holding the output open,
    and the screen waited on it, 6 s against a timeout of 1 s. The Azure CLI is run the same way: `az.cmd` starts a
    Python of its own. `stdin` is what the program reads: the screen's own input where none is named."""
    with subprocess.Popen(
        argv,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=sys.platform != "win32",
        # Read by review: on Windows git ran in the screen's own console, where ssh could ask for a host key or a
        # passphrase over the screen, racing it for the keys. With no console it finds none to prompt on.
        creationflags=NO_CONSOLE if sys.platform == "win32" else 0,
    ) as process:
        try:
            out, err = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
                subprocess.run(
                    [taskkill, "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.kill()
            process.communicate()
            raise
    return subprocess.CompletedProcess(argv, process.returncode, out, err)


def _read(repo: str, *args: str, ok: tuple[int, ...] = (0,)) -> tuple[int, str] | None:
    """What a read-only git command answers in `repo`, its exit code first, or None where git could not answer: the
    screen lists what it can read and says nothing of the rest."""
    try:
        return read_only(repo, *args, ok=ok)
    except GitError:
        return None


def file_lines(repo: str, commit: str, path: str) -> list[str] | None:
    """`path`'s lines at `commit` as git holds them, line 1 first, or None where git holds no such file: counted by
    the reviewer's own `lines_of`, which numbered the finding's lines, so the two never count apart. A comment's
    place ends at its last line's end, which only the line's own text gives."""
    try:
        _code, text = read_only(repo, "show", "--end-of-options", f"{commit}:{path}", strip=False)
    except GitError:
        return None
    return lines_of(text)


def _status(repo: str, *args: str) -> str | None:
    """`git status` as a reader runs it: with no optional lock, so git never writes the index it refreshed. Measured
    by review: a plain status rewrote the index of each repository the scan passed."""
    done = _read(repo, "--no-optional-locks", "status", "--untracked-files=no", *args)
    return None if done is None else done[1]


def _inspect_repo_status(repo: str) -> tuple[bool, str, bool]:
    """Inspect repository validity, checked-out branch, and tracked dirtiness in one `git status` pass."""
    said = _status(repo, "--porcelain=v2", "--branch")
    if said is None:
        return False, "", False
    branch = "DETACHED"
    dirty = False
    for line in said.splitlines():
        if line.startswith("# branch.head "):
            head_name = line.removeprefix("# branch.head ").strip()
            if head_name and head_name != "(detached)":
                branch = head_name
        elif line and not line.startswith("#"):
            dirty = True
    return True, branch, dirty


def discover_projects(root_dir: str, max_depth: int = 2) -> list[ProjectInfo]:
    """Find all Git repositories under `root_dir` up to `max_depth` directory levels."""
    base = Path(root_dir).expanduser().resolve()
    if not base.is_dir():
        return []
    found_paths: list[Path] = []
    _walk_for_repos(base, 0, max_depth, found_paths)
    projects: list[ProjectInfo] = []
    for repo_path in sorted(found_paths, key=lambda p: p.name.lower()):
        path_str = str(repo_path)
        ok, branch, dirty = _inspect_repo_status(path_str)
        if not ok:
            continue
        rel = repo_path.relative_to(base).as_posix() if repo_path != base else repo_path.name
        projects.append(
            ProjectInfo(
                name=rel,
                path=path_str,
                root_path=str(base),
                branch=branch,
                dirty=dirty,
            )
        )
    return projects


def _walk_for_repos(current: Path, depth: int, max_depth: int, acc: list[Path]) -> None:
    """Recursively collect directories containing `.git` up to `max_depth`."""
    try:
        if (current / ".git").exists():
            acc.append(current)
            return
        if depth >= max_depth:
            return
        children = sorted(current.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return
    for child in children:
        if child.is_dir() and not child.name.startswith(".") and child.name not in SKIP_DIRS:
            _walk_for_repos(child, depth + 1, max_depth, acc)


def is_dirty(repo: str) -> bool:
    """Whether `repo` has uncommitted tracked modifications in its working tree or index."""
    return bool(_status(repo, "--porcelain"))


def fetch_remote(repo: str, timeout_s: int = 15, *, prune: bool = False) -> FetchResult:
    """Run `git fetch` on `repo` where it has a remote: with `--prune` only where asked, F5's, the person's own; else
    `--no-prune`, as the person picks the repository, which adds the branches its remote got since and removes none.
    One fetch of a repository runs at a time, and one asked for meanwhile takes its answer, F5's prune excepted, which
    runs after a pick's fetch. Read by review: a cancelled pick's fetch runs on in its thread, and each later fetch of
    the repository waited for it and ran its own, a timeout each, which quitting then waited for."""
    try:
        _code, remotes = read_only(repo, "remote")
    except GitError as exc:
        return FetchResult(ok=False, message=f"git fetch failed: {exc}")
    if not remotes:
        return FetchResult(ok=True, message="No remote configured (local repository)")
    key = _fetch_key(repo)
    while True:
        with _RUNNING_GUARD:
            running = _RUNNING.get(key)
            if running is None:
                mine = _RUNNING[key] = _Running(prune)
                break
        running.ended.wait()  # bounded: the running fetch ends at its timeout
        if running.answer is not None and (running.prune or not prune):
            return running.answer
    try:
        mine.answer = _fetched(repo, timeout_s, prune=prune)
        return mine.answer
    finally:
        with _RUNNING_GUARD:
            del _RUNNING[key]
        mine.ended.set()


def _fetched(repo: str, timeout_s: int, *, prune: bool) -> FetchResult:
    """One `git fetch` of `repo`, and what it says. `--no-prune` is said, never left out: read by review, and
    reproduced, `fetch.prune` in the person's config, as the guide advises for the push hook, made a pick's fetch prune
    a branch they had chosen. No maintenance starts: read by review, a fetch can start git's own, which a timeout
    then stops halfway, leaving its files behind."""
    args = ["fetch", "--prune" if prune else "--no-prune", "--no-auto-maintenance", "--quiet"]
    try:
        done = _run_git(repo, args, timeout_s=timeout_s)
    except subprocess.TimeoutExpired:
        return FetchResult(ok=False, message=f"git fetch timed out after {timeout_s}s (using cached refs)")
    except OSError as exc:
        return FetchResult(ok=False, message=f"git fetch failed: {exc}")
    if done.returncode != 0:
        first_line = (done.stderr.strip().splitlines() or ["git fetch failed"])[0]
        return FetchResult(ok=False, message=f"Fetch warning: {first_line[:80]}")
    return FetchResult(ok=True, message="Fetched latest refs from remote")


def _checked_out(repo: str) -> str | None:
    """The branch `repo` has checked out, or None where it has none or git could not say."""
    try:
        return checked_out(repo)
    except GitError:
        return None


def list_branches(repo: str) -> list[BranchInfo]:
    """List all local and remote branches in `repo` with ahead/behind counts. Each of a branch's fields ends at a NUL,
    `%00`, which no name can hold. Read by an audit, and reproduced: with `|` between them, `feat/a|b`, a name git
    allows, split in six and was left out of both lists, with no word."""
    done = _read(
        repo,
        "for-each-ref",
        "--format=%(refname)%00%(refname:short)%00%(upstream:short)%00%(upstream:track,nobracket)",
        "refs/heads",
        "refs/remotes",
    )
    if done is None:
        return []
    locals_found, remotes_found = _parse_ref_lines(repo, done[1].split("\n"))  # git's own line end, and no other
    current = _checked_out(repo)
    locals_sorted = sorted(locals_found, key=lambda b: (0 if b.name == current else 1, b.name.lower()))
    remotes_sorted = sorted(remotes_found, key=lambda b: b.name.lower())
    return [*locals_sorted, *remotes_sorted]


def _parse_track(track: str) -> tuple[int, int]:
    """Extract `(ahead, behind)` counts from git `%(upstream:track,nobracket)` output."""
    ahead_match = _AHEAD.search(track)
    behind_match = _BEHIND.search(track)
    return (
        int(ahead_match.group(1)) if ahead_match else 0,
        int(behind_match.group(1)) if behind_match else 0,
    )


def _local_branch_info(
    repo: str, short: str, own: str, upstream: str, track: str, remote_shorts: set[str]
) -> BranchInfo:
    """Build a `BranchInfo` for a local branch, reusing `track` when upstream is configured: `short` the name the
    screen shows and a review reads, git's own, which is `heads/x` where a tag is named `x` too, and `own` the
    branch's own name, which git's commands read it by."""
    if upstream:
        ahead, behind = _parse_track(track)
        return BranchInfo(
            name=short,
            is_remote=False,
            remote_name=upstream,
            local_name=own,
            upstream=upstream,
            ahead=ahead,
            behind=behind,
        )
    inferred = f"origin/{own}" if f"origin/{own}" in remote_shorts else ""
    ahead, behind = branch_divergence(repo, own, inferred) if inferred else (0, 0)
    return BranchInfo(
        name=short,
        is_remote=False,
        remote_name=inferred,
        local_name=own,
        upstream=inferred,
        ahead=ahead,
        behind=behind,
    )


def _parse_ref_lines(repo: str, lines: list[str]) -> tuple[list[BranchInfo], list[BranchInfo]]:
    """Split `git for-each-ref` lines into local and remote `BranchInfo` lists."""
    local_map: dict[str, BranchInfo] = {}
    remote_list: list[BranchInfo] = []
    remote_shorts: set[str] = set()
    raw_rows: list[tuple[str, str, str, str]] = []
    for line in lines:
        parts = line.rstrip("\r\n").split("\0")
        if len(parts) == 4:
            raw_rows.append((parts[0], parts[1], parts[2], parts[3]))
            if parts[0].startswith("refs/remotes/") and not parts[0].endswith("/HEAD"):
                remote_shorts.add(parts[1])
    for full, short, upstream, track in raw_rows:
        if full.startswith("refs/heads/"):
            own = full.removeprefix("refs/heads/")
            local_map[short] = _local_branch_info(repo, short, own, upstream, track, remote_shorts)
        elif full.startswith("refs/remotes/") and not full.endswith("/HEAD") and "/" in short:
            _remote_prefix, _, local_name = full.removeprefix("refs/remotes/").partition("/")
            remote_list.append(
                BranchInfo(
                    name=short,
                    is_remote=True,
                    remote_name=short,
                    local_name=local_name,
                    upstream=short,
                )
            )
    return list(local_map.values()), remote_list


def branch_divergence(repo: str, local_branch: str, upstream_ref: str) -> tuple[int, int]:
    """Return `(ahead, behind)` commits between the local branch named `local_branch`, its own name, and
    `upstream_ref`. The branch is read by its full ref: read by an audit, and reproduced, a tag of the same name stood
    in for it, and a branch with a commit of its own was called behind."""
    span = f"refs/heads/{local_branch}...{upstream_ref}"
    done = _read(repo, "rev-list", "--left-right", "--count", "--end-of-options", span)
    parts = done[1].split() if done is not None else []
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def inspect_branch_sync(repo: str, branch: BranchInfo) -> SyncStatus:
    """Determine whether `branch` is a remote-only ref, up-to-date, ahead, behind, or diverged."""
    if branch.is_remote:
        held = _read(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch.local_name}", ok=(0, 1))
        if held is None or held[0] != 0:
            return SyncStatus("remote_only", branch.local_name, branch.name, 0, 0)
        ahead, behind = branch_divergence(repo, branch.local_name, branch.name)
        return _classify_counts(_shown(repo, branch.local_name), branch.name, ahead, behind)
    if not branch.upstream:
        return SyncStatus("local_only", branch.name, "", 0, 0)
    ahead, behind = branch_divergence(repo, branch.local_name, branch.upstream)
    return _classify_counts(branch.name, branch.upstream, ahead, behind)


def _shown(repo: str, own: str) -> str:
    """The name the list shows the local branch `own` by, and a review reads it by: git's own short name, which is
    `heads/x` where a tag is named `x` too and `x` would name the tag."""
    done = _read(repo, "rev-parse", "--abbrev-ref", f"refs/heads/{own}")
    return done[1] if done is not None and done[1] else own


def _classify_counts(local_branch: str, upstream_branch: str, ahead: int, behind: int) -> SyncStatus:
    if behind == 0 and ahead == 0:
        return SyncStatus("up_to_date", local_branch, upstream_branch, 0, 0)
    if behind == 0:
        return SyncStatus("ahead", local_branch, upstream_branch, ahead, 0)
    if ahead == 0:
        return SyncStatus("behind", local_branch, upstream_branch, 0, behind)
    return SyncStatus("diverged", local_branch, upstream_branch, ahead, behind)


def fast_forward_local_branch(repo: str, local_branch: str, upstream_ref: str) -> SyncOutcome:
    """Move `local_branch` to `upstream_ref` where that is a fast-forward: the person's own `git branch -f`, asked for
    at the question. A branch the repository has checked out is left as it is, its files too, and the remote's is
    reviewed by its own name. Read by review: there the answer ran `git merge --ff-only`, which rewrote the files of a
    working tree the question had said would stay untouched. `local_branch` is read as the branch it names, by its
    full ref: read by an audit, and reproduced, `heads/x`, git's name for a branch `x` beside a tag `x`, was moved as
    a new branch of that name."""
    named = _read(
        repo, "rev-parse", "--verify", "--quiet", "--symbolic-full-name", "--end-of-options", local_branch, ok=(0, 1)
    )
    full = named[1] if named is not None and named[0] == 0 else ""
    if not full.startswith("refs/heads/"):
        return SyncOutcome(ok=False, resolved_ref=local_branch, message=f"'{local_branch}' names no local branch")
    ancestor = _read(repo, "merge-base", "--is-ancestor", "--end-of-options", full, upstream_ref, ok=(0, 1))
    if ancestor is None or ancestor[0] != 0:
        return SyncOutcome(
            ok=False,
            resolved_ref=local_branch,
            message=f"'{local_branch}' cannot be fast-forwarded cleanly to '{upstream_ref}'",
        )
    held = _read(repo, "symbolic-ref", "--quiet", "HEAD", ok=(0, 1))
    if held is not None and held[1] == full:
        return SyncOutcome(
            ok=True,
            resolved_ref=upstream_ref,
            message=f"'{local_branch}' is checked out, so it stays as it is. The review reads '{upstream_ref}'.",
        )
    try:
        # `--no-track`: the branch moves, and nothing else. Read by review: it also made the branch track the remote's,
        # a line in the repository's config no one asked for, where the screen had inferred the upstream.
        args = ["branch", "-f", "--no-track", "--end-of-options", full.removeprefix("refs/heads/"), upstream_ref]
        done = _run_git(repo, args, timeout_s=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SyncOutcome(ok=False, resolved_ref=upstream_ref, message=f"Branch sync failed: {exc}")
    if done.returncode == 0:
        return SyncOutcome(
            ok=True,
            resolved_ref=local_branch,
            message=f"Fast-forwarded local '{local_branch}' to '{upstream_ref}' (zero checkout)",
        )
    return SyncOutcome(ok=False, resolved_ref=upstream_ref, message=done.stderr.strip() or "branch update failed")


def preferred_base_branch(repo: str, branches: list[BranchInfo], head_ref: str, preset_base: str = "") -> str:
    """Choose the best default `--base` branch for `repo`, excluding `head_ref`."""
    names = {b.name for b in branches}
    if preset_base and preset_base in names and preset_base != head_ref:
        return preset_base
    try:
        detected = default_base(repo)
    except GitError:
        detected = None
    # By the name the list shows: `origin/HEAD`'s branch is named in full, where no local `main` is held.
    detected = short_ref(detected) if detected else None
    if detected and detected in names and detected != head_ref:
        return detected
    for candidate in ("develop", "main", "master", "origin/develop", "origin/main", "origin/master"):
        if candidate in names and candidate != head_ref:
            return candidate
    return next((b.name for b in branches if b.name != head_ref), "")


@dataclass(frozen=True)
class Change:
    """What a review of a pick would read, for the banner and the folder lists: each folder with its badge, each
    changed file's status letter, git's `A`, `M`, `D` or `R`, the commits since the merge base for a branch, and the
    collector's own sentence where the review would stop before it reads."""

    folders: list[tuple[str, str]]
    statuses: list[str] = field(default_factory=list)
    commits: int | None = None
    refused: str = ""


def read_change(repo: str, base: str | None, head: str, mode: str = "branch") -> Change:
    """The change a review of `mode`, `base` and `head` reads, through the review's own `preflight` and `list_changed`,
    so the banner and the review never count apart. Read by review, and reproduced: the screen's own git calls quoted
    a folder past ASCII, read branches with no shared history as no change, and counted the commits past the base
    where merges crossed. Measured: 0.1 s a pick on a Mac, where those calls took 0.03 s, and in a thread."""
    base = (base or None) if mode == "branch" else None  # no target: the whole project, as `--all` reads it
    resolved = preflight_staged(repo) if mode == "staged" else preflight(repo, base, head)
    refused: str = resolved.get("error_message", "")
    changed: list[dict[str, str]] = []
    if not refused:
        try:
            changed = list_changed(resolved)
        except GitError as exc:  # the change half read is not the change, as the review says too
            refused = str(exc)
    return Change(
        folders=_folders(repo, [entry["path"] for entry in changed]),
        statuses=[entry["status"] for entry in changed],
        commits=resolved["commits_ahead"] if base and not refused else None,
        refused=refused,
    )


def _folders(repo: str, paths: list[str]) -> list[tuple[str, str]]:
    """`[(folder_path, badge), ...]`: each folder and subfolder the changed `paths` lie in, with how many, then every
    other top folder of `repo`, for `--path` and `--path-exclude`."""
    folder_counts: dict[str, int] = {}
    for file_path in paths:
        parts = [p for p in file_path.split("/") if p]
        if len(parts) > 1:
            top = parts[0]
            folder_counts[top] = folder_counts.get(top, 0) + 1
            if len(parts) > 2:
                sub = f"{parts[0]}/{parts[1]}"
                folder_counts[sub] = folder_counts.get(sub, 0) + 1
        elif parts:
            folder_counts[parts[0]] = folder_counts.get(parts[0], 0) + 1
    results: list[tuple[str, str]] = [
        (folder, f"{folder}  ({count} changed)") for folder, count in sorted(folder_counts.items())
    ]
    seen = set(folder_counts)
    results.extend((top_dir, f"{top_dir}  (repo folder)") for top_dir in _repo_top_folders(repo) if top_dir not in seen)
    return results


def _repo_top_folders(repo: str) -> list[str]:
    """List top-level folders in `repo` for `--path` and `--path-exclude` selection."""
    root = Path(repo)
    if not root.is_dir():
        return []
    try:
        return sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and child.name not in SKIP_DIRS and child.name != ".git"
        )
    except OSError:
        return []

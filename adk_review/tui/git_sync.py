"""Git discovery, non-interactive fetch, and zero-checkout branch synchronization for the TUI."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..collect.git import checked_out, default_base, on_path

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
            return f"[remote] {self.name}  (->{self.local_name})"
        status = _sync_badge(self.upstream, self.ahead, self.behind)
        return f"[local]  {self.name}  {status}"


@dataclass(frozen=True)
class FetchResult:
    """Outcome of running `git fetch --prune` on a selected repository."""

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
    """Result of materializing or fast-forwarding a branch without `git checkout`."""

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
    """User git environment with interactive terminal prompts disabled so the TUI never hangs."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["LC_ALL"] = "C"
    return env


def _run_git(repo: str, args: list[str], timeout_s: int = 15) -> subprocess.CompletedProcess[str]:
    """Run git in `repo` with UTF-8 decoding, non-interactive env, and Windows process-tree cleanup on timeout."""
    if (git := on_path("git")) == "git":
        raise OSError("git is not on PATH")
    argv = [git, "-C", repo, *args]
    with subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_git_env(),
    ) as process:
        try:
            out, err = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
                subprocess.run(
                    [taskkill, "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
            process.kill()
            process.communicate()
            raise
    return subprocess.CompletedProcess(argv, process.returncode, out, err)


def _inspect_repo_status(repo: str) -> tuple[bool, str, bool]:
    """Inspect repository validity, checked-out branch, and tracked dirtiness in one `git status` pass."""
    try:
        done = _run_git(repo, ["status", "--porcelain=v2", "--branch", "--untracked-files=no"], timeout_s=5)
    except OSError, subprocess.TimeoutExpired:
        return False, "", False
    if done.returncode != 0:
        return False, "", False
    branch = "DETACHED"
    dirty = False
    for line in done.stdout.splitlines():
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
    try:
        done = _run_git(repo, ["status", "--porcelain", "--untracked-files=no"], timeout_s=5)
    except OSError, subprocess.TimeoutExpired:
        return False
    return done.returncode == 0 and bool(done.stdout.strip())


def fetch_remote(repo: str, timeout_s: int = 15) -> FetchResult:
    """Run `git fetch --prune --quiet` on `repo` if it has a configured remote."""
    try:
        remotes = _run_git(repo, ["remote"], timeout_s=5)
        if remotes.returncode != 0 or not remotes.stdout.strip():
            return FetchResult(ok=True, message="No remote configured (local repository)")
        done = _run_git(repo, ["fetch", "--prune", "--quiet"], timeout_s=timeout_s)
    except subprocess.TimeoutExpired:
        return FetchResult(ok=False, message=f"git fetch timed out after {timeout_s}s (using cached refs)")
    except OSError as exc:
        return FetchResult(ok=False, message=f"git fetch failed: {exc}")
    if done.returncode != 0:
        first_line = (done.stderr.strip().splitlines() or ["git fetch failed"])[0]
        return FetchResult(ok=False, message=f"Fetch warning: {first_line[:80]}")
    return FetchResult(ok=True, message="Fetched latest refs from remote")


def list_branches(repo: str) -> list[BranchInfo]:
    """List all local and remote branches in `repo` with ahead/behind counts."""
    try:
        done = _run_git(
            repo,
            [
                "for-each-ref",
                "--format=%(refname)|%(refname:short)|%(upstream:short)|%(upstream:track,nobracket)",
                "refs/heads",
                "refs/remotes",
            ],
            timeout_s=10,
        )
    except OSError, subprocess.TimeoutExpired:
        return []
    if done.returncode != 0:
        return []
    locals_found, remotes_found = _parse_ref_lines(repo, done.stdout.splitlines())
    current = checked_out(repo)
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


def _local_branch_info(repo: str, short: str, upstream: str, track: str, remote_shorts: set[str]) -> BranchInfo:
    """Build a `BranchInfo` for a local branch, reusing `track` when upstream is configured."""
    if upstream:
        ahead, behind = _parse_track(track)
        return BranchInfo(
            name=short,
            is_remote=False,
            remote_name=upstream,
            local_name=short,
            upstream=upstream,
            ahead=ahead,
            behind=behind,
        )
    inferred = f"origin/{short}" if f"origin/{short}" in remote_shorts else ""
    ahead, behind = branch_divergence(repo, short, inferred) if inferred else (0, 0)
    return BranchInfo(
        name=short,
        is_remote=False,
        remote_name=inferred,
        local_name=short,
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
        parts = line.rstrip("\r\n").split("|")
        if len(parts) == 4:
            raw_rows.append((parts[0], parts[1], parts[2], parts[3]))
            if parts[0].startswith("refs/remotes/") and not parts[0].endswith("/HEAD"):
                remote_shorts.add(parts[1])
    for full, short, upstream, track in raw_rows:
        if full.startswith("refs/heads/"):
            local_map[short] = _local_branch_info(repo, short, upstream, track, remote_shorts)
        elif full.startswith("refs/remotes/") and not full.endswith("/HEAD") and "/" in short:
            _remote_prefix, local_name = short.split("/", 1)
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


def branch_divergence(repo: str, local_ref: str, upstream_ref: str) -> tuple[int, int]:
    """Return `(ahead, behind)` commits between `local_ref` and `upstream_ref`."""
    try:
        done = _run_git(repo, ["rev-list", "--left-right", "--count", f"{local_ref}...{upstream_ref}"], timeout_s=5)
    except OSError, subprocess.TimeoutExpired:
        return 0, 0
    if done.returncode != 0:
        return 0, 0
    parts = done.stdout.strip().split()
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def inspect_branch_sync(repo: str, branch: BranchInfo) -> SyncStatus:
    """Determine whether `branch` is a remote-only ref, up-to-date, ahead, behind, or diverged."""
    if branch.is_remote:
        try:
            exists = _run_git(repo, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch.local_name}"], timeout_s=5)
        except OSError, subprocess.TimeoutExpired:
            return SyncStatus("remote_only", branch.local_name, branch.name, 0, 0)
        if exists.returncode != 0:
            return SyncStatus("remote_only", branch.local_name, branch.name, 0, 0)
        ahead, behind = branch_divergence(repo, branch.local_name, branch.name)
        return _classify_counts(branch.local_name, branch.name, ahead, behind)
    if not branch.upstream:
        return SyncStatus("local_only", branch.name, "", 0, 0)
    ahead, behind = branch_divergence(repo, branch.name, branch.upstream)
    return _classify_counts(branch.name, branch.upstream, ahead, behind)


def _classify_counts(local_branch: str, upstream_branch: str, ahead: int, behind: int) -> SyncStatus:
    if behind == 0 and ahead == 0:
        return SyncStatus("up_to_date", local_branch, upstream_branch, 0, 0)
    if behind == 0:
        return SyncStatus("ahead", local_branch, upstream_branch, ahead, 0)
    if ahead == 0:
        return SyncStatus("behind", local_branch, upstream_branch, 0, behind)
    return SyncStatus("diverged", local_branch, upstream_branch, ahead, behind)


def materialize_remote_branch(repo: str, local_branch: str, remote_ref: str) -> SyncOutcome:
    """Create local tracking branch `local_branch` from `remote_ref` without running `git checkout`."""
    try:
        done = _run_git(repo, ["branch", "--track", local_branch, remote_ref], timeout_s=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SyncOutcome(ok=False, resolved_ref=remote_ref, message=f"Could not create local branch: {exc}")
    if done.returncode == 0:
        return SyncOutcome(
            ok=True,
            resolved_ref=local_branch,
            message=f"Created local branch '{local_branch}' tracking '{remote_ref}' (without checkout)",
        )
    return fast_forward_local_branch(repo, local_branch, remote_ref)


def fast_forward_local_branch(repo: str, local_branch: str, upstream_ref: str) -> SyncOutcome:
    """Fast-forward `local_branch` to `upstream_ref` without switching branches or losing local changes."""
    try:
        return _fast_forward_local_branch(repo, local_branch, upstream_ref)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SyncOutcome(ok=False, resolved_ref=upstream_ref, message=f"Branch sync failed: {exc}")


def _fast_forward_local_branch(repo: str, local_branch: str, upstream_ref: str) -> SyncOutcome:
    anc = _run_git(repo, ["merge-base", "--is-ancestor", local_branch, upstream_ref], timeout_s=5)
    if anc.returncode != 0:
        return SyncOutcome(
            ok=False,
            resolved_ref=local_branch,
            message=f"'{local_branch}' cannot be fast-forwarded cleanly to '{upstream_ref}'",
        )
    current = checked_out(repo)
    if current != local_branch:
        done = _run_git(repo, ["branch", "-f", local_branch, upstream_ref], timeout_s=10)
        if done.returncode == 0:
            return SyncOutcome(
                ok=True,
                resolved_ref=local_branch,
                message=f"Fast-forwarded local '{local_branch}' to '{upstream_ref}' (zero checkout)",
            )
        return SyncOutcome(ok=False, resolved_ref=upstream_ref, message=done.stderr.strip() or "branch update failed")
    if is_dirty(repo):
        return SyncOutcome(
            ok=True,
            resolved_ref=upstream_ref,
            message=f"Kept uncommitted edits on '{local_branch}'; reviewing '{upstream_ref}' directly",
        )
    done = _run_git(repo, ["merge", "--ff-only", upstream_ref], timeout_s=10)
    if done.returncode == 0:
        return SyncOutcome(
            ok=True,
            resolved_ref=local_branch,
            message=f"Fast-forwarded checked-out branch '{local_branch}' to '{upstream_ref}'",
        )
    return SyncOutcome(ok=True, resolved_ref=upstream_ref, message=f"Reviewing '{upstream_ref}' directly")


def preferred_base_branch(repo: str, branches: list[BranchInfo], head_ref: str, preset_base: str = "") -> str:
    """Choose the best default `--base` branch for `repo`, excluding `head_ref`."""
    names = {b.name for b in branches}
    if preset_base and preset_base in names and preset_base != head_ref:
        return preset_base
    detected = default_base(repo)
    if detected and detected in names and detected != head_ref:
        return detected
    for candidate in ("develop", "main", "master", "origin/develop", "origin/main", "origin/master"):
        if candidate in names and candidate != head_ref:
            return candidate
    return next((b.name for b in branches if b.name != head_ref), "")


def changed_folders(repo: str, base: str | None, head: str, mode: str = "branch") -> tuple[list[tuple[str, str]], int]:
    """Return `[(folder_path, badge), ...]` and total changed file count for `mode`, `base`, and `head`."""
    files = _changed_files(repo, base, head, mode)
    folder_counts: dict[str, int] = {}
    for file_path in files:
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
    return results, len(files)


def _changed_files(repo: str, base: str | None, head: str, mode: str = "branch") -> list[str]:
    """List changed file paths for `staged`, `all`, or `branch` mode."""
    if mode == "staged":
        args = ["diff", "--cached", "--name-only"]
    elif mode == "all" or not base:
        args = ["ls-tree", "-r", "--name-only", head]
    else:
        args = ["diff", "--name-only", f"{base}...{head}"]
    try:
        done = _run_git(repo, args, timeout_s=10)
    except OSError, subprocess.TimeoutExpired:
        return []
    if done.returncode != 0:
        return []
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


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

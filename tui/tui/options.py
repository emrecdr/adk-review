"""Profile discovery, option descriptions, and CLI argument builder for `adk-review-tui`."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal

from adk_review.core.findings import RANK
from adk_review.core.profiles import DEFAULT, expanded
from adk_review.core.verdict import EXIT_CLEAN, EXIT_FINDINGS
from adk_review.deliver.report import read_previous, windows_word
from adk_review.judge.catalog import CONFIG, catalog, load_config

from .config import BASELINES_DIR, CACHE_DIR, OUTPUTS_DIR, write_atomic

type ReviewMode = Literal["branch", "staged", "all"]

_SAFE_TOKEN = re.compile(r"[^a-z0-9._-]+")

OPTION_DESCRIPTIONS: dict[str, str] = {
    "mode": "Choose between reviewing a branch diff (--head/--base), staged index (--staged), or all files (--all).",
    "profile": "Named set of deterministic rules and LLM guidelines from config.toml.",
    "fail_on": "Exit 1 at or above this severity: blocker, major or minor. Lower findings stay in the report.",
    "comment": "Write an author-ready PR review markdown file (comment-<repo>__<base>__<head>__...md).",
    "handoff": "Write coding-agent fix instructions (handoff-<repo>__<base>__<head>__...md).",
    "verify": "Ask a second model to verify each finding and drop refuted non-security findings.",
    "use_cache": "Keep each decided review, so the same review run again makes no model call.",
    "use_previous": "Compare against the last review of this branch (--previous) to badge NEW and RESOLVED findings.",
    "path": "Review ONLY the selected folders or files (--path). Leave empty to review the whole change.",
    "path_exclude": "Leave the selected folders or files out of the review completely (--path-exclude).",
    "checks": "Run only specific checks/groups (--check rule.x,guideline.y), overriding --profile.",
    "diff_cap": "Override per-file diff char limit for guidelines (--diff-cap). Leave blank for default.",
    "dry_run": "Resolve revisions, changed files, profile, and model readiness without running checks (--dry-run).",
}


@dataclass(frozen=True)
class ProfileOption:
    """One selectable profile from `adk_review/config.toml` with its rule and guideline counts."""

    name: str
    rule_count: int
    guideline_count: int
    check_ids: tuple[str, ...]

    def label(self) -> str:
        """Short dropdown label for the Textual `Select` widget."""
        if self.guideline_count == 0:
            return f"{self.name}  ({self.rule_count} rules, 0 guidelines — offline)"
        return f"{self.name}  ({self.rule_count} rules, {self.guideline_count} guidelines)"

    def detail(self) -> str:
        """One-line description of the checks activated by this profile."""
        guidelines = [c.removeprefix("guideline.") for c in self.check_ids if c.startswith("guideline.")]
        if not guidelines:
            return f"Runs {self.rule_count} deterministic rules offline with no LLM call."
        shown = ", ".join(guidelines[:5]) + (f" +{len(guidelines) - 5} more" if len(guidelines) > 5 else "")
        return f"Runs {self.rule_count} rules + {self.guideline_count} guidelines ({shown})."


@dataclass(frozen=True)
class ReviewSelection:
    """User selections collected from the TUI Chain of Selects."""

    repo: str
    mode: ReviewMode = "branch"
    head: str = ""
    base: str = ""
    profile: str = DEFAULT
    fail_on: str = "blocker"
    comment: bool = True
    handoff: bool = False
    verify: bool = False
    use_cache: bool = True
    use_previous: bool = True
    dry_run: bool = False
    paths: tuple[str, ...] = ()
    path_excludes: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    diff_cap: int = 0
    output_dir: str = str(OUTPUTS_DIR)


def load_profile_options() -> list[ProfileOption]:
    """Load all profiles from `adk_review/config.toml` with expanded rule and guideline counts."""
    config = load_config(CONFIG)
    found = catalog()
    all_profiles = expanded(config["profiles"], found.ids(), found.groups())
    options: list[ProfileOption] = []
    ordered_names = [DEFAULT, *(sorted(name for name in all_profiles if name != DEFAULT))]
    for name in ordered_names:
        ids = tuple(all_profiles[name])
        rules = sum(1 for cid in ids if cid.startswith("rule."))
        guidelines = sum(1 for cid in ids if cid.startswith("guideline."))
        options.append(ProfileOption(name=name, rule_count=rules, guideline_count=guidelines, check_ids=ids))
    return options


def fail_on_choices() -> list[tuple[str, str]]:
    """Return `(label, severity)` pairs for the `--fail-on` selector."""
    descriptions = {
        "blocker": "blocker  (exit 1 on a blocker only, major and minor still reported)",
        "major": "major    (exit 1 on major or blocker findings)",
        "minor": "minor    (exit 1 on any finding: minor, major, or blocker)",
    }
    return [(descriptions.get(sev, sev), sev) for sev in RANK]


def parse_csv_paths(raw: str) -> list[str]:
    """Split typed paths at each comma or line into normalized relative POSIX paths, each keeping its spaces. Read by
    review: split at every space too, `My Docs` left out `My` and `Docs`, two folders no one named."""
    items: list[str] = []
    for piece in (part.strip() for part in re.split(r"[,\r\n]+", raw)):
        if not piece or PureWindowsPath(piece).drive:
            continue
        cleaned = re.sub(r"/+", "/", piece.replace("\\", "/")).strip("/")
        if cleaned and cleaned != "." and ".." not in cleaned.split("/"):
            items.append(cleaned)
    return list(dict.fromkeys(items))


#: What the screen shows where no quoting keeps the command one command in the shell a person pastes it into.
NOT_PASTED = (
    "This command cannot be pasted into a shell as one command: an argument holds a character the shell reads. "
    "Ctrl+R runs it here as it stands."
)


def format_cli_command(argv: Sequence[str], *, windows: bool = os.name == "nt") -> str:
    """`adk-review` and `argv` as a person pastes them: on Windows quoted as cmd and PowerShell both read them, by the
    reviewer's own `windows_word`, elsewhere as a POSIX shell reads them; `NOT_PASTED` where no quoting keeps an
    argument whole, or a line break or a character no one sees would split it. Read by review: `list2cmdline`, used
    before, quotes for the C runtime alone and left `&`, `;`, `$` and `%` bare, which cmd or PowerShell then read."""
    full = ["adk-review", *argv]
    words = [windows_word(arg) if windows else shlex.quote(arg) for arg in full]
    shown = [word for word in words if word is not None]
    if len(shown) < len(words) or not all(arg.isprintable() for arg in full):
        return NOT_PASTED
    return " ".join(shown)


def parse_csv_checks(raw: str) -> list[str]:
    """Split a comma- or whitespace-separated `--check` input into unique check or group IDs."""
    items = [piece.strip() for piece in re.split(r"[,\s]+", raw.strip()) if piece.strip()]
    return list(dict.fromkeys(items))


def parse_diff_cap(raw: str) -> int:
    """Parse an optional positive integer `--diff-cap` limit (`0` when blank or non-numeric)."""
    cleaned = raw.strip().replace(",", "").replace("_", "")
    if not cleaned:
        return 0
    try:
        value = int(cleaned)
    except ValueError:
        return 0
    return max(0, value)


def _safe_slug(value: str) -> str:
    slug = _SAFE_TOKEN.sub("-", value.lower()).strip("-")
    return (slug[:40] or "ref").strip("-")


def baseline_path(repo: str, base: str, head: str, mode: ReviewMode = "branch") -> Path:
    """Where the screen keeps the last report of one target, the repository, the mode and the branches it reads, for
    `--previous`: a readable slug, and a hash of the whole target. Reproduced by review: named by the slug alone, cut
    at 40 characters with `/` read as `-`, the head left out under `--all` and the repository by its folder alone,
    two branches shared one, and each review marked the other's findings new or resolved."""
    repo_slug = _safe_slug(Path(repo).name or "repo")
    target_slug = _safe_slug(mode if mode != "branch" else f"{base}__{head}")
    where = os.path.normcase(os.path.abspath(repo))
    target = (where, mode, base if mode == "branch" else "", "" if mode == "staged" else head)
    digest = hashlib.sha256("\0".join(target).encode("utf-8")).hexdigest()[:12]
    return BASELINES_DIR / f"baseline-{repo_slug}__{target_slug}__{digest}.json"


def review_command(argv: Sequence[str]) -> list[str]:
    """The review the screen runs: this Python, with `-P`, so the folder the screen started in is never on the import
    path. Reproduced by review: under `python -m` a `hashlib.py` in that folder ran in place of Python's own."""
    return [sys.executable, "-P", "-m", "adk_review.review", *argv]


def build_review_argv(sel: ReviewSelection) -> tuple[list[str], dict[str, Path]]:
    """Build the validated `adk-review` CLI argument list and output file map for `sel`. Nothing is written: the line
    that shows the command builds it at every key typed, and a review makes its folder as it starts
    (`make_output_dir`)."""
    if sel.mode == "branch" and sel.head and sel.base == sel.head:
        raise ValueError("branch under review and target branch cannot be the same")
    argv: list[str] = [sel.repo]
    argv.extend(_target_flags(sel))
    excluded = tuple(dict.fromkeys(p for p in sel.path_excludes if p))
    included = tuple(dict.fromkeys(p for p in sel.paths if p and p not in excluded))
    for folder in included:
        argv.extend(["--path", folder])
    for folder in excluded:
        argv.extend(["--path-exclude", folder])
    checks = tuple(dict.fromkeys(c.strip() for c in sel.checks if c.strip()))
    if checks:
        for cid in checks:
            argv.extend(["--check", cid])
    elif sel.profile and sel.profile != DEFAULT:
        argv.extend(["--profile", sel.profile])
    if sel.diff_cap > 0:
        argv.extend(["--diff-cap", str(sel.diff_cap)])
    if sel.fail_on in RANK:
        argv.extend(["--fail-on", sel.fail_on])
    argv.append("--verify" if sel.verify else "--no-verify")
    if sel.dry_run:
        argv.append("--dry-run")
        return argv, {}
    # A review tells its progress only at a terminal or when asked, and the screen reads it through a pipe. Read by
    # review: the progress tab stayed empty however long a review ran.
    argv.append("--progress")
    outputs = _output_flags(sel, argv)
    return argv, outputs


def _target_flags(sel: ReviewSelection) -> list[str]:
    """Build mutually exclusive target flags (`--staged`, `--all`, or `--head`/`--base`)."""
    if sel.mode == "staged":
        return ["--staged"]
    flags: list[str] = []
    if sel.head:
        flags.extend(["--head", sel.head])
    if sel.mode == "all":
        flags.append("--all")
    elif sel.base:
        flags.extend(["--base", sel.base])
    return flags


def _output_flags(sel: ReviewSelection, argv: list[str]) -> dict[str, Path]:
    """Append `--cache`, `--previous`, `--json`, `--comment`, and `--handoff` flags for a full review run."""
    outputs: dict[str, Path] = {}
    out_dir = _output_dir(sel)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    repo_slug = _safe_slug(Path(sel.repo).name or "repo")
    base_slug = _safe_slug(sel.base if sel.mode == "branch" else sel.mode)
    head_slug = _safe_slug("staged" if sel.mode == "staged" else (sel.head or sel.mode))
    if sel.use_cache:  # the reviewer makes the folder, for the operator alone: its answers quote the code
        argv.extend(["--cache", str(CACHE_DIR)])
    base_json = baseline_path(sel.repo, sel.base, sel.head, sel.mode)
    if sel.use_previous and base_json.is_file():
        argv.extend(["--previous", str(base_json)])
    json_file = out_dir / f"report-{repo_slug}__{base_slug}__{head_slug}__{stamp}.json"
    argv.extend(["--json", str(json_file)])
    outputs["json"] = json_file
    outputs["baseline"] = base_json
    if sel.comment:
        comment_file = out_dir / f"comment-{repo_slug}__{base_slug}__{head_slug}__{stamp}.md"
        argv.extend(["--comment", str(comment_file)])
        outputs["comment"] = comment_file
    if sel.handoff:
        handoff_file = out_dir / f"handoff-{repo_slug}__{base_slug}__{head_slug}__{stamp}.md"
        argv.extend(["--handoff", str(handoff_file)])
        outputs["handoff"] = handoff_file
    return outputs


def _output_dir(sel: ReviewSelection) -> Path:
    """Where the files of the review `sel` chooses go."""
    return Path(sel.output_dir).expanduser().resolve()


def make_output_dir(sel: ReviewSelection, config: Path) -> None:
    """The folder a review's files go to, made as the review starts: the reviewer refuses a file whose folder is not
    there. Read by an audit, and reproduced: made where the command was built, it was made again at every key typed,
    and one that could not be made, a drive not there or a file in the way, ended the screen in a traceback as it
    opened. An `OSError` that names the folder, the setting to change and `config`, the file that holds it."""
    out_dir = _output_dir(sel)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        why = exc.strerror or type(exc).__name__
        raise OSError(
            f"the output folder {out_dir} cannot be made: {why}. Set output_dir in {config} to a folder you can write"
        ) from exc


def _marks(path: Path) -> tuple | None:
    """What in a report marks a later review's findings, as `--previous` reads it, or None where it cannot be read as
    one: its findings' fingerprints, their words aside, its checks, holes, scope, unread files and unkept findings."""
    try:
        earlier = read_previous(str(path))[0]
    except ValueError:
        return None
    return earlier.marks() if earlier is not None else None


def promote_baseline(outputs: Mapping[str, Path], exit_code: int) -> None:
    """Make a decided review's JSON report its target's saved report, which the next review reads with `--previous`,
    where what marks a later review's findings differs from the saved one's: its findings' fingerprints, its checks,
    holes, scope, unread files and unkept findings. The cache key reads the previous report, and read by review, and
    measured, a report saved after every review changed that key each time, so the same review was served from the
    cache only from its fourth run, each a model call under a guideline's profile. Kept where those are the same, it
    marks them as it did, and the same review run a third time is served from the cache. Read by review too: kept
    while the fingerprints alone held, its other parts went stale."""
    if exit_code not in (EXIT_CLEAN, EXIT_FINDINGS):
        return
    json_path = outputs.get("json")
    baseline = outputs.get("baseline")
    if json_path is None or baseline is None or not json_path.is_file():
        return
    if baseline.is_file() and (held := _marks(baseline)) is not None and held == _marks(json_path):
        return
    write_atomic(baseline, json_path.read_text(encoding="utf-8"))

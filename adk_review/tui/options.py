"""Profile discovery, option descriptions, and CLI argument builder for `adk-review-tui`."""

from __future__ import annotations

import os
import re
import shlex
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal

from ..core.findings import RANK
from ..core.profiles import DEFAULT, expanded
from ..core.verdict import EXIT_CLEAN, EXIT_FINDINGS
from ..deliver.report import windows_word
from ..judge.catalog import CONFIG, catalog, load_config
from .config import BASELINES_DIR, CACHE_DIR, OUTPUTS_DIR

type ReviewMode = Literal["branch", "staged", "all"]

_SAFE_TOKEN = re.compile(r"[^a-z0-9._-]+")

OPTION_DESCRIPTIONS: dict[str, str] = {
    "mode": "Choose between reviewing a branch diff (--head/--base), staged index (--staged), or all files (--all).",
    "profile": "Named set of deterministic rules and LLM guidelines from config.toml.",
    "fail_on": "Exit 1 at or above this severity (blocker, major, minor); lower findings stay in the report.",
    "comment": "Write an author-ready PR review markdown file (comment-<repo>__<base>__<head>__...md).",
    "handoff": "Write coding-agent fix instructions (handoff-<repo>__<base>__<head>__...md).",
    "verify": "Ask a second model to verify each finding and drop refuted non-security findings.",
    "use_cache": "Cache decided reviews under adk_review/tui/.state/cache so unchanged re-runs finish instantly.",
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
        "blocker": "blocker  (exit 1 on blockers only; major & minor still reported)",
        "major": "major    (exit 1 on major or blocker findings)",
        "minor": "minor    (exit 1 on any finding: minor, major, or blocker)",
    }
    return [(descriptions.get(sev, sev), sev) for sev in RANK]


def parse_csv_paths(raw: str) -> list[str]:
    """Split a comma- or whitespace-separated path input into normalized relative POSIX paths."""
    items: list[str] = []
    for piece in re.split(r"[,\s]+", raw.strip()):
        if not piece or PureWindowsPath(piece).drive:
            continue
        cleaned = re.sub(r"/+", "/", piece.strip().replace("\\", "/")).strip("/")
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
    """Path where the TUI stores the latest JSON report for `(repo, base, head)` to power `--previous`."""
    repo_slug = _safe_slug(Path(repo).name or "repo")
    target_slug = _safe_slug(mode if mode != "branch" else f"{base}__{head}")
    return BASELINES_DIR / f"baseline-{repo_slug}__{target_slug}.json"


def build_review_argv(sel: ReviewSelection) -> tuple[list[str], dict[str, Path]]:
    """Build the validated `adk-review` CLI argument list and output file map for `sel`."""
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
    out_dir = Path(sel.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    repo_slug = _safe_slug(Path(sel.repo).name or "repo")
    base_slug = _safe_slug(sel.base if sel.mode == "branch" else sel.mode)
    head_slug = _safe_slug("staged" if sel.mode == "staged" else (sel.head or sel.mode))
    if sel.use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
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


def promote_baseline(outputs: Mapping[str, Path], exit_code: int) -> None:
    """Promote the completed JSON report to the branch's `--previous` baseline on clean or findings exits."""
    if exit_code not in (EXIT_CLEAN, EXIT_FINDINGS):
        return
    json_path = outputs.get("json")
    baseline = outputs.get("baseline")
    if json_path is None or baseline is None or not json_path.is_file():
        return
    baseline.parent.mkdir(parents=True, exist_ok=True)
    payload = json_path.read_text(encoding="utf-8")
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=baseline.parent,
        prefix=f".{baseline.name}.",
        delete=False,
    ) as handle:
        try:
            handle.write(payload)
            handle.close()
            os.replace(handle.name, baseline)
        except BaseException:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise

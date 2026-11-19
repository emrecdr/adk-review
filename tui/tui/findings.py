"""Structured JSON findings parser, filter helpers, and `--explain` Markdown renderer for `adk-review-tui`."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from adk_review.core.findings import RANK
from adk_review.judge.catalog import CONFIG, catalog, explained, load_config

#: A commit as git names it whole, SHA-1 or SHA-256: what a report's `head_sha` holds, and what a link may carry.
COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


@dataclass(frozen=True)
class FindingRow:
    """One normalized finding entry loaded from an `adk-review --json` report."""

    severity: str
    tier: str
    check: str
    file: str
    line: int | None
    title: str
    evidence: str
    suggestion: str
    already_held: bool = False
    #: The finding's reason, at any severity, and the corrected code where a model gave one: empty where none.
    why: str = ""
    code: str = ""
    #: The finding's identity across reviews, the lines the diff places it on, and whether the change added one of
    #: them: what a link, a comment's mark and its place in a pull request are built from. Empty or None where the
    #: report holds none.
    fingerprint: str = ""
    lines: tuple[int, int] | None = None
    on_added_line: bool | None = None

    def location(self) -> str:
        """Display location formatted as `file:line` or `file`."""
        return f"{self.file}:{self.line}" if self.line else self.file

    def span(self) -> tuple[int, int] | None:
        """The lines the finding stands on, first and last: the report's range, else its one line, else none."""
        if self.lines is not None:
            return self.lines
        return (self.line, self.line) if self.line is not None and self.line >= 1 else None


@dataclass(frozen=True)
class Reviewed:
    """What a report says it reviewed: the clone, the branch, and the commit its findings' lines are counted in."""

    repository: str
    head: str
    head_sha: str


def _row_from_item(item: dict, tier: str) -> FindingRow:
    """Convert one raw JSON finding dict into a `FindingRow`."""
    raw_line = item.get("line")
    line = raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) else None
    return FindingRow(
        severity=str(item.get("severity") or "minor"),
        tier=tier,
        check=str(item.get("check") or ""),
        file=str(item.get("file") or ""),
        line=line,
        title=str(item.get("title") or ""),
        evidence=str(item.get("evidence") or ""),
        suggestion=str(item.get("suggestion") or ""),
        already_held=bool(item.get("already_held", False)),
        why=str(item.get("why") or ""),
        code=str(item.get("code") or ""),
        fingerprint=fingerprint if isinstance(fingerprint := item.get("fingerprint"), str) else "",
        lines=_range(item.get("lines")),
        on_added_line=added if isinstance(added := item.get("on_added_line"), bool) else None,
    )


def _range(raw: object) -> tuple[int, int] | None:
    """A report's `lines` where it is a range of a file's lines: two whole numbers, from 1, in order. A `bool` is
    no line number, though Python counts it as a whole number."""
    if isinstance(raw, list) and len(raw) == 2 and all(type(n) is int for n in raw) and 1 <= raw[0] <= raw[1]:
        return raw[0], raw[1]
    return None


def _extend_rows(rows: list[FindingRow], items: object, tier_resolver: str | None = None) -> None:
    """Append dict entries from `items` into `rows` using fixed or `since_previous` tier resolution."""
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        tier = tier_resolver or ("NEW" if item.get("since_previous") == "new" else "Active")
        rows.append(_row_from_item(item, tier))


def _read(json_path: Path) -> dict | None:
    """A JSON report as data, or None where the file is missing, is no JSON, or holds no object."""
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def load_reviewed(json_path: Path) -> Reviewed | None:
    """What the report at `json_path` reviewed, or None where it does not say."""
    return load_report(json_path)[1]


def load_findings_from_json(json_path: Path) -> list[FindingRow]:
    """Load `findings`, `old_findings`, and `resolved` entries from a JSON report, sorted by priority."""
    return load_report(json_path)[0]


def _reviewed(raw: dict) -> Reviewed | None:
    """What a report says it reviewed, or None where it does not say: no line is linked to, and no comment placed,
    on a commit the report did not name whole."""
    seen = raw.get("reviewed")
    if not isinstance(seen, dict):
        return None
    repository, head, head_sha = seen.get("repository"), seen.get("head"), seen.get("head_sha")
    if (
        isinstance(repository, str)
        and isinstance(head, str)
        and isinstance(head_sha, str)
        and COMMIT.fullmatch(head_sha)
    ):
        return Reviewed(repository, head, head_sha)
    return None


def load_report(json_path: Path) -> tuple[list[FindingRow], Reviewed | None]:
    """A report's findings, sorted by priority, and what it reviewed, from one read of the file: the screen shows
    the two together, and a commit read apart from its findings was, for a moment, the last review's."""
    raw = _read(json_path)
    return ([], None) if raw is None else (_rows(raw), _reviewed(raw))


def _rows(raw: dict) -> list[FindingRow]:
    """A report's `findings`, `old_findings` and `resolved` entries, sorted by priority."""
    rows: list[FindingRow] = []
    _extend_rows(rows, raw.get("findings"))
    _extend_rows(rows, raw.get("old_findings"), "Old")
    _extend_rows(rows, raw.get("resolved"), "Resolved")
    return sorted(
        rows,
        key=lambda row: (
            row.tier == "Resolved",
            row.tier == "Old",
            -RANK.get(row.severity, 0),
            row.file,
            row.line or 0,
        ),
    )


def filter_findings(
    rows: Sequence[FindingRow],
    severity: str = "all",
    tier: str = "all",
    query: str = "",
) -> list[FindingRow]:
    """Filter finding rows by severity, tier (`Active`/`NEW`/`Old`/`Resolved`), and free-text query."""
    needle = query.strip().lower()
    kept: list[FindingRow] = []
    for row in rows:
        if severity != "all" and row.severity != severity:
            continue
        if tier != "all" and row.tier != tier:
            continue
        if needle:
            haystack = f"{row.severity} {row.tier} {row.check} {row.file} {row.title} {row.evidence}".lower()
            if needle not in haystack:
                continue
        kept.append(row)
    return kept


def format_finding_detail(row: FindingRow) -> str:
    """One finding as plain text, drawn and copied as it is: its reason and its corrected code too, where the model gave
    them. Reported by the owner: a major read as one short line, with nothing to say what was wrong. Its title, fix and
    quote are a model's words and the branch's: read by review, drawn as Markdown, a branch that steered the model
    could make a link the person might click, and the JSON keeps them plain for its reader to escape."""
    badge = f"{row.tier} (possibly already done)" if row.already_held else row.tier
    lines = [
        f"[{row.severity.upper()}] {row.title or '(no title)'}",
        "",
        f"Tier: {badge}    Check: {row.check}",
        f"Location: {row.location()}",
        "",
        "Evidence:",
        *(f"    {line}" for line in (row.evidence or "(none)").splitlines()),
    ]
    if row.why:
        lines.extend(["", f"Why it matters: {row.why}"])
    if row.suggestion:
        lines.extend(["", f"Suggested fix: {row.suggestion}"])
    if row.code:
        lines.extend(["", "Corrected code:", *(f"    {line}" for line in row.code.splitlines())])
    return "\n".join(lines)


def explain_check_markdown(check_id: str) -> str:
    """Look up `check_id` in the local `adk_review` catalog and format its explanation as Markdown."""
    target = check_id.strip()
    if not target:
        return "*No check ID selected.*"
    try:
        config = load_config(CONFIG)
        info = explained(catalog(), config["profiles"], target)
    except ValueError as exc:
        return f"### Unknown Check\n\n{exc}"
    lines = [
        f"## `{info['id']}` — {info['title']}",
        "",
        f"- **Kind:** {info['kind']}",
        f"- **Severity:** {info.get('severity', 'determined by guideline')}",
        f"- **Profiles:** `{info['profiles']}`",
        f"- **Definition:** `{info['file']}`",
    ]
    if info.get("tests"):
        lines.append(f"- **Tests:** {info['tests']}")
    if info.get("why"):
        lines.extend(["", "### Why", "", str(info["why"])])
    if info.get("fix"):
        lines.extend(["", "### Fix", "", str(info["fix"])])
    return "\n".join(lines)

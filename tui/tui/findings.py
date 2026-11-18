"""Structured JSON findings parser, filter helpers, and `--explain` Markdown renderer for `adk-review-tui`."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from adk_review.core.findings import RANK
from adk_review.judge.catalog import CONFIG, catalog, explained, load_config


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

    def location(self) -> str:
        """Display location formatted as `file:line` or `file`."""
        return f"{self.file}:{self.line}" if self.line else self.file


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
    )


def _extend_rows(rows: list[FindingRow], items: object, tier_resolver: str | None = None) -> None:
    """Append dict entries from `items` into `rows` using fixed or `since_previous` tier resolution."""
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        tier = tier_resolver or ("NEW" if item.get("since_previous") == "new" else "Active")
        rows.append(_row_from_item(item, tier))


def load_findings_from_json(json_path: Path) -> list[FindingRow]:
    """Load `findings`, `old_findings`, and `resolved` entries from a JSON report, sorted by priority."""
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        return []
    if not isinstance(raw, dict):
        return []
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
    """One finding as plain text, drawn and copied as it is. Its title, fix and quote are a model's words and the
    branch's: read by review, drawn as Markdown, a branch that steered the model could make a link the person might
    click, and the JSON keeps them plain for its reader to escape."""
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
    if row.suggestion:
        lines.extend(["", f"Suggested fix: {row.suggestion}"])
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

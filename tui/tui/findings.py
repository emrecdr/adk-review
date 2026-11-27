"""Structured JSON findings parser, filter helpers, and `--explain` Markdown renderer for `adk-review-tui`."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from adk_review.collect.git import OBJECT_ID
from adk_review.core.findings import RANK
from adk_review.core.verdict import VERDICTS
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
    #: The finding's reason, at any severity, and the corrected code where a model gave one: empty where none.
    why: str = ""
    code: str = ""
    #: The finding's identity across reviews, the lines the diff places it on, and whether its corrected code can
    #: stand as a suggestion on them, as the report says: what a link, a comment's mark, its place in a pull request
    #: and its draft are built from. Empty, None or False where the report holds none, as an older one holds none.
    fingerprint: str = ""
    lines: tuple[int, int] | None = None
    suggestable: bool = False

    def location(self) -> str:
        """Display location formatted as `file:line` or `file`."""
        return f"{self.file}:{self.line}" if self.line else self.file

    def span(self) -> tuple[int, int] | None:
        """The lines the finding stands on, first and last: the report's range, else its one line, else none."""
        if self.lines is not None:
            return self.lines
        return (self.line, self.line) if self.line is not None and self.line >= 1 else None


def _row_from_item(item: dict, tier: str) -> FindingRow:
    """Convert one raw JSON finding dict into a `FindingRow`."""
    raw_line = item.get("line")
    line = raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) else None
    return FindingRow(
        severity=_text(item.get("severity"), "minor"),
        tier=tier,
        check=_text(item.get("check")),
        file=_text(item.get("file")),
        line=line,
        title=_text(item.get("title")),
        evidence=_text(item.get("evidence")),
        suggestion=_text(item.get("suggestion")),
        already_held=bool(item.get("already_held", False)),
        why=_text(item.get("why")),
        code=_text(item.get("code")),
        fingerprint=_text(item.get("fingerprint")),
        lines=_range(item.get("lines")),
        suggestable=item.get("suggestable") is True,
    )


def _text(raw: object, default: str = "") -> str:
    """A report's field where it is text, else `default`. Read by a code review: `str()` of a value nested 100,000
    lists deep, which json reads, raised `RecursionError` and closed the screen; and a list showed as a title."""
    return raw if isinstance(raw, str) and raw else default


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
    """A JSON report as data, or None where the file is missing, is no JSON, or holds no object. Read by an audit, one
    nested past Python's depth raised `RecursionError`, and by a code review, one holding an integer of more than
    4,300 digits a `ValueError` no narrower `except` caught: the screen closed on each."""
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except OSError, ValueError, RecursionError:  # ValueError holds JSONDecodeError and UnicodeDecodeError
        return None
    return raw if isinstance(raw, dict) else None


def _commit(raw: dict) -> str | None:
    """The commit a report says it reviewed, whole, or None where it names none: a line is linked to, and a comment
    placed, in a commit the report named, as git names one."""
    seen = raw.get("reviewed")
    head = seen.get("head_sha") if isinstance(seen, dict) else None
    return head if isinstance(head, str) and OBJECT_ID.fullmatch(head) else None


def _verdict(raw: dict) -> str:
    """The verdict a report says the review reached, or "" where it names none the screen knows: a report read, apart
    from one no read could give, whose verdict is None. Read by the final code review: the screen took the two for
    one, and a report it could not read said the review kept no finding."""
    verdict = raw.get("verdict")
    return verdict if verdict in VERDICTS else ""


def load_report(json_path: Path) -> tuple[list[FindingRow], str | None, str | None]:
    """A report's findings, sorted by priority, the commit it reviewed and the verdict it reached, from one read of
    the file: the screen shows them together, and a commit read apart from its findings was, for a moment, the last
    review's. Read by an audit: the findings alone left a review that could not check everything reading as one that
    found nothing."""
    raw = _read(json_path)
    return ([], None, None) if raw is None else (_rows(raw), _commit(raw), _verdict(raw))


def _rows(raw: dict) -> list[FindingRow]:
    """A report's `findings`, `old_findings`, `unchanged` and `resolved` entries, sorted by priority: an earlier
    finding not found again whose lines still stand at the head is no resolved one, and comes before what was there
    already."""
    rows: list[FindingRow] = []
    _extend_rows(rows, raw.get("findings"))
    _extend_rows(rows, raw.get("old_findings"), "Old")
    _extend_rows(rows, raw.get("unchanged"), "Unchanged")
    _extend_rows(rows, raw.get("resolved"), "Resolved")
    return sorted(
        rows,
        key=lambda row: (
            row.tier == "Resolved",
            row.tier == "Old",
            row.tier == "Unchanged",
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


#: What the detail says of an earlier review's finding: its line numbers are that review's, so it opens nothing.
_EARLIER = {
    "Unchanged": "Not found again by this review, but its code is unchanged, perhaps at other line numbers. Its "
    "lines are of an earlier review, so it opens no line and takes no comment.",
    "Resolved": "Its code is gone since the earlier review. Its lines are of that review, so it opens no line.",
}


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
    if row.tier in ("Unchanged", "Resolved"):  # read by an outside audit: these read as active findings
        lines.extend(["", _EARLIER[row.tier]])
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

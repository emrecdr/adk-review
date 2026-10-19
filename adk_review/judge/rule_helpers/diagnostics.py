"""An outside tool's diagnostics, read as a rule that names ruff reads them: a code's tier in the rule's own tables,
the fix to name, and one file's findings, the change's own lines listed and the rest counted.

A rule that names ruff keeps its codes, its severities and its fix sentences; what it does with ruff's answer is the
same for each, and lives here once.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from ...core.findings import RANK, Severity
from ...core.rules import ChangedFile, Diagnostic, Hit

#: How many of ruff's findings one file lists: the most severe first, the rest counted in one finding at the most
#: severe of them, so no tier is lost and the report stays one a person reads. Measured with no cap: a new file of
#: 1,500 unused imports made a 229 KB report and a 490 KB JSON, and the fold took 5.5 s over 8,000 findings in one file.
PER_FILE = 50


def family(code: str) -> str:
    """`PLR0913` -> `PLR`, `E501` -> `E`: the letters ruff prints before the number. One letter can be two linters'
    (mccabe's `C90`, flake8-comprehensions' `C4`), which `tier`'s longest key tells apart."""
    return code.rstrip("0123456789")


def tier[V](table: Mapping[str, V], code: str) -> V | None:
    """The table's entry for `code`: the longest key it begins with inside its own family, so `S` never reaches
    `SIM108`, `C90` reaches `C901` and not `C416`, and `E501` wins over `E`; None where the table has none."""
    keys = [key for key in table if code.startswith(key) and family(key) == family(code)]
    return table[max(keys, key=len)] if keys else None


def fix_of(table: Mapping[str, str], code: str, offered: str | None, label: str | None = None) -> str:
    """Ruff's own fix when it offers one, with ruff's own label for it, else the code's or its family's sentence in
    `table`, else a pointer. Only ruff labels a fix: a reader, or a coding agent, sees which `ruff check --fix` would
    apply unasked, and which needs `--unsafe-fixes` and a person's reading."""
    if offered:
        return f"{offered} (a fix ruff labels {label})" if label else offered
    return tier(table, code) or f"see ruff rule {code}"


def listed(file: ChangedFile, rated: list[tuple[Diagnostic, Severity]], fixes: Mapping[str, str]) -> list[Hit]:
    """Each of ruff's diagnostics on `file`, at the severity the rule rated it, as findings: those on lines the change
    added one by one, at most `PER_FILE` of them, the most severe first; the rest counted, one finding each for the
    lines the change did not add and for the ones past the cap, so the verdict reads the same. `fixes` is the rule's
    own table of sentences, read where ruff offers no fix."""
    tail: list[Hit] = []
    # What ruff reports on lines the change did not add is counted, one finding a file at the most severe of them,
    # so the verdict reads the same. Measured over this repository's 22 commits: 2,101 of lint's 2,435 findings,
    # 86.3%, sat on lines the commit did not add, and listed one by one they buried the change's own.
    aside = [(found, severity) for found, severity in rated if found.line not in file.added]
    if aside:
        tail.append(_counted(file.path, aside, f"{len(aside)} of ruff's findings on lines the change did not add"))
        rated = [(found, severity) for found, severity in rated if found.line in file.added]
    if len(rated) > PER_FILE:
        ranked = sorted(range(len(rated)), key=lambda at: -RANK[rated[at][1]])  # stable: by line
        shown = set(ranked[:PER_FILE])
        rest = [pair for at, pair in enumerate(rated) if at not in shown]
        tail.insert(0, _counted(file.path, rest, f"{len(rest)} more of ruff's findings in this file, not listed"))
        rated = [pair for at, pair in enumerate(rated) if at in shown]
    hits = [
        Hit(
            file.path,
            found.line,
            found.source,
            title=f"{found.code} {found.message}",
            severity=severity,
            fix=fix_of(fixes, found.code, found.fix, found.applicability),
        )
        for found, severity in rated
    ]
    return hits + tail


def _counted(path: str, rated: list[tuple[Diagnostic, Severity]], title: str) -> Hit:
    """`rated` as one finding at the file: how many of each code, at the most severe of them, so no tier is lost and
    the verdict reads the same."""
    codes = Counter(found.code for found, _severity in rated)
    return Hit(
        path,
        None,
        ", ".join(f"{code} x{count}" for code, count in codes.most_common()),
        title=title,
        severity=max((severity for _found, severity in rated), key=RANK.__getitem__),
        fix="Run ruff on the file to see each one.",
    )

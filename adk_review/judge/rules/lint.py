"""What ruff reports on each changed Python file at the head, at a severity and with a fix this file names per code.

Ruff runs with `--isolated` and the codes below, never the reviewed repository's own configuration: a change must not
be able to configure the rule that judges it, nor, with `--ignore-noqa`, silence it line by line. Running ruff is
`collect/ruff.py`'s; what its diagnostics mean is this file's. Without ruff on `PATH`, or with one that fails, the rule
did not look, and says so: the run is degraded and the report names it, for the reason a failed guideline is named
rather than empty. `uv run` puts the project's own ruff on `PATH`. Every hit on a changed file is reported, on an added
line or not: ruff reads whole files, and a reader told the file and the line can tell the two apart.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from types import MappingProxyType

from ..rule_helpers import RANK, ChangedFile, FileRule, Hit, Severity, is_python, is_test

#: ruff's codes, run --isolated: errors, pyflakes, warnings, bugbear and bandit, then what a reviewer asks for and no
#: other check here makes, at ruff's own thresholds: complexity (C901: 10; PLR0911, PLR0912, PLR0913, PLR0915: 6
#: returns, 12 branches, 5 arguments, 50 statements), blocking calls in async code (ASYNC), a task created and dropped
#: (RUF006), a debugger call left in (T100), commented-out code (ERA001), a bidirectional control that makes code read
#: in another order than it runs (PLE2502), and a name holding a letter that is not ASCII, which can look like another
#: (PLC2401).
SELECT = (
    "E", "F", "W", "B", "S", "C90", "PLR0911", "PLR0912", "PLR0913", "PLR0915", "ASYNC", "RUF006", "T100", "ERA001",
    "PLE2502", "PLC2401",
)  # fmt: skip
#: ruff codes the rule leaves out, "E501" or "S101" say; empty for none.
IGNORE: tuple[str, ...] = ()
#: The line length `E501` holds a line to: None is ruff's own, 88; a project that formats wider names its width here.
LINE_LENGTH: int | None = None
#: A ruff code's severity: the longest key the code begins with inside its own family, keyed by ruff's own prefixes
#: and codes, so `S` never reaches `SIM108` and `C90` reaches `C901` but not `C416`; anything else is minor. An
#: unused import or local is hygiene, not a defect. A file that does not parse is major: ruff reads no other rule
#: in it, so a `shell=True` inside one went unreported and its one finding read as a nit.
SEVERITY: dict[str, Severity] = {
    "F": "major",
    "F401": "minor",
    "F841": "minor",
    "B": "major",
    "S": "major",
    "ASYNC": "major",
    "C90": "major",
    "RUF006": "major",
    "T10": "major",
    "PLE2502": "major",
    "invalid-syntax": "major",
}
#: A code's severity in a test file, where its pattern is how tests are written: `assert` is pytest's way to check,
#: while in library code it vanishes under `python -O`. Measured: every test assert was a major S101, 762 of them in
#: this repository's tests, 99% of its majors.
IN_TESTS: dict[str, Severity] = {"S101": "minor"}
#: The fix a finding names when ruff offers none: ruff says what is wrong, not what to do. Keyed as `SEVERITY` is:
#: by family, since a sentence true of every rule in one is worth more than a guess per code, and by code where the
#: family's would mislead.
FIX = {
    "invalid-syntax": "Fix the syntax: ruff reads no other rule in a file that does not parse.",
    "E501": "Wrap the line.",
    "E": "Correct what pycodestyle reports.",
    "W": "Resolve the style warning.",
    "F401": "Remove the unused import, or export it on purpose.",
    "F841": "Remove the unused variable, or use its value.",
    "F": "Correct what pyflakes reports; an undefined or redefined name is a bug.",
    "B": "Rewrite the pattern: bugbear names shapes known to misbehave.",
    "S": "Use the safe form of the call; the security guideline weighs what is left.",
    "C90": "Split the function until each part reads on its own.",
    "PLR": "Bring the count under ruff's threshold by extracting part of the construct.",
    "ASYNC": "Move the blocking call off the event loop, or await its async equivalent.",
    "RUF006": "Keep a reference to the task and await it, so its failure is seen.",
    "T10": "Remove the debugger call.",
    "ERA": "Delete the commented-out code: version control keeps it.",
    "PLE2502": "Remove the bidirectional control: code should read in the order it runs.",
    "PLC2401": "Spell the name in ASCII, or check each letter is the one it looks like.",
}


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


def severity_of(code: str) -> Severity:
    """The code's tier, else minor."""
    return tier(SEVERITY, code) or "minor"


def fix_for(code: str, offered: str | None, label: str | None = None) -> str:
    """Ruff's own fix when it offers one, with ruff's own label for it, else the code's or its family's sentence,
    else a pointer. Only ruff labels a fix: a reader, or a coding agent, sees which `ruff check --fix` would apply
    unasked, and which needs `--unsafe-fixes` and a person's reading."""
    if offered:
        return f"{offered} (a fix ruff labels {label})" if label else offered
    return tier(FIX, code) or f"see ruff rule {code}"


class Lint(FileRule):
    """A finding ruff can make is one no guideline need spend a model's attention on, and one no model can miss."""

    id = "lint"
    severity = "minor"  # a code the table above does not name
    title = "what ruff reports, at a severity for each code"
    fix = "Correct what ruff reports."
    tool = "ruff"
    tool_settings = MappingProxyType({"select": SELECT, "ignore": IGNORE, "line_length": LINE_LENGTH})

    def check(self, file: ChangedFile) -> list[Hit]:
        if not is_python(file.path, lambda: file.line(1)):  # ruff reads Python: another file's text is never asked for
            return []
        tests = is_test(file.path)
        hits = [
            Hit(
                file.path,
                found.line,
                found.source,
                title=f"{found.code} {found.message}",
                severity=IN_TESTS[found.code] if tests and found.code in IN_TESTS else severity_of(found.code),
                fix=fix_for(found.code, found.fix, found.applicability),
            )
            for found in file.diagnostics
        ]
        tail: list[Hit] = []
        # What ruff reports on lines the change did not add is counted, one finding a file at the most severe of them,
        # so the verdict reads the same. Measured over this repository's 22 commits: 2,101 of lint's 2,435 findings,
        # 86.3%, sat on lines the commit did not add, and listed one by one they buried the change's own.
        aside = [hit for hit in hits if hit.line is not None and hit.line not in file.added]
        if aside:
            tail.append(_counted(file.path, aside, f"{len(aside)} of ruff's findings on lines the change did not add"))
            hits = [hit for hit in hits if hit.line is None or hit.line in file.added]
        if len(hits) > PER_FILE:
            listed = {
                id(hit) for hit in sorted(hits, key=lambda hit: -RANK[hit.severity or self.severity])[:PER_FILE]
            }  # stable: by line
            rest = [hit for hit in hits if id(hit) not in listed]
            tail.insert(0, _counted(file.path, rest, f"{len(rest)} more of ruff's findings in this file, not listed"))
            hits = [hit for hit in hits if id(hit) in listed]
        return hits + tail


def _counted(path: str, hits: list[Hit], title: str) -> Hit:
    """`hits` as one finding at the file: how many of each code, at the most severe of them, so no tier is lost and
    the verdict reads the same."""
    codes = Counter(hit.title.split()[0] for hit in hits if hit.title)  # each of lint's hits titles its code
    return Hit(
        path,
        None,
        ", ".join(f"{code} x{count}" for code, count in codes.most_common()),
        title=title,
        severity=max((hit.severity or Lint.severity for hit in hits), key=RANK.__getitem__),
        fix="Run ruff on the file to see each one.",
    )

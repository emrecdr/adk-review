"""What ruff reports on each changed Python file at the head, at a severity and with a fix this file names per code.

Ruff runs with `--isolated` and the codes below, never the reviewed repository's own configuration: a change must not
be able to configure the rule that judges it, nor, with `--ignore-noqa`, silence it line by line. Running ruff is
`collect/ruff.py`'s; what its diagnostics mean is this file's. The ruff installed beside the reviewer runs, else the
one on `PATH`; without either, or with one that fails, the rule did not look, and says so: the run is degraded and the
report names it, for the reason a failed guideline is named rather than empty. Each hit on a line the change added is
listed, at most `PER_FILE` a file and the rest counted; those on the lines it did not add are counted too, one finding
a file at the most severe of them, since ruff reads whole files and listed one by one they buried the change's own.
"""

from __future__ import annotations

from types import MappingProxyType

from ..rule_helpers import ChangedFile, FileRule, Hit, Severity, is_test, listed, tier

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
#: ruff codes the rule leaves out. E501, a line's length, is a project's formatter's, and the rule reads no project's
#: settings, so no width fits every project. Measured at ruff's 88 over each project's library code, its tests left out:
#: 83% of litellm's findings, 89% of pydantic's, 36% of google-adk's and 99% of this one's, formatted at 120 (80% with
#: its tests); none of httpx's, formatted at 88, where its formatter holds the width already. An operator takes E501
#: back by leaving it out of here.
IGNORE: tuple[str, ...] = ("E501",)
#: The line length `E501` holds a line to, where an operator takes it back: None is ruff's own, 88.
LINE_LENGTH: int | None = None
#: A ruff code's severity: the longest key the code begins with inside its own family, keyed by ruff's own prefixes
#: and codes, so `S` never reaches `SIM108` and `C90` reaches `C901` but not `C416`; anything else is minor. An
#: unused import or local is hygiene, not a defect. A file that does not parse is major: ruff reads no other rule
#: in it, so a `shell=True` inside one went unreported and its one finding read as a nit. Bandit's codes stay major,
#: under the default bar: measured over the 5.1 million lines of library code this project installs, ruff named four
#: shell calls a possible injection, and 18 `exec`, 35 `eval`, 23 `pickle` and 97 SQL strings; of 28 read in context,
#: every one was what its code meant, a command to run, code to generate, a table's name, so a blocker would stop such
#: a change by its shape alone. A guideline, which reads what feeds the call, can name one a blocker, and `fail_on =
#: "major"` makes rules alone stop at it. S603 and S607 are minor: one names every `subprocess` call whose arguments are
#: not a literal list, the other every program named without its full path, so neither reads what feeds the call, and
#: each is hardening, as the guidelines' scale calls it. Measured over this repository whole, they were 31 of its 42
#: lint majors, and any change to a file that runs a process carried one.
SEVERITY: dict[str, Severity] = {
    "F": "major",
    "F401": "minor",
    "F841": "minor",
    "B": "major",
    "S": "major",
    "S603": "minor",
    "S607": "minor",
    "ASYNC": "major",
    "C90": "major",
    "RUF006": "major",
    "T10": "major",
    "PLE2502": "major",
    "invalid-syntax": "major",
}
#: Codes left out in a test file, where their pattern is how tests are written: `assert` is pytest's way to check,
#: while in library code it vanishes under `python -O`, as `assert False` does, which ruff names B011 beside S101.
#: Measured: every test assert was a major S101, 762 of them in this repository's tests, 99% of its majors; rated minor
#: they still filled each report.
IN_TESTS: frozenset[str] = frozenset({"S101", "B011"})
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
    "F": "Correct what pyflakes reports. An undefined or redefined name is a bug.",
    "B": "Rewrite the pattern: bugbear names shapes known to misbehave.",
    "S": "Use the safe form of the call. The security guideline weighs the rest.",
    "C90": "Split the function until each part reads on its own.",
    "PLR": "Bring the count under ruff's threshold by extracting part of the construct.",
    "ASYNC": "Move the blocking call off the event loop, or await its async equivalent.",
    "RUF006": "Keep a reference to the task and await it, so its failure is seen.",
    "T10": "Remove the debugger call.",
    "ERA": "Delete the commented-out code: version control keeps it.",
    "PLE2502": "Remove the bidirectional control: code should read in the order it runs.",
    "PLC2401": "Spell the name in ASCII, or check each letter is the one it looks like.",
}


def severity_of(code: str) -> Severity:
    """The code's tier, else minor."""
    return tier(SEVERITY, code) or "minor"


class Lint(FileRule):
    """A finding ruff can make is one no guideline need spend a model's attention on, and one no model can miss."""

    id = "lint"
    severity = "minor"  # a code the table above does not name
    reads_tests = True  # ruff reads a test too, less the codes in `IN_TESTS`
    title = "what ruff reports, at a severity for each code"
    fix = "Correct what ruff reports."
    tool = "ruff"
    tool_settings = MappingProxyType({"select": SELECT, "ignore": IGNORE, "line_length": LINE_LENGTH})

    def check(self, file: ChangedFile) -> list[Hit]:
        leave_out = IN_TESTS if is_test(file.path) else ()
        return listed(file, lambda found: severity_of(found.code), FIX, leave_out=leave_out)

"""Python's traps ruff can prove in a changed file: code that does not do what it reads, beside what `lint` selects.

A naive datetime, a default evaluated once and shared, a `return` in `finally` that swallows the exception, a call whose
arguments Python refuses, a `subprocess.run` whose failure goes unseen: each a defect ruff names from the code alone,
and none of them in `lint`'s families. Run with `--isolated` as `lint` runs. A test is read too: an `assert` on a string
literal never fails, so the test it stands in always passes. Measured over whole files: 0.4 a thousand lines of
google-adk, 1.4 of pydantic, 0.7 of httpx and 1.3 of litellm.
"""

from __future__ import annotations

from types import MappingProxyType

from ..rule_helpers import ChangedFile, FileRule, Hit, Severity, is_python, listed, tier

#: ruff's codes, run --isolated: flake8-datetimez's, pylint's errors, and the warnings and ruff's own codes that name
#: code doing other than it reads; with the four of flake8-simplify, flake8-pie and the logging codes that are defects
#: rather than style, which `simplify` leaves out.
SELECT = (
    "DTZ", "PLE",
    "PLW0120", "PLW0127", "PLW0128", "PLW0129", "PLW0131", "PLW0133", "PLW0177", "PLW0245", "PLW0406", "PLW0602",
    "PLW0642", "PLW0711", "PLW1501", "PLW1507", "PLW1508", "PLW1509", "PLW1510", "PLW1641", "PLW2101", "PLW2901",
    "RUF008", "RUF009", "RUF012", "RUF016", "RUF018", "RUF024", "RUF026", "RUF030", "RUF032", "RUF033", "RUF034",
    "RUF040", "RUF043", "RUF048", "RUF049", "RUF060", "RUF064", "RUF071",
    "SIM107", "SIM115", "PIE794", "PIE796", "G101",
)  # fmt: skip
#: `lint` names a bidirectional control already: one finding for it, not two.
IGNORE = ("PLE2502",)
#: A code's severity: the longest key the code begins with inside its own family. Major where the code misbehaves
#: whenever it runs; any other is minor, misbehaving only on some input, or a trap a reader should see.
SEVERITY: dict[str, Severity] = {
    "PLE": "major", "PLW0129": "major", "PLW0133": "major", "PLW0177": "major", "PLW0245": "major",
    "PLW0711": "major", "PLW1501": "major", "PLW2101": "major", "RUF008": "major", "RUF009": "major",
    "RUF016": "major", "RUF018": "major", "RUF024": "major", "RUF026": "major", "RUF030": "major", "RUF033": "major",
    "RUF034": "major", "RUF060": "major", "RUF064": "major", "SIM107": "major", "PIE794": "major", "PIE796": "major",
    "G101": "major",
}  # fmt: skip
#: The sentence a finding names where ruff offers no fix, by family, or by code where the family's would mislead.
FIX = {
    "DTZ": (
        "Give it a timezone, and check what it is compared with or stored beside: an aware value never equals a "
        "naive one."
    ),
    "PLE": "Correct it: Python raises here, or does other than the code reads.",
    "PLW": "Rewrite it as ruff names: the code does not do what it reads.",
    "PLW1510": "Pass check=True, or read the return code: a command that fails otherwise goes unseen.",
    "PLW2901": "Give the inner value a name of its own: the rest of the loop reads the one it overwrote.",
    "RUF": "Rewrite it as ruff names: the code does not do what it reads.",
    "RUF009": "Use field(default_factory=...): a default is evaluated once, when the class is defined, and shared.",
    "RUF012": "Annotate it ClassVar, or build it for each instance: a mutable class default is one object, shared.",
    "SIM107": "Move the return out of finally: a return there swallows the exception being raised.",
    "SIM115": "Open it in a with block, so it is closed when an exception is raised.",
    "PIE794": "Remove the earlier definition: the class keeps only the last.",
    "PIE796": "Give each member its own value: a repeated one makes the second an alias of the first.",
    "G101": "Rename the key in extra: one that clashes with a LogRecord field raises when the line is logged.",
}


class Pitfalls(FileRule):
    """The traps a careful reviewer looks for in Python, where code can name them: what a model would otherwise be
    asked to notice, and could miss."""

    id = "pitfalls"
    severity = "minor"  # a code the table above does not name
    reads_tests = True  # a test can hold a trap too: an assert that never fails
    title = "Python traps ruff can prove: code that does not do what it reads"
    fix = "Rewrite it as ruff names."
    tool = "ruff"
    tool_settings = MappingProxyType({"select": SELECT, "ignore": IGNORE, "line_length": None})

    def check(self, file: ChangedFile) -> list[Hit]:
        if not is_python(file.path, lambda: file.line(1)):  # ruff reads Python: another file's text is never asked for
            return []
        return listed(file, [(found, tier(SEVERITY, found.code) or self.severity) for found in file.diagnostics], FIX)

"""What ruff can write more simply in a changed Python file, doing the same: the cleanup a review can decide by code.

The codes are flake8-simplify's, flake8-return's, flake8-pie's, flake8-comprehensions', perflint's, refurb's and the
logging-format ones, with the few of pylint's and ruff's own that say a thing more simply, run with `--isolated` as
`lint` runs, never with the reviewed repository's configuration. Left out: what is taste rather than simpler — an
`else` after a `return`, a ternary, `contextlib.suppress`, `with` statements merged, a dict in place of `if`s; what
is a defect, and so `pitfalls`'s — a `return` in `finally`, a file opened outside `with`, a class field or an enum
value given twice, a logging `extra` key that clashes; `PERF203`, whose advice ruff keeps to Python before 3.11; and
pyupgrade's, whose floor would come from the reviewed repository's `requires-python`. Measured over whole files, the
codes kept find 2.9 a thousand lines of google-adk, 2.1 of pydantic, 1.0 of httpx and 3.4 of litellm; TRY003, left
out with the rest of flake8-tryceratops, alone found 1,107 in google-adk.
"""

from __future__ import annotations

from types import MappingProxyType

from ..rule_helpers import ChangedFile, FileRule, Hit, is_python, listed

#: ruff's codes, run --isolated: what reads more simply and does the same.
SELECT = (
    "SIM", "RET501", "RET502", "RET503", "RET504", "PIE", "C4", "PERF", "FURB", "G",
    "PLR0402", "PLR1711", "PLR1714", "PLR1716", "PLR1730", "PLR1733", "PLR1736", "PLR5501", "PLW0108", "PLW3301",
    "RUF015", "RUF017", "RUF019", "RUF037", "RUF046", "RUF051", "RUF057", "RUF058", "RUF059",
)  # fmt: skip
#: Taste, a defect `pitfalls` names, and advice for an older Python: see above.
IGNORE = (
    "SIM105", "SIM107", "SIM108", "SIM112", "SIM115", "SIM116", "SIM117", "PIE794", "PIE796", "PERF203", "G101",
)  # fmt: skip
#: The sentence a finding names where ruff offers no fix, by family, or by code where the family's would mislead.
FIX = {
    "SIM": "Write the simpler form ruff names: it does the same.",
    "RET": "Return the value directly, and say where the function returns None.",
    "PIE": "Remove what does nothing.",
    "C4": "Write the comprehension or the literal directly.",
    "PERF": "Build it in one pass, as ruff names.",
    "FURB": "Use the shorter idiom ruff names.",
    "G": "Pass the values to the logging call: it formats them only when the line is logged.",
    "PLR": "Write the simpler form ruff names.",
    "PLW": "Write the simpler form ruff names.",
    "RUF": "Write the simpler form ruff names.",
    "RUF017": "Chain the lists, with itertools.chain or a comprehension: a sum copies each list again.",
    "RUF059": "Name the value it never reads `_`.",
}


class Simplify(FileRule):
    """The part of a simplify pass code can decide: a form ruff proves does the same, said more simply. What only a
    reader can weigh — state that could be derived, code copied with a small change, a fix at the wrong depth — is
    the complexity, efficiency and altitude guidelines'."""

    id = "simplify"
    severity = "minor"
    title = "code ruff can write more simply, doing the same"
    fix = "Write the simpler form ruff names."
    tool = "ruff"
    tool_settings = MappingProxyType({"select": SELECT, "ignore": IGNORE, "line_length": None})

    def check(self, file: ChangedFile) -> list[Hit]:
        if not is_python(file.path, lambda: file.line(1)):  # ruff reads Python: another file's text is never asked for
            return []
        return listed(file, [(found, self.severity) for found in file.diagnostics], FIX)

"""What ruff can write more simply in a changed Python file: the cleanup a review can name by code.

The codes are flake8-simplify's, flake8-return's, flake8-pie's, flake8-comprehensions', perflint's and refurb's, with
the few of pylint's and ruff's own that say a thing more simply, run with `--isolated` as `lint` runs, never with the
reviewed repository's configuration. Ruff labels each fix safe or unsafe, finding by finding, and the hand-off carries
the label: an unsafe one is mostly a comment the rewrite could drop, and a tidy makes it only where it does the same.
Left out: each code whose fix ruff documents as changing what a tidy keeps — when or whether operands are evaluated,
the exception raised, a result on a NaN or a tie, a list mutated in place, what an argument may be — and the logging
codes, since a log line is a contract; taste — an `else` after a `return`, a ternary, `contextlib.suppress`, `with`
statements merged, a dict in place of `if`s; a defect, which `lint` or `pitfalls` names; `PERF203`, whose advice ruff
keeps to Python before 3.11; and pyupgrade's, whose floor would come from the reviewed repository's `requires-python`.
Measured over whole files, the codes kept find 2.3 a thousand lines of google-adk, 2.0 of pydantic, 1.0 of httpx and
3.0 of litellm; TRY003, left out with the rest of flake8-tryceratops, alone found 1,107 in google-adk.
"""

from __future__ import annotations

from types import MappingProxyType

from ..rule_helpers import ChangedFile, FileRule, Hit, listed

#: ruff's codes, run --isolated: what reads more simply.
SELECT = (
    "SIM", "RET501", "RET502", "RET503", "RET504", "PIE", "C4", "PERF", "FURB",
    "PLR0402", "PLR1711", "PLR1716", "PLR1730", "PLR1733", "PLR1736", "PLR5501", "PLW0108", "PLW0127",
    "RUF019", "RUF037", "RUF046", "RUF051", "RUF057", "RUF058", "RUF059",
)  # fmt: skip
#: Taste; a defect `lint` or `pitfalls` names; advice for an older Python; and each code whose fix ruff documents as
#: changing what a tidy keeps: when or whether operands are evaluated, the exception raised, a result on a NaN or a
#: tie, a list mutated in place, what an argument may be.
IGNORE = (
    "SIM105", "SIM107", "SIM108", "SIM112", "SIM115", "SIM116", "SIM117", "PIE794", "PIE796", "PERF203",
    "C417", "C419", "SIM109", "FURB161", "FURB162", "FURB164", "FURB166", "FURB171", "FURB187", "FURB192",
)  # fmt: skip
#: The sentence a finding names where ruff offers no fix, by family, or by code where the family's would mislead. The
#: finding's title is ruff's own message, which names the simpler form.
SIMPLER = "Write the simpler form ruff names."
FIX = {
    **dict.fromkeys(("SIM", "PIE", "C4", "PLR", "PLW", "RUF"), SIMPLER),
    "RET": "Return the value directly, and say where the function returns None.",
    "PERF": "Build it in one pass, as ruff names.",
    "FURB": "Use the shorter idiom ruff names.",
    "RUF059": "Name the value it never reads `_`.",
    "PLW0127": "Remove the assignment of a name to itself: it does nothing.",
}


class Simplify(FileRule):
    """The part of a simplify pass code can name: a simpler form ruff knows. What only a reader can weigh — state that
    could be derived, code copied with a small change, a fix at the wrong depth — is the complexity, efficiency and
    altitude guidelines'."""

    id = "simplify"
    severity = "minor"
    cleanup = True  # how the code reads, never what it does
    title = "code ruff can write more simply"
    fix = SIMPLER
    tool = "ruff"
    tool_settings = MappingProxyType({"select": SELECT, "ignore": IGNORE, "line_length": None})

    def check(self, file: ChangedFile) -> list[Hit]:
        return listed(file, lambda _found: self.severity, FIX)

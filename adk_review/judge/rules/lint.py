"""What ruff reports on each changed Python file at the head, at a severity and with a fix this file names per code.

Ruff runs with `--isolated` and the codes below, never the reviewed repository's own configuration: a change must not
be able to configure the rule that judges it, nor, with `--ignore-noqa`, silence it line by line. Running ruff is
`collect/ruff.py`'s; what its diagnostics mean is this file's. The ruff installed beside the reviewer runs, else the
one on `PATH`; without either, or with one that fails, the rule did not look, and says so: the run is degraded and the
report names it, for the reason a failed guideline is named rather than empty. Each hit the change caused, on a line
it added or one it did not touch, is listed, at most `PER_FILE` a file and the rest counted; what the base held too on
an untouched line is counted in one old finding a file, apart and never at the bar, since ruff reads whole files and
listed one by one they buried the change's own.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType

from ..rule_helpers import (
    ChangedFile,
    Diagnostic,
    FileRule,
    Hit,
    Severity,
    holds_no_secret,
    is_credential,
    is_test,
    listed,
    nodes_in,
    tier,
)

#: ruff's codes, run --isolated: errors, pyflakes, warnings, bugbear and bandit, then what a reviewer asks for and no
#: other check here makes, at ruff's own thresholds: complexity (C901: 10; PLR0911, PLR0912, PLR0913, PLR0915: 6
#: returns, 12 branches, 5 arguments, 50 statements), async code that blocks the event loop or misuses a timeout, a
#: sleep or an await (ASYNC), a task created and dropped (RUF006), a debugger call left in (T100), commented-out code
#: (ERA001), a bidirectional control that makes code read in another order than it runs (PLE2502), and a name holding a
#: letter that is not ASCII, which can look like another (PLC2401).
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
#: The names Databricks defines in every notebook it runs, as its documentation lists them, which ruff, reading the
#: notebook as a module, calls undefined (F821). Read by review on a branch of 20 notebooks: 192 of lint's findings
#: named `spark`, `dbutils` and `display`, nearly all of its 86 majors, where the project's own ruff reported none.
NOTEBOOK_NAMES = frozenset({"spark", "dbutils", "display", "displayHTML", "sql", "udf"})
#: The first line Databricks writes in a notebook kept as a `.py` source file.
NOTEBOOK_MARK = "# Databricks notebook source"
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
    "S101": "Raise an error instead of the assert: python -O removes every assert.",
    "S104": "Bind to the one interface the service needs, not to every interface.",
    "S105": "If it is a credential, read it from the environment or a secret store.",
    "S106": "If it is a credential, read it from the environment or a secret store.",
    "S107": "If it is a credential, read it from the environment or a secret store.",
    "S108": "Make the file with the tempfile module, never at a fixed path.",
    "S110": "Log the exception, or catch only the error you expect.",
    "S112": "Log the exception, or catch only the error you expect.",
    "C90": "Split the function until each part reads on its own.",
    "PLR": "Bring the count under ruff's threshold by extracting part of the construct.",
    "ASYNC2": "Move the blocking call off the event loop, or await its async equivalent.",
    "ASYNC100": "Add an await inside the timeout block, or remove the block: a timeout acts only at an await.",
    "ASYNC105": "Await the call: without an await, it never runs.",
    "ASYNC109": "Drop the timeout parameter, and let the caller wrap the call in a timeout block.",
    "ASYNC110": "Wait on an Event, not on a sleep in a loop.",
    "ASYNC116": "Use sleep_forever() for a sleep longer than a day.",
    "RUF006": "Keep a reference to the task and await it, so its failure is seen.",
    "T10": "Remove the debugger call.",
    "ERA": "Delete the commented-out code: version control keeps it.",
    "PLE2502": "Remove the bidirectional control: code should read in the order it runs.",
    "PLC2401": "Spell the name in ASCII, or check each letter is the one it looks like.",
}


def severity_of(code: str) -> Severity:
    """The code's tier, else minor."""
    return tier(SEVERITY, code) or "minor"


def is_databricks_notebook(file: ChangedFile) -> bool:
    """Whether the file is a Databricks notebook: its first line the mark, a byte order mark before it aside. Databricks
    reads the mark on the first line alone, so a comment above it makes the file a plain module, where the names are
    undefined. Read by review: comments let in above the mark passed over a plain module's undefined `spark`."""
    return file.line(1).lstrip("\ufeff").strip() == NOTEBOOK_MARK


def defined_by_databricks(file: ChangedFile, found: Diagnostic) -> bool:
    """Whether `found` calls undefined a name Databricks defines, in a file marked as a Databricks notebook."""
    named = found.message.split("`")
    return found.code == "F821" and len(named) > 1 and named[1] in NOTEBOOK_NAMES and is_databricks_notebook(file)


#: Ruff's codes for a string given to a name that says password or token: assigned or compared, passed as an
#: argument, or a parameter's default. Ruff weighs the name alone, and the value as `named-secrets` does.
NAMED_PASSWORD = frozenset({"S105", "S106", "S107"})
#: A string ruff names a password is passed over only where it is shorter than this, or holds a space, and no
#: credential check takes it. Read by review: `named-secrets` asks a digit of a name other than a password's, so a
#: secret of letters alone, `client_secret = "AbCdEfGhIjKlMnOp"`, was passed over and no rule named it.
PLAIN_UNDER_CHARS = 8


#: Where a string of a file is looked up: the line it starts on, or a place on that line, the line and a column
#: counted from 1 in characters, as ruff gives a finding's.
type Place = int | tuple[int, int]


def strings_by_place(file: ChangedFile) -> dict[Place, list[str]]:
    """Each string literal of `file` by where ruff places a finding on it: the place it starts at, and for one given to
    a keyword the place the keyword starts at, where ruff puts an argument's finding; and by the line it starts on, for
    a finding whose place holds none. Nothing where the file does not parse. Read by review: by the line alone, the
    address in `connect("https://…", token="Bearer ")` was weighed as the token's, and a value on the line after its
    keyword was not found. Ruff counts a column in characters, where Python counts a line's bytes."""
    found: dict[Place, list[str]] = {}

    def column(node: ast.Constant | ast.keyword) -> int:
        line = file.line(node.lineno)
        before = node.col_offset if line.isascii() else len(line.encode()[: node.col_offset].decode(errors="ignore"))
        return before + 1

    for node in nodes_in(file.module, ast.Constant, ast.keyword) or ():
        given = node.value if isinstance(node, ast.keyword) else node
        if isinstance(given, ast.Constant) and isinstance(given.value, str):
            found.setdefault((node.lineno, column(node)), []).append(given.value)
            if node is given:
                found.setdefault(node.lineno, []).append(given.value)
    return found


def _named(found: Diagnostic, strings: Callable[[], Mapping[Place, Sequence[str]]]) -> tuple[str, Sequence[str]] | None:
    """The name ruff quotes in an S105, S106 or S107 and the string given to it, the one its place holds, else each
    string on its line; None for another code, or where no string was read, which keeps its finding as ruff made it."""
    quoted = found.message.split('"')
    if found.code not in NAMED_PASSWORD or len(quoted) < 3:
        return None
    given = strings().get((found.line, found.column or 0)) or strings().get(found.line)
    return (quoted[1], given) if given else None


def no_credential(found: Diagnostic, strings: Callable[[], Mapping[Place, Sequence[str]]]) -> bool:
    """Whether `found` is S105, S106 or S107 where the string given to the name ruff quotes is plain: short or spaced,
    and no credential, as `named-secrets` weighs one; where no string was read, it keeps its finding. Read by review:
    ruff made `token = "("`, `"<eos>"` and `"Bearer "` each major, where `named-secrets` found none of them."""
    if (named := _named(found, strings)) is None:
        return False

    def plain(value: str) -> bool:
        short = len(value.strip()) < PLAIN_UNDER_CHARS or any(ch.isspace() for ch in value.strip())
        return short and not is_credential(named[0], value, quoted=True, written=True)

    return all(plain(value) for value in named[1])


def names_no_secret(found: Diagnostic, strings: Callable[[], Mapping[Place, Sequence[str]]]) -> bool:
    """Whether `found` is S105, S106 or S107 where the name ruff quotes, or the string given to it, rules a
    credential out: a name about one, a placeholder, a label. Rated minor, never passed over, so no finding is lost.
    Read by review: a string of eight characters with no space kept its major whatever it was, `token_type =
    "authorization_code"` and `token_url = "https://…"` among 91 of the 111 false majors measured in 14 libraries. A
    secret of letters alone, which no other rule names, is none of these and stays major."""
    named = _named(found, strings)
    return named is not None and all(holds_no_secret(named[0], value, quoted=True) for value in named[1])


class Lint(FileRule):
    """A finding ruff can make is one no guideline need spend a model's attention on, and one no model can miss."""

    id = "lint"
    severity = "minor"  # a code the table above does not name
    reads_tests = True  # ruff reads a test too, less the codes in `IN_TESTS`
    title = "what ruff reports, at a severity for each code"
    fix = "Correct what ruff reports."
    why = "Ruff names code that fails, leaks or misleads. Run ruff on the file to see each code and its page."
    tool = "ruff"
    tool_settings = MappingProxyType({"select": SELECT, "ignore": IGNORE, "line_length": LINE_LENGTH})

    def check(self, file: ChangedFile) -> list[Hit]:
        leave_out = IN_TESTS if is_test(file.path) else ()
        # The file's strings, read once and only where ruff named a password's code: `listed` reads a file's
        # diagnostics only once it knows the file is Python, and a file not read whole is no hole of this.
        strings = functools.cache(lambda: strings_by_place(file))
        return listed(
            file,
            lambda found: "minor" if names_no_secret(found, strings) else severity_of(found.code),
            FIX,
            leave_out=leave_out,
            passes_over=lambda found: defined_by_databricks(file, found) or no_credential(found, strings),
        )

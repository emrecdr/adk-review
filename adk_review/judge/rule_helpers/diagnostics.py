"""An outside tool's diagnostics, read as a rule that names ruff reads them: a code's tier in the rule's own tables,
the fix to name, and one file's findings, the change's own lines listed and the rest counted.

A rule that names ruff keeps its codes, its severities and its fix sentences; what it does with ruff's answer is the
same for each, and lives here once.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Mapping

from ...core.findings import RANK, Severity
from ...core.rules import ChangedFile, Diagnostic, Hit, is_python

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
    apply unasked, and which needs `--unsafe-fixes` and a person's reading. A display-only fix, which ruff never
    applies, gives way where `table` names the code itself: read by review, PLW1510's `check=False` reached the
    comment where the rule's own `check=True` did not, and kept unseen the failure the rule names."""
    if offered and not (label == "displayonly" and code in table):
        return f"{offered} (a fix ruff labels {label})" if label else offered
    return tier(table, code) or f"see ruff rule {code}"


def listed(
    file: ChangedFile,
    rate: Callable[[Diagnostic], Severity],
    fixes: Mapping[str, str],
    leave_out: Collection[str] = (),
    passes_over: Callable[[Diagnostic], bool] | None = None,
    own: Callable[[Diagnostic], bool] | None = None,
) -> list[Hit]:
    """Each of ruff's diagnostics on `file`, at the severity `rate` gives it, as findings: those the change caused one
    by one, on its own lines or on one it did not touch, at most `PER_FILE` of them, the most severe first, and those
    past the cap counted in one finding. What the base held too is counted in one old finding, apart and never at the
    bar; what lies on an untouched line and was not set against the base, in one finding at the bar, as before. `fixes`
    is the rule's own table of sentences, read where ruff offers no fix. A diagnostic whose code is in `leave_out`, or
    that `passes_over` names, is neither listed nor counted; one `own` names is the change's, on a line it did not
    touch and whatever the base held: what a rule weighs beyond ruff's words, the base's diagnostic cannot answer for.
    None for a file that is not Python: ruff reads Python, and another file's text is never asked for, so one not read
    whole is no rule's hole."""
    if not is_python(file.path, lambda: file.line(1)):
        return []
    rated = [
        (found, rate(found))
        for found in file.diagnostics
        if found.code not in leave_out and not (passes_over and passes_over(found))
    ]
    tail: list[Hit] = []

    # What ruff reports on lines the change did not add is counted, one finding a file at the most severe of them.
    # Measured over this repository's 22 commits: 2,101 of lint's 2,435 findings, 86.3%, sat on lines the commit did
    # not add, and listed one by one they buried the change's own. Only what the base held too was already there, and
    # is old: apart, never at the bar. One the change made on a line it did not touch, an old line's name left
    # undefined by an import it deleted, is its own, as is one the rule's `own` names; one not set against the base is
    # counted at the bar, as before.
    def caused(found: Diagnostic) -> bool:
        return found.line in file.added or found.known is False or bool(own and own(found))

    untouched = [(found, severity) for found, severity in rated if not caused(found)]
    for known, why in ((True, BEFORE_THIS_CHANGE), (None, NOT_COMPARED)):
        if counted := [(found, severity) for found, severity in untouched if found.known is known]:
            one = len(counted) == 1
            what = f"{len(counted)} ruff finding{'' if one else 's'}{' already in this file,' * bool(known)}"
            title = f"{what} on {'a line' if one else 'lines'} this change did not touch"
            tail.append(_counted(file.path, counted, title, why=why, old=bool(known)))
    rated = [(found, severity) for found, severity in rated if caused(found)]
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
            why=found.url and f"Ruff's page for {found.code} says why it matters, with an example: {found.url}",
        )
        for found, severity in rated
    ]
    return hits + tail


def _slug(diag: Diagnostic) -> str | None:
    """The rule's documentation slug from its URL: e.g. 'hardcoded-password-string' for S105."""
    if not diag.url:
        return None
    clean = diag.url.split("?")[0].split("#")[0].removesuffix(".html").rstrip("/")
    slug = clean.split("/")[-1]
    return slug if slug and slug != diag.code else None


#: A ruff rule's code, as `--select` takes one. `invalid-syntax`, the collector's name for a file that does not
#: parse, is none, and ruff refused a command selecting it, exit 2; it names a parse error whatever it selects.
_RULE_CODE = re.compile(r"[A-Z]+[0-9]+")


#: Why a roll-up of ruff's findings on untouched lines counts at the bar: with no base read, the review cannot tell.
NOT_COMPARED = "The review could not set these lines against the base, so this change may have caused them."
#: Why a roll-up of ruff's findings on lines the change did not touch is there: the change did not make them, and, asked
#: by the owner, the Boy Scout rule expects them fixed all the same, though they never block a merge.
BEFORE_THIS_CHANGE = (
    "This change did not add these lines. By the Boy Scout rule, we expect you to fix them while you are here."
)
#: How many lines a roll-up names for one code before it counts the rest.
LINES_NAMED = 5


def _on_lines(numbers: list[int]) -> str:
    """Where a code's findings are, as a reader finds them: `on line 12`, `on lines 3, 9 and 12`, at most `LINES_NAMED`
    lines and the rest counted."""
    lines = sorted(set(numbers))
    if len(lines) == 1:
        return f"on line {lines[0]}"
    named, more = [str(n) for n in lines[:LINES_NAMED]], len(lines) - LINES_NAMED
    if more > 0:
        return f"on lines {', '.join(named)} and {more} more"
    return f"on lines {', '.join(named[:-1])} and {named[-1]}"


def _counted(path: str, rated: list[tuple[Diagnostic, Severity]], title: str, why: str = "", old: bool = False) -> Hit:
    """`rated` as one finding at the file: how many of each code, with ruff's name for it, at the most severe of them,
    so no tier is lost, and `old` where the base held them all, which keeps it from the bar; and the command that shows
    them as the review ran ruff. Read by
    review: a bare `ruff check` read the project's own config and honoured its noqa, and said "All checks passed!" over
    a counted S105. The file is the finding's own and never in the command: a path is the branch's text, and a shell
    runs what double quotes hold, `$(…)` among it. The most severe code leads, then the count, a code rated two ways
    at its most severe: read by review, listed by count alone, four unused imports led one unsafe shell call under
    "Major findings", and nothing said which code made the finding a major one."""
    codes = Counter(found.code for found, _severity in rated)
    slugs = {found.code: _slug(found) for found, _severity in rated}
    lines = {code: [found.line for found, _severity in rated if found.code == code] for code in codes}
    worst: dict[str, int] = {}
    for found, severity in rated:
        worst[found.code] = max(worst.get(found.code, RANK[severity]), RANK[severity])
    counted = ", ".join(
        f"{code}{f' ({slugs[code]})' if slugs[code] else ''} x{n} {_on_lines(lines[code])}"
        for code, n in sorted(codes.items(), key=lambda pair: (-worst[pair[0]], -pair[1]))  # stable: ruff's own order
    )
    selected = ",".join(sorted(code for code in codes if _RULE_CODE.fullmatch(code)))
    command = "ruff check --isolated --ignore-noqa" + (f" --select {selected}" if selected else "")
    return Hit(
        path,
        None,
        counted,
        title=title,
        severity=max((severity for _found, severity in rated), key=RANK.__getitem__),
        fix=f"Run `{command}` on the file to see each one.",
        why=why,
        old=old,
    )

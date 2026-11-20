"""What a change breaks where no diff shows it, as pyrefly reads the head's tree against the base's: a method, an
attribute, a parameter or a type the change altered, in a file it left alone or on a line of a changed file it kept;
and a call the change added whose arguments do not fit another file's function, touched or not. `types` reads each
changed file alone, a name it imports read as `Any`, and `callers` reads no types, so a call through an object,
`repo.get_user(3)`, is read here or nowhere.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from types import MappingProxyType

from ..rule_helpers import PER_FILE, UNPACKED_EXTRA, Diagnostic, Entry, Hit, Tree, TreeRule, is_test

#: The kinds named on a line the change added: what a call, an attribute, a value or an override breaks across files,
#: which `types` cannot see, reading each changed file alone, a base class from another file `Any`. Read by review, and
#: reproduced: `bad-override` was left out as the change's own, and an override another file's change broke was named
#: by no rule where the change added a decorator above its `def`, whose error covers that line, or edited the `def`.
#: Measured, as the rule runs pyrefly, in working code: 0.34, 0.06 and 0.03 overrides a thousand lines of httpx,
#: pydantic and google-adk that do not fit a parent pyrefly names, where the ten kinds before it make 0.79, 3.58 and
#: 0.47.
ON_ADDED_LINES = (
    "missing-attribute",
    "missing-argument",
    "bad-argument-count",
    "unexpected-keyword",
    "bad-argument-type",
    "not-callable",
    "bad-return",
    "bad-assignment",
    "not-iterable",
    "unsupported-operation",
    "bad-override",
)
#: Pyrefly's words for a diagnostic of those kinds that is no other file's doing, never named on a line the change
#: added: a method marked `@override` that no parent pyrefly reads holds, and a TypedDict that may hold extra items
#: unpacked into a call, `UNPACKED_EXTRA`, which on a kept line the change's own TypedDict made open. The run reads no
#: installed package, so under a class from one pyrefly says so of every method so marked. Read by the check before the
#: merge, and reproduced: with every override named there, a new tool on an installed base beside an edited helper was a
#: major finding a method, and of google-adk's own source written anew, 10 of the 15 overrides named said these words.
#: The pinned pyrefly's words: a test holds them, and fails where an update says it otherwise.
NOT_ON_ADDED_LINES = ("is marked as an override, but no parent class has a matching attribute", UNPACKED_EXTRA)
#: The kinds named on a line the change added wherever the function called is another file's, whether the change
#: touched it or not: a call's arguments that do not fit its parameters. Read by validation, and reproduced:
#: `total(2, 3.0, 4)` added into a module, `total` in one the change left alone, was named by no rule, `types` reading
#: the import as `Any`. Measured over this repository's last 200 Python commits and google-adk's last 150: nothing
#: named there, and each wrong call planted named. A TypedDict that may hold extra items unpacked into a call is left
#: out by its words, above: measured on litellm, it runs.
CALLED_ACROSS = ("missing-argument", "bad-argument-count", "unexpected-keyword")
#: What a call the change added that does not fit its function's parameters needs, and why.
CALL_FIX = (
    "Change the call to fit the parameters of the function it calls. Or change those parameters, and every caller."
)
CALL_WHY = "Python raises a TypeError at this call when the code runs."


class RadiusTypes(TreeRule):
    """Pyrefly runs over the head's Python files laid out as the head holds them, and over the same files as the base
    held them, each changed one as git holds it there, with the reviewer's configuration: no installed package, no
    ignore comment honoured. A diagnostic is the change's where the head holds it and the base did not, told apart by
    its file and kind and the base's own line for it, as the diff numbers a line the change kept; and, in a file the
    change edited, where a line may have moved, by the line's text for what that leaves. One on a line the change
    added is named where it is a call's, an attribute's or an override's that does not fit a parent pyrefly names,
    caused by another file the change touched, or a call's arguments that do not fit a function another file of the
    tree defines, touched or not, which `types`, reading the file alone, cannot see across files; and a test's file is
    not read. Measured over ADK,
    31 method edits: 24 named, at 59 call sites and 4 kept lines of the edited file, none wrongly; over this
    repository's last 195 commits, none wrongly, and one true break named, a value that may be None passed where a
    string is taken, which the next commit fixed; and on litellm, a parameter added first in `get_secret` shifted 11
    calls' arguments that `callers` reads as fitting. Known limits: code without types reads as `Any`, and a use of it
    is not checked; a name an installed package holds reads as `Any` too; a tree the head's read did not hold whole is
    a floor; code loaded by name, `importlib` or `getattr`, is invisible to it; a call to an API the Python the reviewer
    runs on lacks, newer or removed, reads as a missing attribute, as in `types`; a file is in neither run where its
    path holds a name of other characters than letters, digits, `_`, `-`, a dot and a space, as one with a `~`, which
    one system may read otherwise than another; in a file the change edited, an error that left one line as another
    of the same text broke reads as that line moved; and a method marked `@override` whose parent's method another
    file's change removed is not named where the change added its line or its mark."""

    id = "radius-types"
    severity = "major"
    #: Measured over this repository's history: 34 of 37 false findings were tests, a dict that fills part of a
    #: `TypedDict` and a fake typed loosely, which a project rarely type-checks; a test the change breaks when it runs
    #: fails in the project's own test run.
    reads_tests = False
    title = "code the change left alone no longer type-checks"
    fix = "Change the code to fit what the change made, or keep what that code relied on."
    #: True of both kinds of hit, a line the change left alone and an added one another file's change breaks: read by
    #: review, one sentence a hit, picked by whether its line was added, patched a reason that held for one kind.
    why = "Code now gets a value of another type than it expects, so it fails or misbehaves when it runs."
    tool = "pyrefly-radius"
    # A test's file is in neither of pyrefly's runs, as the rule reads none of its findings: measured on ADK, half its
    # bytes tests, a run took 0.96 s where it took 1.59, the same findings.
    tool_settings = MappingProxyType(
        {
            "ignored": ("missing-import", "parse-error"),
            "added_kinds": ON_ADDED_LINES,
            "added_unless": NOT_ON_ADDED_LINES,
            "called": CALLED_ACROSS,
            "tests": False,
        }
    )

    def needs_head(self, files: list[Entry]) -> bool:
        """A Python file the change touched: what depends on one it modified, moved or removed is in the head alone,
        and what one it added calls too. Not a test's, whose findings the rule reads none of and its runs leave out:
        read by review, on ADK a commit that edited one test read and parsed the whole project for nothing. A module
        moved into the tests was a source's: read by an audit, and reproduced, its callers were read by no run. Nor
        was a file renamed to Python's suffix, asked of its old path alone."""
        return any(
            (item["path"].endswith(".py") or item.get("old", "").endswith(".py"))
            and not (is_test(item["path"]) and is_test(item.get("old", item["path"])))
            for item in files
        )

    def check(self, tree: Tree) -> list[Hit]:
        hits = []
        for path, found in sorted(tree.diagnostics.items()):
            if not self.reads(path):
                continue
            # At most `PER_FILE` listed, the rest counted in one finding, as `types` lists pyrefly's.
            shown = sorted(found[:PER_FILE], key=lambda item: item.line)
            added = tree.added.get(path, frozenset())
            # A diagnostic's whole range, as the rule weighs it: read by review, a call over several lines whose
            # argument line the change edited was given a kept line's fix on its first.
            hits += [
                self._listed(path, item, not added.isdisjoint(range(item.line, (item.last or item.line) + 1)))
                for item in shown
            ]
            if rest := found[PER_FILE:]:
                hits.append(_counted(path, rest))
        return hits

    @staticmethod
    def _listed(path: str, item: Diagnostic, added: bool) -> Hit:
        """One of pyrefly's diagnostics as a finding: a call's on a line the change added with a call's own fix and
        why, since that call is the change's to correct. Read by review: it carried the rule's, which tell the person
        to fit the code to what the change made, where the change's own call is what does not fit."""
        title = f"{item.code} {item.message}"
        if added and item.code in CALLED_ACROSS:
            return Hit(path, item.line, item.source, title=title, fix=CALL_FIX, why=CALL_WHY)
        return Hit(path, item.line, item.source, title=title)


def _counted(path: str, rest: Sequence[Diagnostic]) -> Hit:
    kinds = Counter(item.code for item in rest)
    return Hit(
        path,
        None,
        ", ".join(f"{kind} x{count}" for kind, count in kinds.most_common()),
        title=f"{len(rest)} more of pyrefly's findings in this file, not listed",
        fix="Run `pyrefly check` on the project to see each one. Its own settings and ignore comments can hide some.",
    )

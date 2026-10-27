"""What a change breaks where no diff shows it, as pyrefly reads the head's tree against the base's: a method, an
attribute, a parameter or a type the change altered, in a file it left alone or on a line of a changed file it kept.
`types` reads each changed file alone, a name it imports read as `Any`, and `callers` reads no types, so a call through
an object, `repo.get_user(3)`, is read here or nowhere.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from types import MappingProxyType

from ..rule_helpers import PER_FILE, Diagnostic, Entry, Hit, Tree, TreeRule


class RadiusTypes(TreeRule):
    """Pyrefly runs over the head's Python files laid out as the head holds them, and over the same files as the base
    held them, each changed one rebuilt from its diff, with the reviewer's configuration: no installed package, no
    ignore comment honoured. A diagnostic is the change's where the head holds it and the base did not, told apart by
    its file, kind and line's text; one on a line the change added is the `types` rule's, and a test's file is not
    read. Measured over ADK, 31 method edits: 24 named, at 59 call sites and 4 kept lines of the edited file, none
    wrongly; over this repository's last 195 commits, the package's own each type-checked by its CI, none named; and
    on litellm, a parameter added first in `get_secret` shifted 11 calls' arguments that `callers` reads as fitting.
    Known limits: code without types reads as `Any`, and a use of it is not checked; a name an installed package holds
    reads as `Any` too; a tree the head's read did not hold whole is a floor; and code loaded by name, `importlib` or
    `getattr`, is invisible to it."""

    id = "radius-types"
    severity = "major"
    #: Measured over this repository's history: 34 of 37 false findings were tests, a dict that fills part of a
    #: `TypedDict` and a fake typed loosely, which a project rarely type-checks; a test the change breaks when it runs
    #: fails in the project's own test run.
    reads_tests = False
    title = "code the change left alone no longer type-checks"
    fix = "Change the code to fit what the change made, or keep what that code relied on."
    tool = "pyrefly-radius"
    tool_settings = MappingProxyType({"ignored": ("missing-import", "parse-error")})

    def needs_head(self, files: list[Entry]) -> bool:
        """A Python file the change modified, moved or removed: what depends on it is in the head alone."""
        return any(
            item["status"] in ("M", "T", "R", "D") and item.get("old", item["path"]).endswith(".py") for item in files
        )

    def check(self, tree: Tree) -> list[Hit]:
        hits = []
        for path, found in sorted(tree.diagnostics.items()):
            if not self.reads(path):
                continue
            # At most `PER_FILE` listed, the rest counted in one finding, as `types` lists pyrefly's.
            shown = sorted(found[:PER_FILE], key=lambda item: item.line)
            hits += [Hit(path, item.line, item.source, title=f"{item.code} {item.message}") for item in shown]
            if rest := found[PER_FILE:]:
                hits.append(_counted(path, rest))
        return hits


def _counted(path: str, rest: Sequence[Diagnostic]) -> Hit:
    kinds = Counter(item.code for item in rest)
    return Hit(
        path,
        None,
        ", ".join(f"{kind} x{count}" for kind, count in kinds.most_common()),
        title=f"{len(rest)} more of pyrefly's findings in this file, not listed",
        fix="Run pyrefly over the project to see each one.",
    )

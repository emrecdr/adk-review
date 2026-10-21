"""A call or a value its own types say is wrong, as pyrefly reads a changed file alone: a call missing an argument or
given one too many, an attribute a class does not hold, a value of the wrong type passed or returned, one that cannot be
iterated or added. Ruff, which `lint` runs, reads no types: of 30 such defects planted in four libraries and this
repository, it found the 3 that were also undefined names, and pyrefly 27, each on its own line.

Only what pyrefly finds on the lines the change added is a finding, of the kinds weighed below; a name a file imports
from beyond itself reads as `Any`, so a module pyrefly cannot find is the layout's, never the change's. Measured over
1.12 million lines of four libraries' correct code, google-adk, pydantic, httpx and litellm, no ignore comment
honoured: these kinds made 0.53 false alarms a thousand lines, and every other kind more, a missing module or a star
import among them. A call's arguments are the surest, 11 errors in those lines, each read, so they are major; the rest
minor. Not read, as known limits: a defect the change causes on a line it kept, a caller of a function whose signature
it changed among them; and code under a platform or a version pyrefly does not take, as Linux and Python 3.13, or
under `if not TYPE_CHECKING:`.
"""

from __future__ import annotations

from collections import Counter
from types import MappingProxyType

from ..rule_helpers import PER_FILE, RANK, ChangedFile, Diagnostic, FileRule, Hit, Severity, is_python

#: The kinds pyrefly names that this rule reads, each at its severity: the rest are not read.
SEVERITY: dict[str, Severity] = {
    "bad-argument-count": "major",
    "missing-argument": "major",
    "missing-attribute": "minor",
    "bad-argument-type": "minor",
    "bad-return": "minor",
    "not-iterable": "minor",
    "unsupported-operation": "minor",
}


class Types(FileRule):
    """A type checker finds what a call or a value promises and breaks, which a model can only guess at."""

    id = "types"
    severity = "minor"  # a kind the table above does not name, which this rule never reads
    title = "a call or a value its own types say is wrong"
    fix = "Make the call or the value match the type its definition or annotation names."
    tool = "pyrefly"
    tool_settings = MappingProxyType({"kinds": tuple(SEVERITY)})

    def check(self, file: ChangedFile) -> list[Hit]:
        if not is_python(file.path, lambda: file.line(1)):
            return []  # pyrefly reads Python, and another file's text is never asked for
        found = [item for item in file.diagnostics if item.line in file.added and item.code in SEVERITY]
        # At most `PER_FILE` listed, the most severe first, the rest counted in one finding at the most severe of
        # them, as `lint` lists ruff's. Read by review: one file of `1 + ""` lines printed 125,000 findings.
        ranked = sorted(found, key=lambda item: -RANK[SEVERITY[item.code]])  # stable: by line
        shown = sorted(ranked[:PER_FILE], key=lambda item: item.line)
        hits = [self._listed(file.path, item) for item in shown]
        if rest := ranked[PER_FILE:]:
            hits.append(self._counted(file.path, rest))
        return hits

    def _listed(self, path: str, item: Diagnostic) -> Hit:
        return Hit(
            path,
            item.line,
            item.source,
            title=f"{item.code} {item.message}",
            severity=SEVERITY[item.code],
            fix=self.fix,
        )

    @staticmethod
    def _counted(path: str, rest: list[Diagnostic]) -> Hit:
        kinds = Counter(item.code for item in rest)
        return Hit(
            path,
            None,
            ", ".join(f"{kind} x{count}" for kind, count in kinds.most_common()),
            title=f"{len(rest)} more of pyrefly's findings in this file, not listed",
            severity=max((SEVERITY[item.code] for item in rest), key=RANK.__getitem__),
            fix="Run pyrefly on the file to see each one.",
        )

"""A call or a value its own types say is wrong, as pyrefly reads a changed file alone: a call missing an argument or
given one too many, an attribute a class does not hold, a value of the wrong type passed or returned, one that cannot be
iterated or added. Ruff, which `lint` runs, reads no types: of 30 such defects planted in four libraries and this
repository, it found the 3 that were also undefined names, and pyrefly 27, each on its own line.

Only what pyrefly finds on the lines the change added is a finding, of the kinds weighed below; a name a file imports
from beyond itself reads as `Any`, so a module pyrefly cannot find is the layout's, never the change's. Measured over
1.12 million lines of four libraries' correct code, google-adk, pydantic, httpx and litellm: these kinds made 0.47
false alarms a thousand lines, and every other kind more, a missing module or a star import among them. A call's
arguments are the surest: 8 errors in those 1.12 million lines, each read, so they are major; the rest minor.
"""

from __future__ import annotations

from types import MappingProxyType

from ..rule_helpers import ChangedFile, FileRule, Hit, Severity, is_python

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
    severity = "minor"  # a kind the table above does not name, which pyrefly is never asked for
    title = "a call or a value its own types say is wrong"
    fix = "Make the call or the value match the type its definition or annotation names."
    tool = "pyrefly"
    tool_settings = MappingProxyType({"kinds": tuple(SEVERITY)})

    def check(self, file: ChangedFile) -> list[Hit]:
        if not is_python(file.path, lambda: file.line(1)):
            return []  # pyrefly reads Python, and another file's text is never asked for
        return [
            Hit(
                file.path,
                found.line,
                found.source,
                title=f"{found.code} {found.message}",
                severity=SEVERITY[found.code],
                fix=self.fix,
            )
            for found in file.diagnostics
            if found.line in file.added and found.code in SEVERITY
        ]

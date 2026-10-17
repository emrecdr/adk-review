"""A rule tried with no repository: on a text, on a few files, on its own examples and on its own source.

Every helper here reads a rule through `hits`, as a review reads it, so a test sees what the report would. Each file
is read as a change that adds every line of it, and together the files are the head: a line rule, a file rule and a
tree rule are tried the same way.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Mapping
from pathlib import Path

from ...core.evidence import Entry
from ...core.rules import AnyRule, Example, Hit, Tree, TreeRule, UnreadError
from ...core.text import lines_of

#: Where an example given as a text alone is read: a Python file that is no test.
EXAMPLE_PATH = "example.py"


def as_change(files: Mapping[str, str]) -> tuple[list[Entry], Tree]:
    """`files`, each a path and its text, as the collector would hand them to the rules: a change that adds every
    line of every file, each entry whole as `core/evidence.py` spells one, and the head that holds these files and
    nothing else."""
    entries: list[Entry] = []
    for path, text in files.items():
        added = list(enumerate(lines_of(text), 1))
        at = frozenset(number for number, _line in added)
        entries.append(
            {
                "path": path,
                "status": "A",
                "diff": None,
                "added": added,
                "added_at": at,
                "quotable": [],
                "framing": [],
                "unread": "",
                "cut": False,
                "text": text,
                "unread_text": None,
            }
        )
    return entries, Tree(files, {path: text for path, text in files.items() if path.endswith(".py")})


def try_rule(rule: AnyRule, files: Mapping[str, str]) -> list[Hit]:
    """What `rule` finds in `files`, each a path and its text: how a rule of any kind is tried on a snippet, and what
    `self_test` holds a rule's examples to. `UnreadError` where the rule says it could not look, as one that finds
    code Python would run unread says."""
    return rule.hits(*as_change(files))


def lines_hit(rule: AnyRule, path: str, text: str) -> list[int]:
    """The lines of `text`, numbered from 1, that `rule` finds with all of it added to `path`: how a rule's author
    tries a rule on a file's worth of examples."""
    return sorted({hit.line for hit in try_rule(rule, {path: text}) if hit.path == path and hit.line is not None})


def self_check(rule: AnyRule) -> list[int]:
    """The lines of the rule's own source its check fires on, read as added where the catalog reads it: a rule that
    reads its own words is a rule misread, so a rule's test asserts this is empty. Its examples, the lines of `bad`
    and `good`, are read as blank lines: they are words on purpose. Blanked, not skipped: measured, a rule that reads
    a sentence over three lines found its bad example from the line two above it."""
    source = _source(rule)
    examples = _example_lines("\n".join(source), type(rule).__name__)
    kept = ["" if number in examples else line for number, line in enumerate(source, 1)]
    return lines_hit(rule, _own_path(rule), "\n".join(kept) + "\n")


def _source(rule: AnyRule) -> list[str]:
    """The lines of the rule's own file."""
    return lines_of(Path(inspect.getsourcefile(type(rule)) or "").read_text(encoding="utf-8"))


def _own_path(rule: AnyRule) -> str:
    """Where a rule's own source is read: at its first bad example's path when that is a Python file, as a rule that
    reads tests alone needs. Measured: read as a `Dockerfile`, a rule's own `from … import` lines were FROM
    instructions."""
    first = next(iter(files_of(rule.bad[0]))) if rule.bad else EXAMPLE_PATH
    return first if first.endswith(".py") else EXAMPLE_PATH


def self_test(rule: AnyRule) -> list[str]:
    """What a rule's own examples and its own source say against it, a sentence each; empty when the rule does what
    it says: each example under `bad` is flagged, none under `good` is, and a line or a file rule fires on nothing in
    its own source. A rule's first test, with no test written: the catalog asks it of every rule it finds."""
    said: list[str] = []
    said.extend(
        f"flags nothing in its bad example {_shown(example)}"
        for example in rule.bad
        if not _flagged(rule, files_of(example))
    )
    for example in rule.good:
        try:
            found = try_rule(rule, files_of(example))
        except UnreadError as exc:
            said.append(f"could not look at its good example {_shown(example)}: {exc}")
            continue
        for hit in found:
            where = hit.path if hit.line is None else f"{hit.path}:{hit.line}"
            said.append(f"flags {where} in its good example {_shown(example)}")
    if not isinstance(rule, TreeRule) and (fired := self_check(rule)):  # a tree rule reads no file's words
        source = _source(rule)  # read again only to quote what fired
        said += [f"fires on line {line} of its own source: {source[line - 1].strip()}" for line in fired]
    return said


def files_of(example: Example) -> dict[str, str]:
    """One example as files: a text alone is read at `EXAMPLE_PATH`, a `(path, text)` pair at its path, and a
    mapping is several files as it stands."""
    if isinstance(example, str):
        return {EXAMPLE_PATH: example}
    if isinstance(example, Mapping):
        return dict(example)
    path, text = example
    return {path: text}


def _flagged(rule: AnyRule, files: Mapping[str, str]) -> bool:
    """Whether `rule` says something of `files`: a hit, or that it could not look, which is what a rule that finds
    code Python would run unread says of it."""
    try:
        return bool(try_rule(rule, files))
    except UnreadError:
        return True


def _shown(example: Example) -> str:
    """An example as a sentence quotes it: the text of its one file, or its paths, cut to a line's length."""
    files = files_of(example)
    said = repr(next(iter(files.values()))) if len(files) == 1 else "{" + ", ".join(files) + "}"
    return said if len(said) <= 80 else said[:77] + "…" + said[-1]


def _example_lines(source: str, name: str) -> set[int]:
    """The lines of a rule's source that hold its examples, by the class's own `bad = …` and `good = …`."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return set()
    return {
        line
        for found in module.body
        if isinstance(found, ast.ClassDef) and found.name == name
        for node in found.body
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and any(
            isinstance(target, ast.Name) and target.id in ("bad", "good")
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
        for line in range(node.lineno, (node.end_lineno or node.lineno) + 1)
    }

"""What a rule is: a check decided by code, one class in one file of `judge/rules/`.

A rule is self-contained: one class that decides its own scope and its own verdict, and names its severity, its
title and its fix. Three kinds, one mental model — a line, a file, the tree. A `Rule` reads each line a change added
and answers yes or no. A `FileRule` reads each file the change touched, whole at the head — its lines, which of them
the change added, the other files the change touched and, for Python, its module, and ruff's diagnostics where it
names the ruff codes it reads — for what one line cannot show. A `TreeRule` reads the head instead of the change:
every path, and each Python module, parsed once with the blast radius, for what must exist, define a name, derive
from a parent or take a parameter. Every rule is found in its folder and run the same way, by `run_rule`, the ones
shipped with the reviewer among them; the repository under review configures nothing, and no code of its runs.

This file is the contract: the kinds, what each is handed, and how one is run. What an author reaches for while
writing `check` — reading a line's tokens or a module's syntax tree, matching a path, trying a rule on a text — is
`judge/rule_helpers/`, which imports this file and nothing that runs. Pure, as the rest of the core is.
"""

from __future__ import annotations

import ast
import copy
import functools
import inspect
import re
import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath
from types import MappingProxyType
from typing import ClassVar

from .blast import is_test, parse, parsed
from .evidence import Entry, unreleased
from .findings import Checked, Finding, Severity
from .paths import matches
from .profiles import rule_id
from .secrets import CREDENTIAL_REACH, scrub
from .text import EVIDENCE_MAX_CHARS, cut_at, lines_of

#: One example a rule carries: a text, read at `example.py`; a `(path, text)` pair, for a rule that chooses its files;
#: or `{path: text}`, several files at once, for a rule that reads the change or the tree as a whole.
Example = str | tuple[str, str] | Mapping[str, str]


@dataclass(frozen=True)
class Hit:
    """One place a rule found: the path, the line where it knows one, and the text the report quotes as evidence.
    Where one rule covers several problems, as `lint` covers ruff's codes, a hit names its own title, severity or
    fix; every other rule leaves them to the rule's own."""

    path: str
    line: int | None
    text: str
    title: str | None = None
    severity: Severity | None = None
    fix: str | None = None


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One thing ruff reports on a file: the line, as ruff counts lines, and that line's text; the code, the message,
    and the fix ruff offers, if any, with ruff's own label for it: `safe`, `unsafe` or `display-only`."""

    line: int
    source: str
    code: str
    message: str
    fix: str | None = None
    applicability: str | None = None


class AnyRule(ABC):
    """What every rule names, whatever it reads: its id, its severity, its title and its fix, each a sentence the
    report prints; and how it finds its hits. A rule's author writes only `check`."""

    id: ClassVar[str]
    severity: ClassVar[Severity]
    title: ClassVar[str]
    fix: ClassVar[str]
    #: What the rule reads, as `--list-checks` names it: a line, a file, the tree. Each kind below names its own, and
    #: the catalog takes a rule of none of the three kinds for no rule.
    kind: ClassVar[str]
    #: The rule's own examples, its first test and its documentation: each `bad` one it flags, each `good` one it
    #: leaves alone. The catalog holds a rule to them wherever the rule is found.
    bad: ClassVar[tuple[Example, ...]] = ()
    good: ClassVar[tuple[Example, ...]] = ()
    #: Whether the rule reads tests, by `is_test`'s conventions. A rule about the product's code reads none, so the
    #: tests' deliberate fakes are no findings: measured, demo-derives-from flagged `TestInspectRepository` and
    #: `FakeModel`. A rule about what runs, what leaks or what steers says True, since a test runs in CI with its keys
    #: and a file named `test_*` is a test whatever it holds, a workflow among them. Only a test's text is withheld: a
    #: file rule's `file.changed` and a tree rule's `tree.paths` still list every test.
    reads_tests: ClassVar[bool] = False
    #: Whether the rule reads the project's own files `scan` leaves out. Only a credential rule does: a key leaks from
    #: a README as from code. Another project's code reaches no rule.
    reads_every_kind: ClassVar[bool] = False
    #: Whether its findings change how code reads and never what it does, as a simplify pass's do: a handoff lists them
    #: apart, under what a tidy keeps, and holds one back where a defect at major or above names the same lines.
    cleanup: ClassVar[bool] = False

    def reads(self, path: str) -> bool:
        """Whether the rule is handed what `path` holds: any file, for a rule that reads tests; else one no test."""
        return self.reads_tests or not is_test(path)

    @property
    def check_id(self) -> str:
        """The check's id, as `config.toml`, the flags and the report spell it: `rule.no-print`."""
        return rule_id(self.id)

    @abstractmethod
    def hits(self, files: list[Entry], tree: Tree | None) -> list[Hit]:
        """Each place the rule finds: on an added line, numbered from git's own hunk headers; in a changed file, at
        the line it names; or over the head, at a line where the rule knows one and else at the path. `UnreadError`
        where it could not look at what it asked for."""


def added_to(entry: Entry) -> list[tuple[int, str]]:
    """The lines a change added to `entry`, numbered: those its diff adds, or every line of a symbolic link's text,
    which is the file the link names and new under the link's name, though its diff adds one line, that file's path.
    Measured by review: the shell call of `run.py`, a link to an image holding Python, was counted as a line the change
    did not add."""
    if entry.get("link") and entry["text"] is not None:
        return list(enumerate(lines_of(entry["text"]), 1))
    return unreleased(entry["added"])


class Rule(AnyRule):
    """One check over the lines a change added: it decides its own scope, and its own verdict on a line."""

    kind = "line"

    @abstractmethod
    def check(self, path: str, line: str) -> bool:
        """True when `line`, added to `path`, breaks the rule."""

    def hits(self, files: list[Entry], tree: Tree | None) -> list[Hit]:  # noqa: ARG002 -- a line rule reads no tree
        read = [entry for entry in files if self.reads(entry["path"])]
        found = [
            Hit(entry["path"], line, f"+{text}")
            for entry in read
            for line, text in added_to(entry)
            if self.check(entry["path"], text)
        ]
        # A link's lines are the file it names: one not read, or not followed, shows only its path, so the rule could
        # not look.
        # Read by review: a key in a blob a partial clone lacked was approved, named nowhere, and kept in the cache.
        holes = [(entry["path"], entry["unread_text"]) for entry in read if entry.get("link") and entry["unread_text"]]
        return _answered(found, holes)


#: What Python reads as source by its name; a file with no suffix is a script when its first line runs Python.
PYTHON_SUFFIXES = (".py", ".pyw", ".pyi")
#: A first line that runs Python: the interpreter, PyPy, or a runner of a script's own, `uv run` or `pipx run`. Measured
#: by review: a script run through `uv run --script` or PyPy went unlinted, its `shell=True` with it.
_RUNS_PYTHON = re.compile(r"#!.*(?:\b(?:python|pypy)|\b(?:uv|pipx)\s+run\b)")


def is_python(path: str, first_line: Callable[[], str]) -> bool:
    """Whether Python reads `path` as source: by its suffix, or, for a file with none, a first line that runs Python.
    `first_line` is asked only for a file with no suffix, so a file not read whole is asked for its text only then.
    Measured before: `shell=True` in a `.pyw` file or a script named `manage` went unlinted."""
    suffix = PurePosixPath(path).suffix
    return suffix in PYTHON_SUFFIXES if suffix else bool(_RUNS_PYTHON.match(first_line()))


def is_python_text(path: str, text: str | None) -> bool:
    """`is_python` for a file whose text is in hand, or None where it was not read: its first line, or none."""
    return is_python(path, lambda: (text or "").partition("\n")[0])


class ChangedFile:
    """One file a change touched, as a file rule reads it: its path, its text at the head as lines numbered from 1 as
    git numbers them, the numbers of the lines the change added, the path of every file the change touched and, for a
    Python file, its module — the tree's parse where the head was listed, or why the tree refused it, the file's own
    otherwise — and, for a rule that names the ruff codes it reads, ruff's diagnostics.

    A file the head holds that the collector did not read whole is handed over all the same, with no text: asking for
    its lines, its module or ruff's diagnostics raises `UnreadError`, so the rule that asks is the one that could not
    look, and a rule that never asks — lint beside a lock file — loses nothing. Measured before: one unread lock file
    made lint a hole and dropped every finding it had made on the other files. The miss is also recorded on the file,
    so a rule that catches every exception still answers as one that could not look. A rule that reads the lines a
    change added and those around them asks `near` instead, which reads such a file as far as its diff shows it."""

    def __init__(
        self,
        path: str,
        *,
        text: str | None,
        added: Iterable[int],
        module: ast.Module | str | None,
        diagnostics: Iterable[Diagnostic],
        unread: str,
        changed: frozenset[str],
        shown: Mapping[int, str] = MappingProxyType({}),
        old: str | None = None,
    ) -> None:
        self.path = path
        self.added = frozenset(added)
        #: A renamed or copied file's former path, under git's R and C alone.
        self.old = old
        #: Every path the change touched, this file's among them: what a rule about the change as a whole asks — a
        #: source file changed and no test with it, a manifest changed and its lock file not. One set for every file
        #: of a change, never copied: measured, a copy each made a file rule cost 1.6 s over 10,000 files.
        self.changed = changed
        self._text = text
        self._module = module
        self._diagnostics = tuple(diagnostics)
        self._unread = unread
        #: What the diff shows of a file not read whole, by line number: the lines the change added, and those kept
        #: around them.
        self._shown = shown
        #: Whether the rule asked for the text of a file that was not read.
        self.missed = False

    def arrived(self, *globs: str) -> bool:
        """Whether this file came into the paths `globs` name by a rename or a copy from outside them: every line of it
        is new there, though the diff adds none. Measured by review: a workflow moved into `.github/workflows/` and a
        lockfile renamed into `uv.lock`, each adding no line, were read by no rule that reads what a change adds. A
        link needs no asking: every line of its text is one the change added."""
        return self.old is not None and not matches(self.old, *globs)

    def _text_at_head(self) -> str:
        """The file's text, or `UnreadError` for one the collector did not read whole: a hole, never an empty file."""
        if self._text is None:
            self.missed = True
            raise UnreadError(f"{self.path} {self._unread}")
        return self._text

    @functools.cached_property
    def lines(self) -> tuple[str, ...]:
        """The file's lines at the head, as git numbers them."""
        return tuple(lines_of(self._text_at_head()))

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """The tool's diagnostics on the file, for a rule that names a tool: none for any other rule."""
        self._text_at_head()  # a tool reads what was read: an unread file has no diagnostics, and no silence either
        return self._diagnostics

    def line(self, number: int) -> str:
        """Line `number`, counted from 1 as git and the report count; empty past the end."""
        return self.lines[number - 1] if 0 < number <= len(self.lines) else ""

    def near(self, number: int) -> str | None:
        """Line `number`, counted from 1, where it can be read: the file's own; or, of a file too large to read whole,
        the line its diff shows, one the change added or one kept beside it. None past the end, or past what the diff
        shows. What a rule asks that reads the lines a change added and those around them, never the whole file, so a
        file of any size is read and none is a hole: measured, a lock file past the cap made such a rule a hole, and a
        push that changed it stopped."""
        if self._text is not None:
            return self.lines[number - 1] if 0 < number <= len(self.lines) else None
        if number in self._shown:
            return self._shown[number]
        if number in self.added:  # a diff shows every line its change added: one it does not, no one read
            self._text_at_head()
        return None

    @functools.cached_property
    def module(self) -> ast.Module | str:
        """The file as Python reads it, or why there is no module: not a Python file, or it does not parse."""
        if self._module is not None:
            return self._module
        # Python as lint reads it, by its suffix or a first line that runs Python: measured before, `.pyw` files and
        # scripts named like `manage` read as no module. A file with no suffix not read is none, as before.
        if not is_python_text(self.path, self._text):
            return "not read as Python"
        return parse("\n".join(self.lines)) or "does not parse"  # refused as the tree's parser refuses one


def _each(found: object, what: str, returns: str) -> Iterable:
    """What a `check` returned, when it can be read one hit at a time; else a sentence saying what a check of its
    kind returns. Measured before: a `check` that forgot its `return` ended as "'NoneType' object is not iterable"."""
    if isinstance(found, str | bytes | Mapping) or not isinstance(found, Iterable):
        raise TypeError(f"check({what}) returned {found!r}: it returns {returns}")
    return found


def _whole(line: object) -> bool:
    """A line number, or None for no line: a bool is neither."""
    return line is None or type(line) is int


#: The outside tools a rule may name, each run by its adapter in `collect/tools.py`: named here, in the pure core, so
#: the catalog refuses a name no adapter runs without loading what runs one. A test holds the two to each other.
TOOLS = frozenset({"ruff", "pyrefly"})


class FileRule(AnyRule):
    """One check over each file a change touched, read whole at the head: its lines, which of them the change added
    and, for Python, its module — for what one added line cannot show: a sentence wrapped over several lines, a
    decorator and its definition, a function a change grew past a length."""

    kind = "file"
    #: The outside tool the rule reads, by the name of its adapter in `collect/tools.py` — `ruff` — and the settings
    #: that adapter takes, the rule's own and never the repository's: a rule that names one is given each changed file
    #: with the tool's diagnostics on `file.diagnostics`. None for every rule but those that read ruff.
    tool: ClassVar[str | None] = None
    tool_settings: ClassVar[Mapping[str, object]] = MappingProxyType({})

    @abstractmethod
    def check(self, file: ChangedFile) -> Iterable[int | tuple[int | None, str] | Hit]:
        """Each line of `file` that breaks the rule, numbered from 1 in the file at the head: the number alone, and
        the report quotes that line; `(line, what is wrong)` to say more, None for the file as a whole; or a `Hit`
        where one needs its own title, severity or fix. Empty where the rule holds."""

    def hits(self, files: list[Entry], tree: Tree | None) -> list[Hit]:
        changed = frozenset(entry["path"] for entry in files)
        returns = "a list of line numbers, (line, what is wrong) pairs or Hits"
        found: list[Hit] = []
        missed: list[tuple[str, str]] = []
        for entry in files:
            if entry["text"] is None and not entry["unread_text"]:
                continue  # deleted, binary, a link, a submodule: nothing at the head for a file rule to read
            path = entry["path"]
            if not self.reads(path):
                continue  # a test, to a rule that reads none: still in `changed`, as every path the change touched
            module = tree.module(path) if tree is not None and path in tree.parsed else None  # the tree's one parse
            added = (line for line, _text in added_to(entry))
            # Ruff's diagnostics ride on an entry only for the rule that named its codes; every other rule gets none.
            unread = entry["unread_text"] or ""
            diagnostics, shown = entry.get("diagnostics", ()), entry.get("shown") or {}
            file = ChangedFile(
                path,
                text=entry["text"],
                added=added,
                module=module,
                diagnostics=diagnostics,
                unread=unread,
                changed=changed,
                shown=shown,
                old=entry.get("old"),
            )
            try:
                found += [self._hit(file, item) for item in _each(self.check(file), "file", returns)]
            except UnreadError:
                if not file.missed:  # not this file's text: let the rule's run name it
                    raise
            if file.missed:  # the head holds it, the rule asked for it, and it was not read: a hole, never silence
                missed.append((path, unread))
        return _answered(found, missed)

    @staticmethod
    def _hit(file: ChangedFile, item: object) -> Hit:
        """One thing `check` returned, as a hit: a line number, quoted as the diff shows that line; a `(line, what
        is wrong)` pair; or a `Hit`. Anything else is a sentence. Measured before: a list of messages ended as
        "Hit.__init__() takes from 4 to 7 positional arguments but 25 were given"."""
        if isinstance(item, Hit):
            return item
        if type(item) is int:
            line, what = item, None
        elif isinstance(item, tuple) and len(item) == 2 and _whole(item[0]) and isinstance(item[1], str):
            line, what = item
            if line is None:
                return Hit(file.path, None, what)
        else:
            raise TypeError(
                f"check(file) returned {item!r} among its hits. Each hit is a line number, "
                "a (line, what) pair, or a Hit"
            )
        # A line the rule can see: its file's, or one its diff shows of a file not read whole, where a line it shows
        # none of is a hole, through `lines`, as any read of that file is.
        if (seen := file.near(line)) is None:
            raise ValueError(f"check(file) names line {line} of {file.path}, which has {len(file.lines)} line(s)")
        # A line number alone is quoted as the diff shows that line: `+` where the change added it, a space where not.
        return Hit(file.path, line, what if what is not None else ("+" if line in file.added else " ") + seen)


def raised_line(exc: BaseException, owns: Callable[[str], bool]) -> int | None:
    """The last line, in `exc`'s traceback, of a file `owns` names: where a check's own file raised, for its author to
    read, whether the catalog loading it or a run of it caught the error."""
    own = [frame.lineno for frame in traceback.extract_tb(exc.__traceback__) if owns(frame.filename)]
    return own[-1] if own else None


def _raised_at(rule: AnyRule, exc: BaseException) -> str:
    """Where the rule's own file raised, as ` (no_print.py line 23)`, or nothing where no line of it did: a rule's
    author is told which line to read. Measured before: "raised ZeroDivisionError: division by zero" and no more."""
    try:
        source = inspect.getsourcefile(type(rule))
    except (TypeError, OSError):
        source = None
    line = raised_line(exc, lambda name: name == source)
    return f" ({PurePath(source).name} line {line})" if line and source else ""


def run_rule(rule: AnyRule, files: list[Entry], tree: Tree | None) -> Checked:
    """One rule over what it reads, a line rule over the lines a change added and a tree rule over the head: its
    findings, each named at its line or its path, or why it could not look — it raised, the tree was not listed, a
    file the head holds was not read whole, a hit it named badly, with a severity off the scale say. A rule that could
    not look is never read as one that found nothing, and one rule's failure is no other rule's. What a file rule found
    on the files it could read stands beside the files it could not."""
    could_not_look: str | None = None
    try:
        try:
            hits = rule.hits(files, tree)
        except UnreadError as exc:
            hits, could_not_look = exc.found, f"rule {rule.id} could not look: {exc}"
        read = EVIDENCE_MAX_CHARS + CREDENTIAL_REACH
        findings = [
            Finding(
                file=hit.path,
                line=hit.line,
                severity=hit.severity or rule.severity,
                title=f"{rule.id}: {hit.title or rule.title}",
                # The report never prints a secret, a rule's evidence included, and the line is cut once, after a
                # scrub reading as far as a credential crossing the cut could run: measured by review, a 10 MB line
                # was scrubbed again by every later reader, 160 s in all, and one 2 MB line whole took 9 s.
                evidence=cut_at(scrub(hit.text[:read], cut=len(hit.text) > read)[0], EVIDENCE_MAX_CHARS),
                suggestion=hit.fix or rule.fix,
            )
            for hit in hits
        ]
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- the project's code: a failure, an exit too, is named
        return Checked(could_not_look=f"rule {rule.id} raised {type(exc).__name__}: {exc}{_raised_at(rule, exc)}")
    return Checked(findings=findings, could_not_look=could_not_look)


class UnreadError(LookupError):
    """Why a rule could not look: a file the head holds that was not read whole, as one past the size cap is, or a
    tree that was not listed. A hole, never a finding and never silence. `found` carries what the rule found on what
    it could read, which stands beside the hole."""

    def __init__(self, why: str, found: Iterable[Hit] = ()) -> None:
        super().__init__(why)
        self.found = tuple(found)


def _answered(found: list[Hit], missed: Sequence[tuple[str, str]]) -> list[Hit]:
    """What a rule found, or `UnreadError` naming each `(path, why)` of `missed` as "path why", "; " between them:
    `push._stops` splits a merge's holes there, so the line and file rules spell them alike."""
    if missed:
        raise UnreadError("; ".join(f"{path} {why}" for path, why in missed), found)
    return found


class Tree:
    """The tree at the head, as a tree rule reads it: every path, each Python file read whole parsed once, for the
    blast radius and the rules alike, and the Python files not read whole."""

    def __init__(self, paths: Iterable[str], sources: Mapping[str, str], unread: Iterable[str] = ()) -> None:
        self.paths = frozenset(paths)
        self.unread = frozenset(unread)
        self.parsed = parsed(sources)

    @functools.cached_property
    def without_tests(self) -> Tree:
        """The same head with no test's module, for a tree rule that reads no tests: every path still listed, so a
        rule asking whether `tests/` exists is answered, and a test not read whole no hole of its. Made once a tree,
        whichever rules ask, and nothing parsed again."""
        view = copy.copy(self)
        view.parsed = {path: module for path, module in self.parsed.items() if not is_test(path)}
        view.unread = frozenset(path for path in self.unread if not is_test(path))
        return view

    def has(self, path: str) -> bool:
        """A file by its path, or a folder by its path ending in `/`."""
        if path.endswith("/"):
            return any(p.startswith(path) for p in self.paths)
        return path in self.paths

    def module(self, path: str) -> ast.Module | str:
        """`path` as Python reads it, or why there is no module: the head holds no such file, holds it as no Python
        file, or it does not parse. `UnreadError` where the head holds it and it was not read whole."""
        if path in self.unread:
            raise UnreadError(f"{path} was not read whole")
        if path not in self.paths:
            return "not at the head"
        if path not in self.parsed:
            return "not read as Python"
        return self.parsed[path] or "does not parse"

    def modules(self) -> Iterator[tuple[str, ast.Module]]:
        """Every Python module read at the head that parses, by path. A file not read whole is not here: the report's
        blast radius says how many, so a rule over the whole tree is read as a floor, never as silence."""
        for path in sorted(self.parsed):
            if (module := self.parsed[path]) is not None:
                yield path, module


class TreeRule(AnyRule):
    """One check over the tree at the head: what must exist, define a name, derive from a parent or take a
    parameter. Its expectations are its own constants, as a line rule's are."""

    kind = "tree"

    @abstractmethod
    def check(self, tree: Tree) -> Iterable[tuple[str, str] | tuple[str, int | None, str] | Hit]:
        """Each `(path, what is wrong)` the head breaks — or `(path, line, what)` where the rule knows the line, as one
        over a module's syntax tree does, or a `Hit`; empty where it holds."""

    def hits(self, files: list[Entry], tree: Tree | None) -> list[Hit]:  # noqa: ARG002 -- a tree rule reads no lines
        if tree is None:
            raise UnreadError("the tree at the head was not listed")
        returns = "a list of (path, what is wrong) pairs, (path, line, what) triples or Hits"
        read = tree if self.reads_tests else tree.without_tests
        return [self._hit(item) for item in _each(self.check(read), "tree", returns)]

    @staticmethod
    def _hit(item: object) -> Hit:
        """One thing `check` returned, as a hit: `(path, what is wrong)`, `(path, line, what)`, or a `Hit`. Anything
        else is a sentence. Measured before: a list of bare paths was read a character at a time, and `README.md`
        became a finding at the file `R` whose evidence was `E`."""
        if isinstance(item, Hit):
            return item
        if isinstance(item, tuple) and len(item) == 2 and all(isinstance(part, str) for part in item):
            return Hit(item[0], None, item[1])
        if (
            isinstance(item, tuple)
            and len(item) == 3
            and isinstance(item[0], str)
            and _whole(item[1])
            and (isinstance(item[2], str))
        ):
            return Hit(*item)
        raise TypeError(
            f"check(tree) returned {item!r} among its hits. Each hit is a (path, what) pair, a (path, line, what) "
            "triple, or a Hit"
        )

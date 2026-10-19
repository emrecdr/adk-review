"""What a review can run, and what it will: every rule in `rules/`, every guideline in `guidelines/`, and the profiles
and the review policy in `config.toml`. The command and chat both ask here, so they never disagree on what runs.

A check's folder holds checks and nothing else: one class a file, named after the file — `no_print.py` holds the rule
`no-print`, `security.py` the guideline `security`. Anything else there — a module that will not import, a file with
no check or with two, a check missing a part, a name that is not its file's, a rule its own examples disprove — is a
sentence, never a check quietly left out, and the command answers it with exit 2. Every definition is read from the
reviewer's own folders; nothing is read from the repository under review.
"""

from __future__ import annotations

import importlib
import pkgutil
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.findings import RANK
from ..core.guidelines import Guideline
from ..core.profiles import NAME, chosen, expand, expanded, nearest
from ..core.rules import TOOLS, AnyRule, FileRule, Rule, TreeRule, raised_line
from ..core.text import squash
from .rule_helpers import self_test
from .rule_helpers.testing import files_of

#: The reviewer's one config file: the review policy and the profiles, beside the command.
CONFIG = Path(__file__).parents[1] / "config.toml"
#: The two folders of checks, as packages. The rules' helpers are a folder of their own beside them, `rule_helpers/`,
#: which is never read for checks: only these two are.
RULES = f"{__package__}.rules"
GUIDELINES = f"{__package__}.guidelines"
#: What each folder's checks derive from, and where what is no check belongs, as a refusal says it.
NO_CHECK = {
    RULES: "a rule is one class deriving from Rule, FileRule or TreeRule, and a helper rules share lives in "
    "judge/rule_helpers/",
    GUIDELINES: "a guideline is one class deriving from Guideline",
}
#: The three kinds a rule is one of: a class of another shape is no rule, so a folder holds one way to write one.
KINDS = (Rule, FileRule, TreeRule)
#: Every setting under `[review]`: the test its value must pass, and the sentence naming what it must be. `type`, never
#: `isinstance`: TOML's `true` is a bool, and a bool is no number of tokens.
REVIEW = {
    "fail_on": (lambda v: type(v) is str and v in RANK, "one of " + ", ".join(RANK)),
    "max_tokens": (lambda v: type(v) is int and v >= 0, "a whole number of tokens"),
    "verify": (lambda v: type(v) is bool, "true or false"),
    "diff_cap": (lambda v: type(v) is int and v > 0, "a whole number of characters above 0"),
    "skip": (lambda v: type(v) is list and all(type(g) is str and g for g in v), "a list of globs"),
}
#: What each section is a table of, as the sentence refusing another shape names it.
TABLES = {"review": "settings", "profiles": "named lists"}


def load_config(path: Path) -> dict:
    """`config.toml`, read whole: each section a table, each setting it must hold, as it must be, and nothing it
    does not know, since a key misspelt must never be a setting silently left at another value. What its profiles
    name is `chosen`'s to check. Anything else is a sentence, raised as `ValueError` for the command to answer
    with exit 2."""
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8-sig"))  # as Windows Notepad has saved it: a BOM
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path.name}: {exc}") from None
    if strangers := sorted(set(config) - set(TABLES)):
        there = " and ".join(f"[{section}]" for section in TABLES)
        raise ValueError(f"{path.name}: no section [{strangers[0]}]; there are {there}")
    for section, of in TABLES.items():
        if type(config.setdefault(section, {})) is not dict:
            raise ValueError(f"{path.name}: [{section}] must be a table of {of}")
    review = config["review"]
    if strangers := sorted(set(review) - set(REVIEW)):
        raise ValueError(f"{path.name}: [review] {strangers[0]} is not a setting")
    for key, (fits, what) in REVIEW.items():
        if key not in review:
            raise ValueError(f"{path.name}: [review] {key} is missing")
        if not fits(review[key]):
            raise ValueError(f"{path.name}: [review] {key} must be {what}")
    return config


def _words(check: object, *parts: str) -> bool:
    """Whether `check` gives each of `parts` as words: a string, and not an empty one."""
    return all(isinstance(getattr(check, part, None), str) and getattr(check, part) for part in parts)


def _rule_problem(rule: AnyRule) -> str | None:
    """What keeps `rule` from being one whole rule, or None: a part it does not name, or a way of finding hits that
    is not one of the three kinds'."""
    if missing := [part for part in ("id", "title", "fix") if not _words(rule, part)]:
        return f"{type(rule).__name__} needs an id, a title and a fix, and has no {' and no '.join(missing)}"
    if (severity := getattr(rule, "severity", None)) not in RANK:
        return f"{rule.id!r} has severity {severity!r}, not {' or '.join(RANK)}"
    if not isinstance(reads := getattr(rule, "reads_tests", False), bool):  # "no" would read as True
        return f"{rule.id!r} has reads_tests {reads!r}, not True or False"
    if not isinstance(tidies := getattr(rule, "cleanup", False), bool):
        return f"{rule.id!r} has cleanup {tidies!r}, not True or False"
    if (tool := getattr(rule, "tool", None)) is not None and tool not in TOOLS:
        return f"{rule.id!r} names the tool {tool!r}, which no adapter runs; there are {', '.join(sorted(TOOLS))}"
    if "hits" in vars(type(rule)):  # one way to write a rule of each kind: its author writes `check`
        return f"{rule.id!r} writes its own `hits`; a rule writes `check`, and its kind finds the hits"
    return None


def _rule_disproved(rule: AnyRule) -> str | None:
    """What a rule's own examples and its own source say against it, or None: asked last, of a rule whole and
    rightly named, so the cheaper refusals come first. A rule's first test, run wherever the rule is found."""
    if said := self_test(rule):
        return f"{rule.id!r} {said[0]}" + (f" (and {len(said) - 1} more)" if len(said) > 1 else "")
    return None


def _guideline_problem(guideline: Guideline) -> str | None:
    """What keeps `guideline` from being one whole guideline, or None."""
    if not _words(guideline, "id", "title", "role"):
        return f"{type(guideline).__name__} needs an id, a title and a role"
    if type(budget := getattr(guideline, "thinking_budget", None)) is not int or budget < 0:
        return f"{guideline.id!r} has thinking_budget {budget!r}, not a whole number of tokens"
    if not isinstance(tidies := getattr(guideline, "cleanup", False), bool):
        return f"{guideline.id!r} has cleanup {tidies!r}, not True or False"
    return None


def _file(module: str) -> str:
    """A check's file from its module's name, as `--list-checks` and a refusal name it: `judge/rules/no_print.py`."""
    return module.split(".", 1)[1].replace(".", "/") + ".py"


def _found(
    package: str,
    base: type | tuple[type, ...],
    problem: Callable[[Any], str | None],
    disproved: Callable[[Any], str | None] = lambda _check: None,
) -> tuple[Any, ...]:
    """The one `base` each module of `package`'s folder defines, by id: a sentence for anything else there."""
    found = []
    for module in pkgutil.iter_modules(importlib.import_module(package).__path__):
        name, named = f"{package}.{module.name}", module.name.replace("_", "-")
        where = _file(name)
        try:  # the project's own code runs from here on, its attributes too: whatever it raises is a sentence
            loaded = importlib.import_module(name)
            checks = [
                kind()
                for kind in vars(loaded).values()
                if isinstance(kind, type) and issubclass(kind, base) and kind.__module__ == loaded.__name__
            ]
            if len(checks) != 1:
                why = f"a check's file holds one check, and this one holds {len(checks)}: {NO_CHECK[package]}"
            elif not (why := problem(checks[0])):
                if checks[0].id != named:
                    why = f"its id is {checks[0].id!r}; a check is named after its file: {named!r}"
                elif not NAME.fullmatch(named):
                    why = f"{named!r} is no check's name: lower-case words joined by hyphens"
                else:
                    why = disproved(checks[0])
        except (Exception, SystemExit) as exc:  # noqa: BLE001 -- a check file's own code: whatever it raises is named
            # The line of the check's own file that raised, for its author to read; a syntax error names its own.
            line = raised_line(exc, lambda name, where=where: Path(name).as_posix().endswith(where))
            at = f" (line {line})" if line and not isinstance(exc, SyntaxError) else ""
            raise ValueError(f"{where}: {type(exc).__name__}: {exc}{at}") from None
        if why:
            raise ValueError(f"{where}: {why}")
        found.append(checks[0])
    return tuple(sorted(found, key=lambda check: check.id))


@dataclass(frozen=True)
class Catalog:
    """Every check there is: the rules in `rules/` and the guidelines in `guidelines/`, each in its name's order."""

    rules: tuple[AnyRule, ...]
    guidelines: tuple[Guideline, ...]

    def head_reading(self) -> dict[str, bool]:
        """What gathering reads for these checks, as `gather` takes it: the head whole for a tree rule, and for a
        guideline the view it is shown and the radius, which reads the head only where a changed file is Python. The
        command and chat both ask here, so the two cannot gather differently."""
        return {"tree_rules": any(rule.kind == "tree" for rule in self.rules), "guidelines": bool(self.guidelines)}

    def ids(self) -> tuple[str, ...]:
        """Every check's id, the rules and then the guidelines: the report's order, so on a tie the fold keeps the
        reading of code."""
        return tuple(check.check_id for check in (*self.rules, *self.guidelines))

    def groups(self) -> dict[str, list[str]]:
        """The two names a profile may give for every check of a kind."""
        return {"rules": [rule.check_id for rule in self.rules], "guidelines": [g.check_id for g in self.guidelines]}


def catalog() -> Catalog:
    """Every rule and every guideline, found in their two folders and nowhere else; `ValueError` for anything there
    that is not one whole check named after its file."""
    rules = _found(RULES, KINDS, _rule_problem, _rule_disproved)
    return Catalog(rules, _found(GUIDELINES, Guideline, _guideline_problem))


def running(found: Catalog, profiles: dict) -> dict[str, str]:
    """Each check's id, and the profiles that run it in the config's order, or that only `--check` runs it: what
    `--list-checks` and `--explain` both say."""
    runs = expanded(profiles, found.ids(), found.groups())
    return {
        check: ", ".join(profile for profile, ids in runs.items() if check in ids) or "none: --check only"
        for check in found.ids()
    }


def explained(found: Catalog, profiles: dict, check: str) -> dict:
    """`--explain ID`: one check as its file documents it — what it is, why a project wants it, the profiles that run
    it and, for a rule, what it flags and what it leaves alone, by its own examples. `ValueError` for an id no check
    has."""
    one = next((c for c in (*found.rules, *found.guidelines) if c.check_id == check), None)
    if one is None:
        raise ValueError(f"--explain names {check!r}, which no check is; {nearest(check, found.ids())}")
    return _described(one, running(found, profiles))


def _described(one: AnyRule | Guideline, runs: Mapping[str, str]) -> dict:
    """One check as `--explain` says it and `--list-checks` lists it, `runs` naming the profiles that run each."""
    said = {
        "id": one.check_id,
        "file": _file(type(one).__module__),
        "profiles": runs[one.check_id],
        "title": one.title,
        "why": squash(type(one).__doc__ or ""),
    }
    if isinstance(one, Guideline):
        return {**said, "kind": "guideline", "role": squash(one.role)}
    bad, good = ([files_of(example) for example in examples] for examples in (one.bad, one.good))
    return {
        **said,
        "kind": f"{one.kind} rule",
        "severity": one.severity,
        "tests": "read" if one.reads_tests else "not read",
        "fix": one.fix,
        "bad": bad,
        "good": good,
    }


@dataclass(frozen=True)
class Selection(Catalog):
    """What one review runs, a catalog's part: the rules and the guidelines the profile turns on, in the order it
    names them, the profile as the report names it, and the id of every check it leaves off."""

    name: str
    off: tuple[str, ...]


def select(found: Catalog, profiles: dict, profile: str, picked: Sequence[str] = ()) -> Selection:
    """The checks one review runs: `profile`, every profile read first and `default` required — or, with `--check`,
    the ids picked for this one run, held to what a profile is."""
    known, groups = found.ids(), found.groups()
    name, on = profile, chosen(profiles, profile, known, groups)
    if picked:
        name, on = "--check " + " ".join(picked), expand(list(picked), known, groups, "--check")
    rules = {rule.check_id: rule for rule in found.rules}
    guidelines = {g.check_id: g for g in found.guidelines}
    return Selection(
        rules=tuple(rules[check] for check in on if check in rules),
        guidelines=tuple(guidelines[check] for check in on if check in guidelines),
        name=name,
        off=tuple(check for check in known if check not in on),
    )


def table(found: Catalog, profiles: dict) -> list[tuple[str, str, str, str, str, str]]:
    """`--list-checks`: one row a check, the rules and then the guidelines — its id, its kind, the severity its
    findings carry, the profiles that run it, what it looks for, and the file that is all of it."""
    runs = running(found, profiles)
    said = (_described(one, runs) for one in (*found.rules, *found.guidelines))
    return [
        (d["id"], d["kind"], d.get("severity", "the model's, by finding"), d["profiles"], d["title"], d["file"])
        for d in said
    ]

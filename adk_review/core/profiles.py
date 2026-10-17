"""What a check is called, and which checks a review runs.

A check is a rule, decided by code, or a guideline, judged by a model. Its id names its kind and its name —
`rule.no-print`, `guideline.security` — and is the one spelling `config.toml`, `--check`, `--list-checks` and the
report use. Inside ADK, where an agent's name and a state key must be Python identifiers, the same check goes by
`state_key`. A profile is a named list of ids in `config.toml`; `rules` and `guidelines` stand for every check of
their kind. With no `--profile`, a review runs the profile `default`, which the file must hold. Pure, as the rest of
the core is.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from difflib import get_close_matches

#: The profile a review runs when none is named; `config.toml` must hold it, as it holds every other.
DEFAULT = "default"
#: A check's name: lower-case words joined by hyphens, as its file's name spells them with `_`. The catalog holds
#: every check to it, and `state_key` relies on it: no two names so spelled share a key.
NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


def rule_id(name: str) -> str:
    """The check id of the rule `name`: `rule.no-print`."""
    return f"rule.{name}"


def guideline_id(name: str) -> str:
    """The check id of the guideline `name`: `guideline.security`."""
    return f"guideline.{name}"


def state_key(check: str) -> str:
    """A check's id as ADK spells it, an agent's name and a state key, both Python identifiers: `rule.no-print` is
    `rule_no_print`. A name is lower-case words joined by hyphens, so no two ids share a key."""
    return check.replace(".", "_").replace("-", "_")


def expanded(profiles: dict, known: Sequence[str], groups: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    """Every profile's check ids, spelled out: each profile read, whether or not it runs, and `DEFAULT` required,
    since a review given no --profile runs it. Anything among them that does not mean exactly one thing — a profile
    that is no list of known ids, one that turns nothing on, or no `default` at all — is refused, never guessed at."""
    found = {profile: expand(ids, known, groups, f"profile {profile!r}") for profile, ids in profiles.items()}
    if DEFAULT not in found:
        raise ValueError(
            f"config.toml [profiles] has no `{DEFAULT}`: the checks a review runs when no --profile names one"
        )
    return found


def chosen(profiles: dict, name: str, known: Sequence[str], groups: Mapping[str, Sequence[str]]) -> list[str]:
    """The check ids profile `name` turns on, in the order it names them. `DEFAULT` is a profile like any other, the
    one a review runs when no --profile names another, and the file must hold it: a check runs only where a profile
    names it. A profile names a check by its id, or every check of a kind by the group's name — `rules`,
    `guidelines`. Every profile is read first, by `expanded`."""
    found = expanded(profiles, known, groups)
    if name not in found:
        others = sorted(profile for profile in found if profile != DEFAULT)
        raise ValueError(f"no profile {name!r}; there are {', '.join([DEFAULT, *others])}")
    return found[name]


def expand(ids: object, known: Sequence[str], groups: Mapping[str, Sequence[str]], what: str) -> list[str]:
    """The check ids a list names — each an id among `known`, or a group's name standing for its ids — spelled out
    once each in the order given. `what` is the list as a sentence refusing it names it: a profile, or the `--check`
    flag; refused when it is no list, names something unknown, or turns nothing on."""
    if not isinstance(ids, list):
        raise ValueError(f"{what} must be a list of check ids")
    if strangers := [check for check in ids if check not in known and check not in groups]:
        nearest = get_close_matches(strangers[0], [*known, *groups], n=1)
        hint = f"did you mean {nearest[0]}?" if nearest else f"known: {', '.join(known)}"
        raise ValueError(
            f"{what} names {strangers[0]!r}, which no check or group is; {hint}"
            + (f"; groups: {', '.join(groups)}" if groups else "")
        )
    expanded = list(dict.fromkeys(check for named in ids for check in groups.get(named, [named])))
    if not expanded:
        raise ValueError(f"{what} turns no check on, and a review that looks at nothing approves anything")
    return expanded

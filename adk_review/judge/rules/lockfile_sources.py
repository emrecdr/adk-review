"""A lockfile that fetches a package from somewhere other than its package manager's public registry."""

from __future__ import annotations

import re

from ..rule_helpers import ChangedFile, FileRule, matches

#: Each lockfile read, by name, and where its package manager fetches from by default, as the starts of the URLs it
#: writes there. A source an added line names outside them is one a person reads before it is fetched; a team's own
#: mirror joins its lockfile's tuple here. A lockfile of another kind is not read.
REGISTRIES = {
    "package-lock.json": ("https://registry.npmjs.org/",),
    "npm-shrinkwrap.json": ("https://registry.npmjs.org/",),
    "yarn.lock": ("https://registry.yarnpkg.com/", "https://registry.npmjs.org/"),
    "pnpm-lock.yaml": ("https://registry.npmjs.org/",),
    "uv.lock": ("https://pypi.org/simple", "https://files.pythonhosted.org/"),
    "poetry.lock": ("https://pypi.org/", "https://files.pythonhosted.org/"),
    "pdm.lock": ("https://pypi.org/", "https://files.pythonhosted.org/"),
    "Pipfile.lock": ("https://pypi.org/simple", "https://pypi.python.org/simple"),
    "Cargo.lock": ("registry+https://github.com/rust-lang/crates.io-index", "sparse+https://index.crates.io/"),
    "Gemfile.lock": ("https://rubygems.org/",),
}
#: A source a lockfile names: a URL, with Cargo's or a package manager's `registry+`, `sparse+` or `git+` before it.
SOURCE = re.compile(r"(?:(?:git|registry|sparse)\+)?(?:https?|git|ssh)://[^\s\"',}\]]+")
#: Where a source fetches from: its scheme and its host, without a user or a password a URL can carry, read to the
#: last `@` before the host, as redaction reads one: a password may hold an `@`, and its tail is no host.
ORIGIN = re.compile(r"((?:\w+\+)?\w+)://(?:[^/\s]*@)?([^/:?#\s]+)")


class LockfileSources(FileRule):
    """A lockfile is written whole by its package manager, so the shipped skip list leaves it to the rules, and no
    guideline reads where it fetches from. A branch that points a package at a host of its own, a git repository or
    plain HTTP changes what every install runs, in lines no one reads. Honest edits are common — a team's mirror, a
    fork pinned for a fix — so it starts below the bar: a person reads the source before it is fetched."""

    id = "lockfile-sources"
    severity = "minor"
    reads_tests = True  # a lock file a test installs from fetches what it names all the same
    title = "a lockfile fetches a package from outside its public registry"
    fix = "Confirm the source is the one meant, a registry or a mirror the team trusts, over TLS, before it is fetched."
    bad = (
        ("uv.lock", 'source = { registry = "https://pypi.evil.example/simple" }\n'),
        ("web/package-lock.json", '"resolved": "http://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",\n'),
        ("Cargo.lock", 'source = "git+https://github.com/someone/serde?rev=1#1a2b3c"\n'),
    )
    good = (
        ("uv.lock", 'source = { registry = "https://pypi.org/simple" }\n'),
        ("web/package-lock.json", '"resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",\n'),
        ("docs/sources.md", "Fetched from https://pypi.evil.example/simple\n"),
    )

    def check(self, file: ChangedFile) -> list[tuple[int | None, str]]:
        registry = next((starts for name, starts in REGISTRIES.items() if matches(file.path, name)), None)
        if registry is None:
            return []
        found: dict[str, tuple[int | None, str]] = {}  # by origin, the first line that names it
        # Each added line as the diff shows it: a lockfile past the size limits is not read whole, and needs not be.
        for number in sorted(file.added):
            for source in SOURCE.findall(file.near(number) or ""):
                if source.startswith(registry):
                    continue
                origin = ORIGIN.match(source)
                where = f"{origin[1]}://{origin[2]}" if origin else source
                found.setdefault(where, (number, f"{file.path} fetches from {where}, outside its public registry"))
        return list(found.values())

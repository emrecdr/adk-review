"""A lockfile that fetches a package from somewhere other than its package manager's public registry."""

from __future__ import annotations

import re

from ..rule_helpers import ChangedFile, FileRule, matches

#: Each lockfile read, by name, and where its package manager fetches from by default, as the starts of the URLs it
#: writes there; hex, bun and CocoaPods' CDN write none for a package from the registry. A source an added line names
#: outside them is one a person reads before it is fetched; a team's own mirror joins its lockfile's tuple here. The
#: skip list's other lockfiles are read by the credential rules alone: `go.sum`, `gradle.lockfile` and NuGet's
#: `packages.lock.json` name no source, Swift's `Package.resolved`, Nix's `flake.lock` and Composer's `composer.lock`
#: name a URL for every package with no one registry to hold it to, and Deno's `deno.lock`, whose imports are URLs by
#: design, is not read until a real one is measured. uv's tuple holds the company's own Nexus, by scheme, host and port:
#: its projects declare it as their default index, and every change to a lock drew a finding for it.
REGISTRIES = {
    "package-lock.json": ("https://registry.npmjs.org/",),
    "npm-shrinkwrap.json": ("https://registry.npmjs.org/",),
    "yarn.lock": ("https://registry.yarnpkg.com/", "https://registry.npmjs.org/"),
    "pnpm-lock.yaml": ("https://registry.npmjs.org/",),
    "uv.lock": (
        "https://pypi.org/simple",
        "https://files.pythonhosted.org/",
        "https://p-nexus-3.development.nl.eu.abnamro.com:8443/",
    ),
    "poetry.lock": ("https://pypi.org/", "https://files.pythonhosted.org/"),
    "pdm.lock": ("https://pypi.org/", "https://files.pythonhosted.org/"),
    "Pipfile.lock": ("https://pypi.org/simple", "https://pypi.python.org/simple"),
    "Cargo.lock": ("registry+https://github.com/rust-lang/crates.io-index", "sparse+https://index.crates.io/"),
    "Gemfile.lock": ("https://rubygems.org/",),
    "pubspec.lock": ("https://pub.dev", "https://pub.dartlang.org"),
    "mix.lock": ("https://repo.hex.pm",),
    "bun.lock": ("https://registry.npmjs.org/",),
    "Podfile.lock": ("https://github.com/CocoaPods/Specs.git", "https://cdn.cocoapods.org/"),
}
#: A source a lockfile names: a URL, with Cargo's or a package manager's `registry+`, `sparse+` or `git+` before it,
#: or a host's shorthand, as bun writes `github:owner/repo#ref`, named without its ref.
SOURCE = re.compile(
    r"(?:(?:git|registry|sparse)\+)?(?:https?|git|ssh)://[^\s\"',}\]]+|\b(?:github|gitlab|bitbucket):[^\s\"',}\]#]+"
)
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
    fix = "Confirm the source before an install fetches it. Use a registry or a mirror the team trusts, over TLS."
    bad = (
        ("uv.lock", 'source = { registry = "https://pypi.evil.example/simple" }\n'),
        ("web/package-lock.json", '"resolved": "http://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",\n'),
        ("Cargo.lock", 'source = "git+https://github.com/someone/serde?rev=1#1a2b3c"\n'),
        # Each below as a real lockfile holds it: harmonoid's, elixir-ls's, ens-contracts' and Hammerspoon's.
        ("pubspec.lock", '      url: "https://github.com/alexmercerind/flutter-draggable-scrollbar.git"\n'),
        (
            "mix.lock",
            '  "toxic2": {:git, "https://github.com/lukaszsamson/toxic2.git",'
            ' "c47c911dac5aafa860206d222a781c3d71afc843", [ref: "c47c911dac5aafa860206d222a781c3d71afc843"]},\n',
        ),
        (
            "bun.lock",
            '    "clones-with-immutable-args": ["clones-with-immutable-args@github:Arachnid/clones-with-immutable-args'
            '#2376882", {}, "Arachnid-clones-with-immutable-args-2376882"],\n',
        ),
        ("ios/Podfile.lock", "EXTERNAL SOURCES:\n  Sentry:\n    :git: https://github.com/getsentry/sentry-cocoa.git\n"),
    )
    good = (
        ("uv.lock", 'source = { registry = "https://pypi.org/simple" }\n'),
        ("web/package-lock.json", '"resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",\n'),
        ("docs/sources.md", "Fetched from https://pypi.evil.example/simple\n"),
        # And as harmonoid's, elixir-ls's, wow.export's and Hammerspoon's hold a package from the registry.
        ("pubspec.lock", '      url: "https://pub.dev"\n    source: hosted\n'),
        (
            "mix.lock",
            '  "deep_merge": {:hex, :deep_merge, "1.0.0",'
            ' "b4aa1a0d1acac393bdf38b2291af38cb1d4a52806cf7a4906f718e1feb5ee961", [:mix], [], "hexpm",'
            ' "ce708e5f094b9cd4e8f2be4f00d2f4250c4095be93f8cd6d018c753894885430"},\n',
        ),
        (
            "bun.lock",
            '    "@babel/helper-string-parser": ["@babel/helper-string-parser@7.27.1", "", {},'
            ' "sha512-qMlSxKbpRlAridDExk92nSobyDdpPijUq2DW6oDnUqd0iOGxmQjyqhMIihI9+zv4LPyZdRje2cavWPbCbWm3eA=="],\n',
        ),
        ("ios/Podfile.lock", "SPEC REPOS:\n  https://github.com/CocoaPods/Specs.git:\n    - ASCIImage\n"),
    )

    def check(self, file: ChangedFile) -> list[tuple[int | None, str]]:
        name, registry = next(
            ((name, starts) for name, starts in REGISTRIES.items() if matches(file.path, name)), ("", None)
        )
        if registry is None:
            return []
        found: dict[str, tuple[int | None, str]] = {}  # by origin, the first line that names it
        # Each added line as the diff shows it: a lockfile past the size limits is not read whole, and needs not be.
        # A comment fetches nothing, and pub writes `https://dart.dev` in the header of every pubspec.lock.
        # A lockfile renamed into place adds no line, and every line of it is fetched from now.
        for number in file.new_lines(name):
            line = file.near(number) or ""
            if line.lstrip().startswith("#"):
                continue
            for source in SOURCE.findall(line):
                if any(_within(source, start) for start in registry):
                    continue
                origin = ORIGIN.match(source)
                where = f"{origin[1]}://{origin[2]}" if origin else source
                found.setdefault(where, (number, f"{file.path} fetches from {where}, outside its public registry"))
        return list(found.values())


def _within(source: str, start: str) -> bool:
    """Whether `source` is the registry's address `start` or lies under it: what follows begins a path, a query or a
    fragment, so `https://pub.dev` holds `https://pub.dev/packages` and no `https://pub.dev.evil.example`. Never a
    colon after it, which opens a user's name in `https://pub.dev:x@evil.example`, whose host is `evil.example`; the
    colon CocoaPods writes after a YAML key is taken off first, since no URL ends in one."""
    source = source.removesuffix(":")
    return source.startswith(start) and (start.endswith("/") or source[len(start) :][:1] in ("", "/", "?", "#"))

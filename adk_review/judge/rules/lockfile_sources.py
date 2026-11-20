"""A lockfile that fetches a package from somewhere other than its package manager's public registry."""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..rule_helpers import ChangedFile, FileRule, matches

#: Each lockfile read, by name, and where its package manager fetches from by default, as the starts of the URLs it
#: writes there; hex, bun and CocoaPods' CDN write none for a package from the registry. A source an added line names
#: outside them is one a person reads before it is fetched. Public registries alone: a team's own registry or mirror is
#: the operator's setting, `REVIEW_REGISTRIES`, which the rule is given as `own_registries`, and never a line here,
#: since this file is published with the reviewer. Read by review: a company's projects declare its registry as their
#: default index, every change to a lock drew a finding for it, and its host was written into this list, in a public
#: repository. The skip list's other lockfiles are read by the credential rules alone: `go.sum`, `gradle.lockfile` and
#: NuGet's `packages.lock.json` name no source, Swift's `Package.resolved`, Nix's `flake.lock` and Composer's
#: `composer.lock` name a URL for every package with no one registry to hold it to, and Deno's `deno.lock`, whose
#: imports are URLs by design, is not read until a real one is measured.
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
    "pubspec.lock": ("https://pub.dev", "https://pub.dartlang.org"),
    "mix.lock": ("https://repo.hex.pm",),
    "bun.lock": ("https://registry.npmjs.org/",),
    "Podfile.lock": ("https://github.com/CocoaPods/Specs.git", "https://cdn.cocoapods.org/"),
}
#: A source a lockfile names: a URL in each spelling a URL's parser reads as one, with Cargo's or a package manager's
#: `registry+`, `sparse+` or `git+` before it, or a host's shorthand, as bun writes `github:owner/repo#ref`, named
#: without its ref. The scheme in any case, then any run of slashes and backslashes, or none where the address follows
#: at once and the scheme opens a word, so a key, `:git: https://…`, is no scheme. Read by review, and reproduced with
#: uv and a server of the probe's own: uv installed from `HTTPS://`, `https:/`, `https:`, `https:///` and `https:\\`,
#: each on a line the rule passed over where it read `https://` alone. Measured over 17 projects' lockfiles, of every
#: kind read here, 7,687 sources: the same ones either way.
SOURCE = re.compile(
    r"(?i)(?:(?:git|registry|sparse)\+)?(?:https?|git|ssh):[/\\]+[^\s\"',}\]]+"
    r"|(?<![\w.-])(?:(?:git|registry|sparse)\+)?(?:https?|git|ssh):[^\s\"',}\]/\\][^\s\"',}\]]*"
    r"|\b(?:github|gitlab|bitbucket):[^\s\"',}\]#]+"
)
#: Where a source fetches from: its scheme and its host, without a user or a password a URL can carry, read to the
#: last `@` before the host, as redaction reads one: a password may hold an `@`, and its tail is no host. Never past a
#: `?` or a `#`, which end the host: read by an audit, `https://evil.example?@pypi.corp.example.com` read as the
#: company's host, where every URL parser fetches from `evil.example`. In each spelling `SOURCE` takes, so none is
#: printed whole; a host's shorthand has none, and is named as it stands.
ORIGIN = re.compile(r"(?i)((?:\w+\+)?(?:https?|git|ssh)):[/\\]*(?:[^/\\?#\s]*@)?([^/\\:?#\s]+)")
#: A backslash in a line, with what it stands before: a quote's and a backslash's own are how Poetry writes a marker and
#: Windows a path, and any other, `\u0068ttps`, `ht\ttps`, or one that ends the line, writes an address in pieces.
ESCAPE = re.compile(r"\\(.?)")
#: What opens a string over several lines in TOML, where a line break inside an address is one its parser drops.
OVER_LINES = ('"""', "'''")
#: How Cargo says it reads a registry, written before its address: `registry+` an index kept in git, `sparse+` one over
#: HTTP. Taken off before a source is set against a registry of the operator's own, which is given as an address.
#: Never `git+`: a git source is no registry's, whoever hosts it.
READ_AS_A_REGISTRY = ("registry+", "sparse+")


class LockfileSources(FileRule):
    """A lockfile is written whole by its package manager, so the shipped skip list leaves it to the rules, and no
    guideline reads where it fetches from. A branch that points a package at a host of its own, a git repository or
    plain HTTP changes what every install runs, in lines no one reads. Honest edits are common — a team's mirror, a
    fork pinned for a fix — so it starts below the bar: a person reads the source before it is fetched. An address is
    read in each spelling a URL's parser takes, and a line that writes a string as no package manager does, with an
    escape or over several lines, is named for what it may hide. Known limit: the rule reads a line at a time, so an
    address a YAML lockfile writes over several lines of one string is not read."""

    id = "lockfile-sources"
    severity = "minor"
    reads_tests = True  # a lock file a test installs from fetches what it names all the same
    security = True  # a verifier may dispute its finding, never drop it
    title = "a lockfile fetches a package from outside its public registry"
    fix = (
        "Confirm the source before an install fetches it. Name a registry of your own in REVIEW_REGISTRIES, or use "
        "one the team trusts, over TLS."
    )
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
        # A spelling a URL's parser reads and no package manager writes, and an address written in pieces.
        ("uv.lock", 'wheels = [{ url = "HTTPS://pypi.evil.example/pad-1.0-py3-none-any.whl" }]\n'),
        ("uv.lock", 'wheels = [{ url = "\\u0068ttps://pypi.evil.example/pad-1.0-py3-none-any.whl" }]\n'),
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
        ("poetry.lock", 'markers = "python_version < \\"3.11\\""\n'),  # a quote's escape, as Poetry writes a marker
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
            if _in_pieces(line):  # by no origin: once a file, on the first such line
                found.setdefault("", (number, f"{file.path} {WRITTEN_IN_PIECES}"))
            for source in _sources(line):
                if any(_within(source, start) for start in registry) or _of_the_operators(source, self.own_registries):
                    continue
                origin = ORIGIN.match(source)
                where = f"{origin[1]}://{origin[2]}" if origin else source
                found.setdefault(where, (number, f"{file.path} fetches from {where}, outside its public registry"))
        return list(found.values())


#: What a line's finding says where its string is written as no package manager writes one, after the file's path.
WRITTEN_IN_PIECES = "writes a string as no package manager does, with an escape or over several lines"


def _sources(line: str) -> list[str]:
    """Each source the line names, read as it stands and with no tab, which a URL's parser drops: reproduced, uv
    installed from an address with a tab inside its scheme, and a tab after a registry's host joined it to another's.
    Both readings count, so neither hides what the other shows."""
    return list(dict.fromkeys(source for text in (line, line.replace("\t", "")) for source in SOURCE.findall(text)))


def _in_pieces(line: str) -> bool:
    """Whether the line writes a string as no package manager does: an escape but a quote's or a backslash's own, a
    backslash at its end, or a string opened over several lines. Reproduced with uv: an address written `\\u0068ttps`,
    or with a line break inside it, was installed from, and a reader of single lines sees neither. Measured over 17
    projects' lockfiles, 63,750 lines: none holds one of them, so the line is named for a person to read."""
    return any(mark in line for mark in OVER_LINES) or any(after not in ('"', "\\") for after in ESCAPE.findall(line))


def _of_the_operators(source: str, own: Sequence[str]) -> bool:
    """Whether `source` lies under a registry the operator named as their own, for any lockfile, however Cargo says
    it reads one: under an address, by its port too, or on a host of a domain, `*.example.com`. Over TLS alone: the
    setting takes no other address."""
    address = next((source.removeprefix(how) for how in READ_AS_A_REGISTRY if source.startswith(how)), source)
    return any(_in_domain(address, start[2:]) if start.startswith("*.") else _within(address, start) for start in own)


def _in_domain(source: str, domain: str) -> bool:
    """Whether `source` is fetched over TLS from `domain` or a host under it: `corp.example.com` holds
    `https://pypi.corp.example.com/simple` on any port, never `corp.example.com.evil.example` nor
    `evilcorp.example.com`, and a user's name before an `@` is no host. Asked by the owner: a company serves its
    packages from several hosts of one domain, where an address names one. A git source there is still one to read,
    as under an address."""
    origin = ORIGIN.match(source.removesuffix(":"))
    if origin is None or origin[1].lower() != "https":
        return False
    host = origin[2].lower().rstrip(".")
    return host == domain.lower() or host.endswith(f".{domain.lower()}")


def _within(source: str, start: str) -> bool:
    """Whether `source` is the registry's address `start` or lies under it: what follows begins a path, a query or a
    fragment, so `https://pub.dev` holds `https://pub.dev/packages` and no `https://pub.dev.evil.example`. Never a
    colon after it, which opens a user's name in `https://pub.dev:x@evil.example`, whose host is `evil.example`; the
    colon CocoaPods writes after a YAML key is taken off first, since no URL ends in one."""
    source = source.removesuffix(":")
    return source.startswith(start) and (start.endswith("/") or source[len(start) :][:1] in ("", "/", "?", "#"))

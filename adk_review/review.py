"""The command: evidence first, guidelines judge, code decides.

    uv run python -m adk_review.review [repo] [--head B] [--base main | --all | --staged] [--path DIR] [...]

Every flag, and what it does, is in `GUIDE.md` beside the package, which a test holds to this parser.

A review runs in four steps, a package each: `core/` is the domain and imports
nothing that runs a process or calls a model, `collect/` gathers the evidence,
`judge/` applies the checks — every rule in `judge/rules/`, every guideline in
`judge/guidelines/`, one file each — and `deliver/` writes the report.
`config.toml` holds the profiles and the review policy, and this file is the
driver that fixes the order of the steps. Each package may import only itself
and the packages before it; `tests/test_layering.py` keeps it that way. Around
the steps:

1. Retries on both arms (`judge/config.py`), set once on the request both clients read:
   `RETRY_ATTEMPTS` on Gemini, none on Copilot, whose SDK spaces two retries of its own.
2. A token ceiling (`--max-tokens`) that ends the spending, never the report:
   the call that would pass it is refused, the guideline is named, exit 3.
3. An outside tool for a rule that names one: ruff for `lint`, `pitfalls` and `simplify`
   (`judge/rules/`), run by `collect/ruff.py` through `collect/tools.py`
   over the changed Python files at the head.
4. A second opinion (`--verify`): another model judges each guideline finding
   before the fold — one verifier per finding, a second graph run like the
   first; refuted findings are dropped and named, unjudged ones kept and
   named, the rules' findings never asked, nor a guideline's that only says again
   what a rule found.
5. A blast radius (`core/blast.py`): what at the head depends on each changed
   file, read from the syntax tree, told to the guidelines beside the diff and
   heading the report; the verdict never reads it.
6. Every check one file (`judge/catalog.py`): a rule in `judge/rules/` —
   over each added line, each changed file, or the head — and a guideline in
   `judge/guidelines/`, each named after its file, and named profiles of which
   checks run, chosen with `--profile`; `--list-checks` shows them all. Every
   definition is read from the reviewer's own folders; nothing is read from
   the repository under review.
7. What a review covers: the change against a base; `--path` keeps it to a
   folder of the change, the rest counted and named; `--all` reviews every
   file at the head; `--staged`, the index against `HEAD`. With neither a base
   nor `--all`, a person at a terminal is asked which; a pipeline gets the
   change against `main`, or `master`, or the branch `origin/HEAD` names. The
   repository is never assumed: named first on the command line, or asked for
   at a terminal; a pipeline that names none is a usage error.
8. The rules as a node (`judge/checks.py`): every rule runs in one
   `FunctionNode` before the guidelines, in this graph and in chat's, its
   answer left in state as each guideline's is; a profile with no guideline
   runs them straight from here, as `--try` does, the engine never imported.
9. Reports beside the text one, under its safety rules: JSON for a pipeline
   (`--json FILE`) and a hand-off for a coding agent (`--handoff FILE`), both
   written whenever a review is decided, so never on exit 2.

The order is the architecture: deterministic evidence and rules first, model
judgement second, deterministic decision last; a model never decides
whether the build passes. `main` reads as that order, and so does the graph.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import signal
import sys
import tempfile
import threading
import time
import tomllib
import warnings
from collections.abc import Callable, Generator, Sequence
from importlib import metadata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TextIO

#: Why the reviewer could not load, if it could not: a broken install is one sentence and exit 3 like every other
#: failure, never Python's own exit 1, which reads as a blocker. Measured before: a package that would not import
#: ended the command in a traceback and exit 1 before `main` ran. The engine — ADK, and the judge's agents, graph,
#: plugins and verifier — is imported where a review starts, never here: the modes that read the checks alone,
#: `--list-checks`, `--explain` and `--try`, and a usage error, never wait for it.
UNLOADED: str | None = None
try:
    from dotenv import load_dotenv

    from .collect.git import MAX_FILE_BYTES, GitError, checked_out, collect_evidence, default_base, is_repository
    from .core.evidence import Evidence, unreleased
    from .core.findings import RANK
    from .core.guidelines import Guideline
    from .core.health import Health, measured
    from .core.profiles import DEFAULT
    from .core.rules import FileRule
    from .core.verdict import (
        EXIT_CLEAN,
        EXIT_DEGRADED,
        EXIT_FINDINGS,
        EXIT_INTERRUPTED,
        EXIT_TERMINATED,
        EXIT_USAGE,
        Outcome,
        decide,
        fold,
        from_rules,
        not_read,
    )
    from .deliver.report import (
        Previous,
        as_data,
        read_previous,
        render,
        render_checks,
        render_dry_run,
        render_explained,
        render_handoff,
        render_tried,
        safe_line,
    )
    from .judge.catalog import (
        CONFIG,
        REVIEW,
        Selection,
        catalog,
        explained,
        load_config,
        select,
        table,
    )
    from .judge.checks import run_rules, unspent
    from .judge.rule_helpers.testing import as_change
except Exception as exc:  # noqa: BLE001 -- a broken install's own words, whatever it raised
    UNLOADED = f"{type(exc).__name__}: {exc}"

ROOT = Path(__file__).resolve().parents[1]
#: The distribution `adk-review` is installed from, whose version a run names.
DISTRIBUTION = "adk-review"


def setting(key: str) -> Callable[[str], int]:
    """The type of the flag that overrides the `[review]` setting `key`: a whole number held to the config's own test
    and named by its sentence, so a flag takes no value the config would refuse. Measured before: `--max-tokens -5`
    was taken, printed as a ceiling, and would have refused every call."""
    fits, what = REVIEW[key]

    def parsed(value: str) -> int:
        try:
            number: int | None = int(value)
        except ValueError:
            number = None
        if number is None or not fits(number):
            raise argparse.ArgumentTypeError(f"{value!r} is not {what}")
        return number

    return parsed


#: Where a review's results go, which no result depends on: left out of what keys a kept review.
OUTPUTS = {"json", "handoff", "cache", "record", "trace", "debug_log", "progress", "quiet"}
#: How often a person at a terminal is told the review still works, in seconds. A model's call can take a minute, and
#: silence reads as a hang.
HEARTBEAT_SECONDS = 10
#: The engine's own outputs, by dest: a review with no guideline still runs the engine where one is asked for.
ENGINE = ("trace", "debug_log", "record")
#: The modes besides a review, by their flags' dests: each stops before a review would start.
MODES = {"dry_run": "--dry-run", "list_checks": "--list-checks", "try_paths": "--try", "explain": "--explain"}
#: What only a review reads, by dest: what it writes, keeps, records and compares against.
REVIEW_ONLY = {
    "json": "--json",
    "handoff": "--handoff",
    "cache": "--cache",
    "previous": "--previous",
    "record": "--record",
    "trace": "--trace",
    "debug_log": "--debug-log",
}


def _try(paths: Sequence[str], chosen: Selection, fail_on: str) -> int:
    """`--try PATH…`: the chosen rules over files on disk, each read as wholly added, the head these files alone, as
    a rule's own tests read it: one command where seeing a rule fire took four, and two more an edit. Exit 1 at the
    bar, 3 for a rule that could not look, decided as a review's is."""
    files, skipped = _on_disk(paths)
    for path, why in skipped:
        _say(f"--try leaves out {path}: {why}")
    if not files:
        _say("--try found no text file to read")
        return EXIT_USAGE
    entries, tree = as_change(files)
    answers = run_rules({"files": entries, "tree": tree}, chosen.rules)
    print(render_tried(paths, answers, [g.check_id for g in chosen.guidelines]))
    found, holes = from_rules(answers)
    return decide(Outcome(kept=found, errors=holes), fail_on)[1]


def _on_disk(paths: Sequence[str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Each file under `paths` and its text: a file by its path as given, a folder's files by their paths inside it,
    as a project's own head names them, `/`-separated, and never a `.git`. A file that is not UTF-8 text, or past
    the size a review reads, is left out and named."""
    found: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []
    for given in paths:
        root = Path(given).expanduser()
        if not root.exists():
            skipped.append((given, "no such file or folder"))
            continue
        files = (
            [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
        )
        for file in files:
            name = file.as_posix() if file == root else file.relative_to(root).as_posix()
            if file.stat().st_size > MAX_FILE_BYTES:
                skipped.append((name, f"over {MAX_FILE_BYTES:,} bytes"))
                continue
            try:
                found[name] = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped.append((name, "not UTF-8 text"))
    return found, skipped


def _traced(path: str) -> None:
    """ADK's spans recorded to a local SQLite file, through ADK's own exporter: a provider of the run's own, never
    `maybe_set_otel_providers`, which also exports to any `OTEL_EXPORTER_OTLP_*` endpoint the environment names, and
    the spans would leave the machine. Each span is written as it ends, so a run stopped midway keeps what it did."""
    from google.adk.telemetry.sqlite_span_exporter import SqliteSpanExporter
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(SqliteSpanExporter(db_path=os.path.abspath(path))))
    trace.set_tracer_provider(provider)


def build_parser(review: dict) -> argparse.ArgumentParser:
    """The command's flags, each setting's default taken from `config.toml`'s `[review]`: a flag wins for one run."""
    parser = argparse.ArgumentParser(
        prog="adk-review",
        description="Review a branch: rules in Python, guidelines judge, code decides.",
        # Kept as written: argparse would reflow the typical command across two lines.
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "typical:     adk-review <repo> --base main --profile default-rules   (the default's rules, no model)\n"
            "             drop --profile for its guidelines too\n"
            "exit codes:  0 approved, 1 a finding at --fail-on, 2 a usage error before any model call,\n"
            "             3 a hole: a check that failed or could not look\n"
            "every flag, check and limit: GUIDE.md beside the package"
        ),
    )
    parser.add_argument(
        "repo",
        nargs="?",
        help="the repository to review. A terminal asks for it when it is missing. A pipeline must give it",
    )
    parser.add_argument("--head", help="the branch under review (default: the one checked out)")
    what = parser.add_mutually_exclusive_group()
    what.add_argument(
        "--base",
        help="the branch the change merges into. Without it or --all, a terminal asks. A pipeline uses main, then "
        "master, then the branch origin/HEAD names",
    )
    what.add_argument("--all", action="store_true", help="review every file at the head, not what it changes")
    what.add_argument(
        "--staged",
        action="store_true",
        help="review the staged index against HEAD, as the next commit would hold it. The review writes nothing and "
        "skips what is not staged",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="DIR",
        help="review only this folder of the repository. Repeat it for more folders",
    )
    parser.add_argument(
        "--fail-on",
        choices=list(RANK),
        default=review["fail_on"],
        help=f"exit 1 at or above this severity (config.toml: {review['fail_on']}). Findings below it stay in the "
        "report",
    )
    parser.add_argument(
        "--max-tokens",
        type=setting("max_tokens"),
        default=review["max_tokens"],
        help=f"stop spending past this many tokens (config.toml: {review['max_tokens']}). 0 means no ceiling. The "
        "report keeps what the review found by then, and names refused guidelines",
    )
    parser.add_argument(
        "--diff-cap",
        type=setting("diff_cap"),
        default=review["diff_cap"],
        metavar="N",
        help=f"the characters of one file's diff a guideline reads (config.toml: {review['diff_cap']:,}). The report "
        "names a file cut there under not read, and the review ends in exit 3",
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=list(review["skip"]),
        metavar="GLOB",
        help="a glob of changed files the guidelines leave to the rules, as the config's skip list does. The report "
        "names each one, never as a hole. Repeat it for more globs",
    )
    parser.add_argument(
        "--scan",
        action="append",
        default=list(review["scan"]) or ["*"],  # every file, as an empty list reads: a glob added reads no fewer
        metavar="GLOB",
        help="a glob of changed files a review reads, beside the config's scan list. The default reads Python, "
        "notebooks and configs, and every file with no suffix. The review ignores any other file and only lists it. "
        "The credential rules still read the project's own. Repeat it for more globs",
    )
    parser.add_argument(
        "--third-party",
        action="append",
        default=list(review["third_party"]),
        metavar="GLOB",
        help="a glob of folders that hold other projects' code, beside the config's third_party list. The review "
        "never reads a file under one and only lists it. Repeat it for more globs",
    )
    parser.add_argument(
        "--verify",
        action=argparse.BooleanOptionalAction,
        default=review["verify"],
        help="ask a second model whether each guideline finding that says more than a rule holds, refuted ones named "
        f"(config.toml: {'on' if review['verify'] else 'off'})",
    )
    which = parser.add_mutually_exclusive_group()
    which.add_argument(
        "--profile",
        default=DEFAULT,
        help=f"the checks to run, as config.toml's [profiles] names them. Without it, the {DEFAULT} profile runs",
    )
    which.add_argument(
        "--check",
        action="append",
        default=[],
        metavar="ID",
        help="run only this check, this once: rule.<name>, guideline.<name>, or the group rules or guidelines. Repeat "
        "it for more checks. A check's author tries a check this way, with no profile edited",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        help="also write the review to FILE as JSON, for a pipeline. It holds the verdict, its exit code and every "
        "finding with its place. The review writes it whenever it decides, so never on exit 2",
    )
    parser.add_argument(
        "--handoff",
        metavar="FILE",
        help="also write each kept finding to FILE as a short instruction for a coding agent. Each names the place, "
        "the quoted lines, the change, and what must still hold. The review applies nothing",
    )
    parser.add_argument(
        "--cache",
        metavar="DIR",
        help="keep each decided review in DIR. Its key holds the reviewer's code and config, the commits, the checks, "
        "every setting and each model. The same inputs get the kept review, with no model call. The cache never keeps "
        "a review with a hole, or with a finding no verifier could judge",
    )
    parser.add_argument(
        "--previous",
        metavar="FILE",
        help="a previous review's --json report. The review marks each finding new or seen before, by its "
        "fingerprint. It lists what it no longer finds. A review with a hole resolves nothing",
    )
    parser.add_argument(
        "--debug-log",
        metavar="FILE",
        help="write every model request and answer to FILE, secrets redacted, without the session's state. Use it to "
        "see what a guideline was asked and what it said",
    )
    parser.add_argument(
        "--trace",
        metavar="FILE",
        help="record ADK's OpenTelemetry spans, with their timings and token counts, to FILE, a local SQLite "
        "database. The trace stays on this machine",
    )
    parser.add_argument(
        "--record",
        metavar="DIR",
        help="keep each model answer in DIR, under a hash of the request as the model read it, secrets redacted. A "
        "fake model replays them offline to measure a change to an instruction",
    )
    told = parser.add_mutually_exclusive_group()
    told.add_argument(
        "--progress",
        action="store_true",
        help="tell each phase on stderr, and every few seconds what the review still waits on. On at a terminal",
    )
    told.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing when the review passes. Print the report and any error when it does not. For a pipeline",
    )
    # A review, or one of four modes besides it, each stopping before a review would start: one at a time.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve what a review would cover: the revisions, the files, the profile, the model and its readiness. "
        "It prints that and stops. No rule, guideline or model call runs",
    )
    mode.add_argument(
        "--list-checks",
        action="store_true",
        help="print every rule and guideline with its kind, severity and profiles. It then stops, with no repository, "
        "model or key",
    )
    mode.add_argument(
        "--try",
        dest="try_paths",
        nargs="+",
        metavar="PATH",
        help="run the profile's rules, or --check's, over these files or folders on disk, each read as wholly added. "
        "A rule's author sees a rule fire this way, with no repository, commit or model",
    )
    mode.add_argument(
        "--explain",
        metavar="ID",
        help="print one check whole: what it reads, its severity, the profiles that run it, its file and its own "
        "examples. It then stops",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version()}")
    return parser


def version() -> str:
    """The reviewer's version: the installed distribution's, as `adk-review` is installed, else the project's own
    `pyproject.toml`'s, as a clone runs it. What a pipeline that pins the reviewer names in its log, and the JSON
    beside each review."""
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        pass
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def scope_of(folders: Sequence[str]) -> tuple[str, ...]:
    """Each --path as git spells a folder: `/` between names, no `./`, no trailing `/`; `.` is the whole repository.
    One that starts at a root or a drive, or climbs out with `..`, is refused: git names nothing there."""
    scope = []
    for folder in folders:
        path = PurePosixPath(folder.replace("\\", "/"))
        if ".." in path.parts or PureWindowsPath(folder).anchor:  # a root or a drive, `/` or `\` alike
            raise ValueError(f"--path {folder!r} must name a folder inside the repository")
        if path.parts:
            scope.append(path.as_posix())
    return tuple(scope)


def target(repo: str, base: str | None, head: str | None, whole: bool) -> tuple[str | None, str]:
    """What to review, `(base, head)`, a base of None meaning every file at the head. With no --head, the branch
    checked out. With neither --base nor --all, a person at a terminal is asked; anything else, CI or a script, which
    nothing can ask, gets what the command always did: the change against `main`, or `master` where there is none,
    or else the branch `origin/HEAD` names.
    `_a_terminal` says when a question can be asked."""
    if not is_repository(repo):
        return base, head or "HEAD"  # the collector names what is missing
    head = head or checked_out(repo)
    if head is None:
        raise ValueError(f"no branch is checked out in {repo}: name the one to review with --head")
    if base is not None or whole:
        return base, head
    default = default_base(repo)
    if _a_terminal():
        return _ask(head, default), head
    if default is None:
        why = "no main or master branch, and no origin/HEAD naming one"
        raise ValueError(f"{repo} has {why}: name the base with --base, or review it all with --all")
    return default, head


def repository(given: str | None) -> str:
    """The repository to review: the one named, else the one a person at a terminal names when asked, on stderr, as
    the base is asked for. Never assumed: at the user's request, a tool that judges code reviews what it is told to,
    and no repository is a default here, a demo's included. A pipeline, which nothing can ask, names it or is
    refused."""
    if given is not None:
        return os.path.abspath(os.path.expanduser(given))  # the report names it whole: `.` once printed as `.`
    answer = _answer("review: which repository should be reviewed? Its path: ") if _a_terminal() else ""
    if not (path := answer.strip().strip("\"'")):  # a path pasted with its quotes is the path
        raise ValueError("no repository named: give its path as the first argument")
    return os.path.abspath(os.path.expanduser(path))


def _a_terminal() -> bool:
    """Whether a question can be asked: stdin and stderr are both the terminal, the answer's end and the question's.
    Measured with stderr sent to a file: the question went there, and the command waited on a terminal that showed
    nothing."""
    return _terminal(sys.stdin) and _terminal(sys.stderr)


def _answer(prompt: str) -> str:
    """The line a person types after `prompt`, the prompt on stderr so a report sent to a file is still only the
    report; "" at the end of the input, or on Ctrl-C."""
    print(prompt, end="", file=sys.stderr, flush=True)
    try:
        return sys.stdin.readline()
    except KeyboardInterrupt:
        return ""


def _ask(head: str, default: str | None) -> str | None:
    """The base to compare with, or None for the whole project, asked as `repository` asks for the repository. Asked
    again until answered; no answer is a usage error."""
    choices: dict[str, str | None] = {"1": default} if default else {}
    choices["2"] = None
    print(f"review: no --base given. What should be reviewed at {head!r}?", file=sys.stderr)
    if default:
        print(f"  1  what it changes against {default!r} (--base {default})", file=sys.stderr)
    print("  2  every file, the whole project (--all)", file=sys.stderr)
    while True:
        answer = _answer(f"choose {' or '.join(choices)}: ")
        if not answer:  # the end of the input, or Ctrl-C: no choice was made
            raise ValueError("no choice was made: name --base or --all")
        if (choice := answer.strip()) in choices:
            return choices[choice]


class Terminated(BaseException):  # named for what happened to the run, as KeyboardInterrupt is
    """A job stopped the run with SIGTERM: raised in the run's place, so what it holds is let go as on Ctrl-C. A
    `BaseException`, as `KeyboardInterrupt` is, for the same reason: the handler raises in whatever frame runs, and a
    model call's own code catches every `Exception` to name a failed guideline and go on. Measured before: raised
    there, the stop read as that guideline's failure, and the review called the other models and ended in exit 3."""


def _terminated(_signal: int, _frame: object) -> None:
    raise Terminated


def _cleared(flag: str, given: str) -> None:
    """`--json FILE` or `--handoff FILE` made ready before the review: a missing folder or a folder refused, and an
    earlier file of that name removed, a link itself and never what it points to, so a run that ends in exit 2 leaves
    no report a pipeline could take for its own. Measured before: an earlier file outlived exit 2, and a link was
    written through."""
    path = Path(given).expanduser()
    if not path.resolve().parent.is_dir():
        raise ValueError(f"{flag} {given}: there is no such folder to write it in")
    if path.is_dir():
        raise ValueError(f"{flag} {path}: that is a folder. Name a file in it")
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ValueError(f"{flag} {path}: the earlier file could not be removed: {exc.strerror or exc}") from None


def _written(path: Path, text: str) -> None:
    """`text` written beside `path` and moved over it: a reader never sees half a report, and the file is the
    operator's alone, as a temporary file is made. A character UTF-8 cannot hold, a lone surrogate standing for a byte
    of a `--head` that was no UTF-8, is written as its escape, which JSON reads back as it was: measured by review,
    the report printed, then the write raised, exit 3, the temporary file left behind, as any failed write left it."""
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        errors="backslashreplace",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        try:
            handle.write(text)
            handle.close()  # closed before it moves: Windows moves no open file
            os.replace(handle.name, path)
        except BaseException:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise


def _terminal(stream: TextIO | None) -> bool:
    """Whether `stream` is a terminal. A closed one is none: Python holds it as None, as under `2>&-`."""
    return stream is not None and stream.isatty()


def _say(sentence: str) -> None:
    """A sentence on stderr, made safe as the report's prose is: git's or an exception's words can be the branch's. One
    write a line, so the heartbeat's thread never splits another line."""
    print(f"review: {safe_line(sentence)}\n", end="", file=sys.stderr)


def _review(argv: list[str] | None = None) -> int:
    try:
        load_dotenv(ROOT / ".env")
    except UnicodeDecodeError:
        _say("the central .env is not UTF-8. Save it as UTF-8: Windows PowerShell 5.1's `>` writes UTF-16")
        return EXIT_USAGE
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a Windows console is not UTF-8 by default
    try:  # before anything runs: the config, the rules and the profiles read whole, or a sentence and exit 2
        # The config first, since every flag's default is one of its settings.
        config = load_config(CONFIG)
        args = build_parser(config["review"]).parse_args(argv)
        found = catalog()
        # One run's `--check` list is held to what a profile is, and named in the report as the flag.
        chosen = select(found, config["profiles"], args.profile, args.check)
        scope = scope_of(args.path)
        _mode_alone(args)
        explanation = explained(found, config["profiles"], args.explain) if args.explain else None
        previous = read_previous(args.previous) if args.previous else (None, None)
        for flag, given in (("--json", args.json), ("--handoff", args.handoff)):
            if given:
                _cleared(flag, given)
    except ValueError as exc:
        _say(str(exc))
        return EXIT_USAGE
    if args.list_checks:
        print(render_checks(table(found, config["profiles"])))
        return EXIT_CLEAN
    if explanation is not None:
        print(render_explained(explanation), end="")
        return EXIT_CLEAN
    if args.try_paths:
        return _try(args.try_paths, chosen, args.fail_on)
    return _reviewed(args, chosen, scope, previous)


def _mode_alone(args: argparse.Namespace) -> None:
    """A mode besides a review reads none of a review's own flags: measured, `--explain` beside `--json` cleared the
    file, then wrote nothing and exited 0. `--try` reads files on disk, and takes no repository either."""
    stops = next((flag for dest, flag in MODES.items() if getattr(args, dest)), None)
    if stops is None:
        return
    if given := [flag for dest, flag in REVIEW_ONLY.items() if getattr(args, dest)]:
        raise ValueError(f"{given[0]} is a review's, and {stops} runs no review")
    if stops == "--try" and args.repo is not None:
        raise ValueError("--try reads files on disk: it takes no repository")


def _compared(args: argparse.Namespace) -> tuple[str, str | None, str]:
    """The repository, and the base and head compared in it, as the flags name them or a person at a terminal
    answers; `ValueError` or `GitError` for what cannot be reviewed."""
    repo = repository(args.repo)
    if args.cache and Path(args.cache).expanduser().resolve().is_relative_to(Path(repo).resolve()):
        raise ValueError(f"--cache {args.cache} lies in the repository under review: nothing is written into it")
    if args.staged and args.head:
        raise ValueError("--staged reviews the index. It takes no --head")
    base, head = ("HEAD", "the index") if args.staged else target(repo, args.base, args.head, args.all)
    return repo, base, head


def _reviewed(args: argparse.Namespace, chosen: Selection, scope: tuple[str, ...], previous: Previous) -> int:
    """A review. The engine — ADK, and the judge's agents, graph, plugins, run and verifier — is imported where a
    guideline runs, or a flag of the engine's own asks for its run, and nowhere else: measured, best of seven,
    `--list-checks` started in 0.75 s with it imported first and 0.13 s without, `--try` in 0.73 s and 0.15 s; and best
    of three by the installed command, the demo's rules-only review took 0.87 to 0.94 s, 0.41 s of it the engine
    imported for a graph holding no agent, which the pre-push hook paid on every push."""
    guidelines = tuple(guideline.check_id for guideline in chosen.guidelines)
    # Only a guideline calls a model: a profile without one needs no key, and the verifier has no guideline finding to
    # judge.
    verify = args.verify and bool(guidelines)
    why, model = _arm(verify, chosen.guidelines) if guidelines else (None, "")
    if why is not None and not args.dry_run:  # a dry run reports an arm that is not ready; a review stops at it
        _say(why)
        return EXIT_USAGE
    try:
        repo, base, head = _compared(args)
    except (ValueError, GitError) as exc:
        _say(str(exc))
        return EXIT_USAGE
    # Told at a terminal, where a person waits, or when asked; a pipeline's log stays the report alone.
    say = None if args.quiet else _say if args.progress or _terminal(sys.stderr) else None
    if say is not None:
        say(f"reading {head} against {base or 'the empty tree'}")
    with _beating(say, lambda: "reading the change"):
        evidence = collect_evidence(
            repo,
            base,
            head,
            scope,
            staged=args.staged,
            **chosen.head_reading(),
            diff_cap=args.diff_cap,
            skip=args.skip,
            scan=args.scan,
            third_party=args.third_party,
        )
    if evidence["status"] != "success":
        # Measured: a first review of a repository checked out on main ended at "adds nothing" with no way on. The
        # flags are the command's, so the command names them; the index takes neither.
        widen = f". Name the branch to review with --head. Or review every file at {head!r} with --all, not --base"
        _say(evidence["error_message"] + (widen if evidence.get("adds_nothing") and not args.staged else ""))
        return EXIT_USAGE
    if say is not None:
        say(f"read {len(evidence['files'])} file(s), {len(evidence.get('ignored', ()))} ignored")
    # What every report says of the review itself, a dry run's too: with no guideline there is no model to name; an
    # arm that is not ready names no model either: `why` says why.
    shown = {"model": model, "profile": chosen.name, "off": chosen.off, "checks": chosen.ids()}
    if args.dry_run:
        unread = not_read(evidence["files"], guidelines)
        print(
            render_dry_run(
                evidence, **shown, guidelines=guidelines, ready=why or "ready", unread=unread, config=str(CONFIG)
            )
        )
        return EXIT_CLEAN
    return _decided(args, chosen, evidence, shown, previous, say)


def _arm(verify: bool, guidelines: Sequence[Guideline] = ()) -> tuple[str | None, str]:
    """Whether the arm the guidelines call is ready, and the model the report names where it is: the verifier's own
    model under --verify, beside everyone's, every model the run would build checked before anything is spent. The
    engine's first import, made only where a guideline runs."""
    from .judge.config import require_ready, served
    from .judge.verify import VERIFIER

    roles = ((VERIFIER,) if verify else ()) + tuple(guideline.id for guideline in guidelines)
    presets = {guideline.id: guideline.models for guideline in guidelines}
    why = require_ready(roles=roles, presets=presets)
    return why, served(roles, presets) if why is None else ""


def _decided(
    args: argparse.Namespace,
    chosen: Selection,
    evidence: Evidence,
    shown: dict,
    previous: Previous,
    say: Callable[[str], None] | None,
) -> int:
    """The review decided and reported: served from the cache where one holds it; else the rules, and the guidelines
    where any runs. `--trace`, `--debug-log` and `--record` are the engine's own and bring its run too."""
    cached = _cached_at(args, chosen, evidence, shown["model"]) if args.cache else None
    if cached is not None and (hit := _from_cache(cached)) is not None:
        if not args.quiet:
            _say(f"served from the cache: {cached.name}, a review of these commits with these checks and models")
        return _delivered(args, hit["report"], hit["handoff"], hit["data"], hit["code"])
    # Under --all the files are the project: its measures, read before the checks node lets the texts and tree go.
    health = measured(evidence["files"], evidence["tree"]) if evidence["whole"] else None
    engine = bool(chosen.guidelines) or any(getattr(args, flag) for flag in ENGINE)
    judged = _judged(args, chosen, evidence, say) if engine else _ruled(args, chosen, evidence, say)
    return _reported(args, evidence, *judged, shown, previous, health, cached, tidies=chosen.tidies())


def _ruled(
    args: argparse.Namespace, chosen: Selection, evidence: Evidence, say: Callable[[str], None] | None
) -> tuple[Outcome, dict, dict[str, float]]:
    """A profile with no guideline: every rule run as `--try` runs them, no engine imported, no model to name, nothing
    spent, and no agent timed, as the report says of such a review either way."""
    if say is not None:
        say(f"running {len(chosen.rules)} rule(s)")
    began = time.monotonic()
    with _beating(say, lambda: "running the rules"):
        rules, holes = from_rules(run_rules(evidence, chosen.rules))
    if say is not None:
        say(f"rules done in {time.monotonic() - began:.1f} s")
    outcome = Outcome(unread=not_read(evidence["files"], ()), errors=holes)
    fold(outcome, rules, chosen.ids(), chosen.tidies())
    return outcome, unspent(args.max_tokens or None), {}


def _judged(
    args: argparse.Namespace, chosen: Selection, evidence: Evidence, say: Callable[[str], None] | None
) -> tuple[Outcome, dict, dict[str, float]]:
    """The rules, then the guidelines, over a session that starts with the change they read, in no event a log could
    carry; every guideline finishes, whatever one of them does; then the second opinion and the fold. The checks node
    drops the tree and the texts once the rules have read them; a ceiling of 0 is none. One event loop for the run
    and its second opinion: `asyncio.run` makes a loop and closes it, and a client a library kept from the
    guidelines' calls met a closed loop at the verifiers', as ADK's cached Gemini client did
    (google/adk-python#5538)."""
    import asyncio

    from google.adk.runners import InMemoryRunner

    from .judge.agents import DIFF, IGNORED
    from .judge.graph import build_app
    from .judge.plugins import ONE_TURN, Progress, accounted
    from .judge.run import read_checks, read_guidelines, run_seeded
    from .judge.verify import second_opinion

    guidelines = tuple(guideline.check_id for guideline in chosen.guidelines)
    verify = args.verify and bool(guidelines)  # the verifier has no guideline finding to judge without one
    app = build_app(
        chat=False,
        checks=chosen,
        evidence=evidence,
        ceiling=args.max_tokens or None,
        say=say,
        debug_log=args.debug_log,
        record=args.record,
    )
    if args.trace:
        _traced(args.trace)
    runner = InMemoryRunner(app=app)
    progress = next(plugin for plugin in app.plugins if isinstance(plugin, Progress))
    with asyncio.Runner() as loop:
        with _beating(say, lambda: _waiting(progress.running())):
            run = loop.run(
                run_seeded(
                    runner,
                    session_id="run",
                    state={DIFF: evidence["diff"], IGNORED: evidence.get("ignored", [])},
                    prompt=ONE_TURN,
                )
            )
        rules, holes = read_checks(run, chosen.rules)
        outcome = read_guidelines(run, evidence["files"], guidelines, holes)
        if verify:
            with _beating(say, lambda: "verifying the findings"):
                second_opinion(outcome, rules, evidence["diff"], app.plugins, loop.run, chosen.tidies())
    outcome.redacted, usage, seconds = accounted(app.plugins)
    fold(outcome, rules, chosen.ids(), chosen.tidies())
    return outcome, usage, seconds


def _reported(
    args: argparse.Namespace,
    evidence: Evidence,
    outcome: Outcome,
    usage: dict,
    seconds: dict[str, float],
    shown: dict,
    previous: Previous,
    health: Health | None,
    cached: Path | None,
    *,
    tidies: frozenset[str],
) -> int:
    """The verdict, decided by code, and the review's reports: the text one always, the hand-off and the JSON where
    asked for or kept, and the whole kept where a cache is named and the review has no hole and no finding a verifier
    could not judge. `tidies` are the checks whose findings the hand-off lists apart, as cleanup."""
    seen, seen_health = previous
    verdict, code = decide(outcome, args.fail_on)
    report = render(
        evidence,
        outcome,
        verdict=verdict,
        usage=usage,
        fail_on=args.fail_on,
        previous=seen,
        health=health,
        previous_health=seen_health,
        **shown,
    )
    # A review with a hole is never kept, nor one with a finding no verifier could judge: seen live, a verifier met
    # Copilot's 429 and the review approved a major no verifier judged, which the cache would have served again
    # without asking. The next run looks again.
    keep = cached is not None and code in (EXIT_CLEAN, EXIT_FINDINGS) and not outcome.unverified
    # The hand-off and the JSON only where asked for, or kept: measured by review in-process, each costs about the
    # report's own time, 0.9 and 0.2 times it on the demo and 1.6 and 1.1 times over this repository whole.
    handoff = (
        render_handoff(evidence, outcome, verdict=verdict, fail_on=args.fail_on, tidies=tidies)
        if args.handoff or keep
        else ""
    )
    data = (
        as_data(
            evidence,
            outcome,
            verdict=verdict,
            code=code,
            usage=usage,
            fail_on=args.fail_on,
            **shown,
            version=version(),
            seconds=seconds,
            previous=seen,
            health=health,
        )
        if args.json or keep
        else {}
    )
    if keep:
        stored = {"report": report, "handoff": handoff, "data": data, "code": code}
        with contextlib.suppress(OSError):  # a cache that cannot be written costs the next run, never this one
            cached.parent.mkdir(mode=0o700, parents=True, exist_ok=True)  # the operator's alone: it quotes the code
            _written(cached, json.dumps(stored, ensure_ascii=False))
    return _delivered(args, report, handoff, data, code)


def _delivered(args: argparse.Namespace, report: str, handoff: str, data: dict, code: int) -> int:
    """The review's outputs: the report on stdout, the hand-off and the JSON where asked for; exit 3 where a file
    asked for cannot be written. Under `--quiet` a review that passes prints nothing."""
    if not (args.quiet and code == EXIT_CLEAN):
        print(report)
    for flag, written, text in (
        ("hand-off", args.handoff, lambda: handoff),
        ("JSON report", args.json, lambda: json.dumps(data, indent=2, ensure_ascii=False) + "\n"),
    ):
        if not written:  # measured by review: the JSON was dumped whole for a kept review that wrote none
            continue
        try:
            _written(Path(written).expanduser(), text())
        except OSError as exc:  # the folder was there when the review began; a full or read-only disk is not
            _say(f"could not write the {flag} to {written}: {exc.strerror or exc}")
            return EXIT_DEGRADED
    return code


@contextlib.contextmanager
def _beating(say: Callable[[str], None] | None, what: Callable[[], str]) -> Generator[None]:
    """Tell `say` what the review still does, every `HEARTBEAT_SECONDS` while the block runs. Asked by the owner: a
    person at a terminal must never read a long model call as a hang."""
    if say is None:
        yield
        return
    done, began = threading.Event(), time.monotonic()

    def beat() -> None:
        while not done.wait(HEARTBEAT_SECONDS):
            say(f"still {what()}: {time.monotonic() - began:.0f} s")

    thread = threading.Thread(target=beat, name="heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()


def _waiting(running: Sequence[str]) -> str:
    """What a person waits on while the graph runs: the guidelines still running, else the graph's own work."""
    return f"waiting on {', '.join(running)}" if running else "working"


def _cached_at(args: argparse.Namespace, chosen: Selection, evidence: Evidence, model: str) -> Path:
    """Where a decided review of these inputs is kept: a file named by a hash of everything its result depends on —
    the reviewer's own code and its config, whatever their version says, the commits, what of them is covered,
    the checks and every setting, each model a call would go to, and each outside tool a rule runs. A missing input
    would serve a stale approval."""
    from .collect.tools import IDENTITIES
    from .judge.settings import fallback_name

    digest = hashlib.sha256()
    tools = sorted({rule.tool for rule in chosen.rules if isinstance(rule, FileRule) and rule.tool})
    here = Path(__file__).parent
    for path in sorted(here.rglob("*")):
        if path.suffix in (".py", ".toml") and "__pycache__" not in path.parts:
            digest.update(path.relative_to(here).as_posix().encode() + b"\0" + path.read_bytes())
    # What was read, byte for byte, a file at a time: the index has no commit to name it by, and a hash of a commit's
    # diffs costs nothing beside the review it would spare. Joined first, the diffs were copied twice at once.
    diffs = hashlib.sha256()
    for f in (*evidence["files"], *evidence.get("unscanned", ())):
        diffs.update(unreleased(f["diff"]).encode())
        diffs.update(b"\0")
    pre = evidence["preflight"]
    said = {
        **{key: pre.get(key) for key in ("repo", "head", "head_sha", "base", "base_sha", "merge_base")},
        "scope": evidence["scope"],
        "whole": evidence["whole"],
        "profile": chosen.name,
        "checks": list(chosen.ids()),
        # Everything the run was asked but where its outputs go: a flag added later is in the key from the start.
        "asked": {key: value for key, value in sorted(vars(args).items()) if key not in OUTPUTS},
        "untracked": evidence.get("untracked"),  # `--staged`'s note: a new untracked file is a new report
        "ignored": evidence.get("ignored"),  # read by review: a staged change to a vendored file served a stale list
        "models": [model, fallback_name()],  # the verifier's among `model`'s roles under --verify
        # Each outside tool a rule that runs reads, as it would run: a newer ruff finds what an older one did not.
        "tools": {tool: IDENTITIES[tool]() for tool in tools},
        "previous": Path(args.previous).expanduser().read_bytes().hex() if args.previous else "",
        "diffs": diffs.hexdigest(),
    }
    digest.update(json.dumps(said, sort_keys=True, default=str).encode())
    return Path(args.cache).expanduser() / f"{digest.hexdigest()[:32]}.json"


def _from_cache(path: Path) -> dict | None:
    """A kept review, or None: nothing kept, or a file that is not one, which is reviewed afresh, never trusted."""
    try:
        hit = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):  # RecursionError: nested past what `json.loads` reads
        return None
    shape = {"report": str, "handoff": str, "data": dict, "code": int}
    fits = isinstance(hit, dict) and all(isinstance(hit.get(key), kind) for key, kind in shape.items())
    return hit if fits and hit["code"] in (EXIT_CLEAN, EXIT_FINDINGS) else None


def main(argv: list[str] | None = None) -> int:
    """The command, with a last resort: an error nothing above named ends as one sentence and exit 3, a review that
    did not finish, never Python's own exit 1, which is the code for "a blocker was found". Measured before: errors
    found and named one at a time, six recorded in the design record and a seventh by review, each ended so. Ctrl-C
    ends as one sentence and the shell's own code for it, 130; a reader that closes the pipe early, as `| head` does,
    as one sentence and exit 3, the report undelivered. Measured before: a traceback and 130, and exit 3 or Python's
    own 120 for a closed pipe. A reviewer that could not load says so, exit 3."""
    if UNLOADED is not None:
        print(f"review: the reviewer could not load ({UNLOADED}). Reinstall it with `uv sync`", file=sys.stderr)
        return 3  # EXIT_DEGRADED, which the imports that failed would have named
    try:
        return _review(argv)
    except KeyboardInterrupt:
        _say("interrupted: the review did not finish")
        return EXIT_INTERRUPTED
    except Terminated:
        _say("terminated: the review did not finish")
        return EXIT_TERMINATED
    except BrokenPipeError:
        with contextlib.suppress(OSError, ValueError):  # Python flushes stdout at exit, into the same closed pipe
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        _say("the report's reader closed it before its end. It never got the verdict")
        return EXIT_DEGRADED
    except Exception as exc:  # noqa: BLE001 -- a crash must never read as a verdict
        _say(f"the review did not finish: {type(exc).__name__}: {exc}")
        return EXIT_DEGRADED


def settle() -> None:
    """A review's process-wide settings, once a process, for each command that runs one: `run`, and the pre-push
    hook's."""
    warnings.filterwarnings("ignore", message=".*EXPERIMENTAL.*")  # ADK announces a flag on every tool declaration
    # ADK logs a failed node with its whole traceback through Python's last-resort stderr handler. The report
    # names the guideline and the reason, so that logger gets a handler that prints nothing; configured logging
    # (an operator's basicConfig) still receives every record.
    logging.getLogger("google_adk").addHandler(logging.NullHandler())
    # genai warns, on every Gemini call ADK makes, that it would rather be called another way; the same handler.
    logging.getLogger("google_genai").addHandler(logging.NullHandler())
    # A job stops a run with SIGTERM, which would end the process where it stands: ruff's copies of the branch's files
    # stayed in the temporary folder. Raised as an exception, the run lets go of what it holds, as on Ctrl-C.
    signal.signal(signal.SIGTERM, _terminated)


def run() -> int:
    """The process boundary, for `python -m adk_review.review` and the installed `adk-review` alike: the
    process-wide settings, once, then `main`, which stays a function of its arguments."""
    settle()
    return main()


if __name__ == "__main__":
    raise SystemExit(run())

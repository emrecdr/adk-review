"""How often the guidelines find what the demo plants: `uv run python scripts/measure.py [--runs N] [--pause S]
[--all] [--profile NAME | --check ID]`.

What code finds is found every run; what a guideline finds is the model's doing and varies. This builds the planted
branch, reviews it N times, and reads each review from the JSON report it writes, or, where a review decided nothing
and wrote none, as a person reads the text. Per planted defect, in how many runs
a model's check named it, kept or folded into another source's finding, and at what severity; the model's findings on
no planted defect, an upper bound on its false ones; the model's own blockers, the rules' set aside, since the rules
find the planted key every run; the verdicts across runs; the findings dropped for a quote the diff does not bear out;
the tokens and the time. Each run calls the model of the arm `.env` names, on Copilot about 5,000 tokens, and each
run's line names the model and its arm, since a measurement holds for the arm it ran on. The report names the
reviewer's commit it measured. `--all` reviews the planted branch as a whole project, every file at its head, as
`--all` does: a file's line there is its line at the head, as on the diff's new side, so the planted lines
are read the same way.

Measured before, reading the text alone: every planted defect was credited to whatever model finding sat in its lines,
six unrelated ones among them, and the exit code it counted came from the rules' blockers.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from make_demo_repo import BASE_REF, demo_repo

ROOT = Path(__file__).resolve().parents[1]
DEMO_REPO = demo_repo()
COMMAND = "adk_review.review"
#: The prefix of a guideline's check id: what a model's finding is filed under.
GUIDELINE = "guideline."
#: Each planted defect: its file and its lines, as `make_demo_repo.py` plants them.
PLANTED = {
    "shell call": ("src/payments/charge.py", range(22, 29)),
    "key pair": ("src/payments/config.py", range(4, 6)),
    "untested function": ("src/payments/refund.py", range(1, 12)),
    "copied block": ("src/payments/report.py", range(4, 6)),  # its lines hold the cents division too: either counts
}
RANK = {"minor": 1, "major": 2, "blocker": 3}
#: How long one review may run: past it, its run names no exit code and the next starts.
TIMEOUT_S = 600
#: A kept finding in a text report, `- **major** …`, ending in its `(path:line)`.
FINDING = re.compile(r"^- \*\*(?P<severity>\w+)\*\* .*\((?P<path>[^():]+):(?P<line>\d+)\)$")
#: A finding folded into the kept one above it, on the same lines: its check, under that finding.
ALSO = re.compile(r"^  - also on these lines: `(?P<source>[\w.-]+)`: ")
SPEND = re.compile(r"^- spend: \d+ model call\(s\) completed of \d+ attempted, (?P<tokens>[\d,]+) tokens")
DROPPED = "- findings dropped for evidence not in the diff: "
VERDICT = "## Verdict: "


@dataclass
class Run:
    """What one review says of the model's checks."""

    code: int | None
    seconds: float
    verdict: str = "none"
    named: dict[str, str] = field(default_factory=dict)  # each planted defect named, at the most severe reading
    by: dict[str, set[str]] = field(default_factory=dict)  # each planted defect named, the guidelines that named it
    stray: int = 0  # the model's findings on no planted defect
    blockers: int = 0  # the model's own blockers
    dropped: int = 0
    tokens: int = 0


def credit(run: Run, findings: Iterable[tuple[str, str, Iterable[int], str]]) -> None:
    """Each finding of a model's check, `(check, path, its lines, severity)`, to the planted defect whose lines it
    stands on, at its most severe reading and by the guideline that made it; one on none is stray. A folded finding's
    severity is the one it was given."""
    for check, path, lines, severity in findings:
        lines = set(lines)
        hits = [name for name, (at, planted) in PLANTED.items() if path == at and lines & set(planted)]
        for name in hits:
            run.named[name] = max(run.named.get(name, severity), severity, key=lambda s: RANK.get(s, 0))
            run.by.setdefault(name, set()).add(check)
        run.stray += not hits
        run.blockers += severity == "blocker"


def judged(data: dict) -> list[tuple[str, str, range, str]]:
    """Every finding a guideline made in the JSON report, kept or folded: its check, its file, the lines the diff
    places it on, and its severity. Which guideline made it is kept: a finding of another dimension can stand on a
    planted defect's lines, a test missing for the function that holds it."""
    found = []
    for f in (*data["findings"], *data["folded"]):
        if f["check"].startswith(GUIDELINE):
            first, last = f["lines"] or (f["line"], f["line"])
            lines = range(first, last + 1) if first is not None and last is not None else range(0)
            found.append((f["check"], f["file"], lines, f["severity"]))
    return found


def read_json(data: dict, run: Run) -> Run:
    """The JSON report: every finding a guideline made, kept or folded, placed by the lines the diff gives it."""
    credit(run, judged(data))
    run.verdict, run.dropped, run.tokens = data["verdict"], len(data["dropped"]), data["usage"]["tokens"]
    return run


def live(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """The flags every live measurement takes: how many runs, the pause between them, and what each review runs: a
    profile, the shipped default unless one is named, or each check named alone. Measured live under `full`: every
    guideline's finding stood on a small file's planted lines, so which one found a defect could not be told; a
    guideline run alone can only be its own."""
    parser.add_argument("--runs", type=int, default=3, help="reviews of each branch (each calls the model)")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds to wait between runs (a quota's worth)")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--profile", help="the profile each review runs, as the command takes it; default unless named")
    which.add_argument("--check", action="append", help="run this check alone, as the command does; repeat for more")
    return parser


def chosen(args: argparse.Namespace) -> list[str]:
    """What each review runs, as the command's own flags: each check named, or the profile named, or none, the
    default."""
    if args.check:
        return [flag for check in args.check for flag in ("--check", check)]
    return ["--profile", args.profile] if args.profile else []


def options(script: str, description: str) -> argparse.ArgumentParser:
    """The flags of a live measurement over branches it builds: `live`'s, and which branches to review, or none."""
    parser = live(argparse.ArgumentParser(prog=script, description=description))
    parser.add_argument("--build-only", action="store_true", help="build the branches, review nothing")
    parser.add_argument("--branch", action="append", help="review only this branch, by its name; repeat for more")
    return parser


def picked(parser: argparse.ArgumentParser, made: dict[str, str], names: list[str] | None) -> dict[str, str]:
    """The branches a run reviews: those `--branch` names, or every one built; a name the set does not hold is a
    usage error. Measured by review: a profile's own branches cost a third of the set, each review three to six
    calls, and Gemini's free tier allows twenty a day."""
    if unknown := sorted(set(names or ()) - set(made)):
        parser.error(f"no branch named {', '.join(unknown)}. There are {', '.join(made)}")
    return {name: made[name] for name in names} if names else made


def review_command(repo: Path, branch: str, report: Path, selection: Sequence[str], whole: bool) -> list[str]:
    """The command one run executes: the branch against the demo's base, or every file at its head where `whole`,
    its JSON to `report`, running what `selection`, `chosen`'s flags, names. Measured before: every live script
    reviewed with `default`, so a profile's own guidelines, the simplify pass's among them, had no way to be
    measured."""
    scope = ["--all"] if whole else ["--base", BASE_REF]
    return [sys.executable, "-m", COMMAND, str(repo), "--head", branch, *scope, "--json", str(report), *selection]


class Review(NamedTuple):
    """One run of one review: its exit code, None where it ran past `TIMEOUT_S`; its JSON report, None where it
    wrote none, and its text report, read where it did not; and how long it took."""

    code: int | None
    data: dict | None
    text: str
    seconds: float


#: How a review's JSON names a call its provider refused for its rate, on either arm, as live runs named one: Copilot's
#: `RateLimitError: 429: …`, Gemini's `RESOURCE_EXHAUSTED: 429: …`.
RATE_LIMITED = re.compile(r"\b(?:RateLimitError|RESOURCE_EXHAUSTED): 429\b")


def rate_limited(data: Mapping | None) -> str | None:
    """The first reason a review's JSON gives, a check's that could not look or a verifier's that could not judge, that
    names a provider's rate limit; None where none does."""
    reasons = [*((data or {}).get("could_not_look") or {}).values()]
    reasons += [item.get("reason", "") for item in (data or {}).get("unverified") or ()]
    return next((reason for reason in reasons if RATE_LIMITED.search(reason)), None)


def reviewed(
    repo: Path, branches: Mapping[str, str], args: argparse.Namespace, whole: bool = False
) -> Iterator[tuple[str, Review]]:
    """The review of each branch against the demo's base, or of every file at its head where `whole`, as `live`'s
    flags in `args` say: `--runs` times, each a call to the model, `--pause` seconds before every run but the first,
    running what `chosen` makes of `--profile` or `--check`; by branch, each run's `Review`. Every live script's runs
    are these, each held to `TIMEOUT_S`: before, only `measure.py`'s own loop was, and a review that never answered
    would have held every later run of the other two. They stop at the first run whose report names a rate limit, that
    run unread: measured live, Copilot's limit for utility models lasts hours, and each run after it sent three
    requests a guideline into it and measured nothing there."""
    selection = chosen(args)
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "review.json"
        runs = [(name, branch, number) for name, branch in branches.items() for number in range(args.runs)]
        for at, (name, branch, number) in enumerate(runs):
            if at and args.pause:
                time.sleep(args.pause)
            code, text, seconds, data = _run_once(review_command(repo, branch, report, selection, whole), report)
            # The model and its arm, as the report names them: a measurement holds for the arm it ran on.
            on = f", {data['model']}" if data and data.get("model") else ""
            print(f"{name} run {number + 1}: exit {code}{on}", flush=True)
            if limit := rate_limited(data):
                print(f"stopped at {name} run {number + 1}: {limit}; no run after it is made", flush=True)
                return
            yield name, Review(code, data, text, seconds)


def _run_once(command: list[str], report: Path) -> tuple[int | None, str, float, dict | None]:
    """One review, held to `TIMEOUT_S`: its exit code, None past the timeout, its text, its seconds, and the JSON
    report it wrote, if any, which is then removed for the next run."""
    started = time.monotonic()
    try:
        # A review's exit code is its verdict, 1 and 3 among the answers: read, never raised.
        done = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S,
            check=False,
        )
        code, text = done.returncode, done.stdout
    except subprocess.TimeoutExpired:
        code, text = None, ""
    seconds = time.monotonic() - started
    data = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    report.unlink(missing_ok=True)
    return code, text, seconds, data


def tally(counts: Counter[str]) -> str:
    """`counts`, the most first, as every live script's summary names them: `blocker x2, major x1`."""
    return ", ".join(f"{key} x{count}" for key, count in counts.most_common())


def read_text(report: str, run: Run) -> Run:
    """A text report, as a person reads it: a model's findings at the line each names, for a review that decided
    nothing and wrote no JSON."""
    source, found, place = "", [], None
    for line in report.splitlines():
        if line.startswith(VERDICT):
            run.verdict = line.removeprefix(VERDICT).split(",")[0]
        elif line.startswith("### "):
            source = line[4:].split(" ")[0]
        elif hit := FINDING.match(line):
            place = (hit["path"], int(hit["line"]))  # where a finding folded into this one stands too
            if source.startswith(GUIDELINE):
                found.append((source, place[0], [place[1]], hit["severity"]))
        elif (also := ALSO.match(line)) and place and also["source"].startswith(GUIDELINE):
            found.append((also["source"], place[0], [place[1]], "folded"))
        elif line.startswith(DROPPED):
            run.dropped = int(line.removeprefix(DROPPED))
        elif spent := SPEND.match(line):
            run.tokens = int(spent["tokens"].replace(",", ""))
    credit(run, found)
    return run


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a Windows console is not UTF-8 by default
    parser = live(
        argparse.ArgumentParser(prog="measure", description="How often the guidelines find what the demo plants.")
    )
    parser.add_argument("--all", action="store_true", help="review every file at the head, not the change")
    args = parser.parse_args(argv)
    subprocess.run([sys.executable, "scripts/make_demo_repo.py"], cwd=ROOT, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        encoding="utf-8",
        check=False,  # a commit the report cannot name is said so, below
    )
    who = "a guideline"
    runs: list[Run] = []
    planted = {"feature/payments": "feature/payments"}
    selection = chosen(args)
    for _name, review in reviewed(DEMO_REPO, planted, args, whole=args.all):
        run = Run(review.code, review.seconds)
        # A review that decided nothing wrote no JSON, and is read as a person reads its text.
        runs.append(read_json(review.data, run) if review.data else read_text(review.text, run))
        found = ", ".join(f"{name} ({severity})" for name, severity in sorted(run.named.items())) or "nothing"
        print(f"  {run.verdict}; {who} named {found}; {run.stray} elsewhere", flush=True)
    total = len(runs)
    what = "the planted branch as a whole project" if args.all else "the planted branch"
    under = f", under {' '.join(selection)}" if selection else ""
    print(f"\nadk-review at {head.stdout.strip() or 'an unknown commit'}, {total} runs of {what}{under}")
    for name in PLANTED:
        readings = Counter(run.named[name] for run in runs if name in run.named)
        said = tally(readings)
        by = tally(Counter(check for run in runs for check in run.by.get(name, ())))
        print(
            f"- {name}: named by {who} in {sum(readings.values())} of {total}"
            + (f", as {said}" if said else "")
            + (f"; by {by}" if by else "")
        )
    print(f"- {who}'s findings on no planted defect, an upper bound on its false ones: {sum(r.stray for r in runs)}")
    print(f"- {who}'s own blocker, the rules' set aside: in {sum(r.blockers > 0 for r in runs)} of {total} runs")
    verdicts = Counter(run.verdict for run in runs)
    print(f"- verdicts: {tally(verdicts)}")
    print(f"- findings dropped for a quote the diff does not bear out: {sum(run.dropped for run in runs)} in all")
    tokens, seconds = sum(run.tokens for run in runs) // max(total, 1), sum(run.seconds for run in runs) / max(total, 1)
    print(f"- a run: {tokens:,} tokens, {seconds:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

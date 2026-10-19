"""How often the guidelines find what the demo plants: `uv run python scripts/measure.py [--runs N] [--pause S]
[--all]`.

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
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

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
#: A finding in a text report, kept (`- **major** …`) or folded into another's, ending in its `(path:line)`.
FINDING = re.compile(
    r"^- (?:\*\*(?P<severity>\w+)\*\* |folded into `[\w.-]+`'s finding on the same lines: )"
    r".*\((?P<path>[^():]+):(?P<line>\d+)\)$"
)
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
    stray: int = 0  # the model's findings on no planted defect
    blockers: int = 0  # the model's own blockers
    dropped: int = 0
    tokens: int = 0


def credit(run: Run, findings: Iterable[tuple[str, Iterable[int], str]]) -> None:
    """Each finding of a model's check, `(path, its lines, severity)`, to the planted defect whose lines it stands
    on, at its most severe reading; one on none is stray. A folded finding's severity is the one it was given."""
    for path, lines, severity in findings:
        lines = set(lines)
        hits = [name for name, (at, planted) in PLANTED.items() if path == at and lines & set(planted)]
        for name in hits:
            run.named[name] = max(run.named.get(name, severity), severity, key=lambda s: RANK.get(s, 0))
        run.stray += not hits
        run.blockers += severity == "blocker"


def judged(data: dict) -> list[tuple[str, range, str]]:
    """Every finding a guideline made in the JSON report, kept or folded: its file, the lines the diff places it on,
    and its severity."""
    return [(f["file"], placed(f), f["severity"]) for f in (*data["findings"], *data["folded"]) if by_guideline(f)]


def by_guideline(finding: dict) -> bool:
    """Whether a finding of the JSON report is a guideline's, a model's reading, and not a rule's."""
    return finding["check"].startswith(GUIDELINE)


def placed(finding: dict) -> range:
    """The lines the diff places a finding of the JSON report on, or none where it names no line."""
    first, last = finding["lines"] or (finding["line"], finding["line"])
    return range(first, last + 1) if first is not None and last is not None else range(0)


def read_json(data: dict, run: Run) -> Run:
    """The JSON report: every finding a guideline made, kept or folded, placed by the lines the diff gives it."""
    credit(run, judged(data))
    run.verdict, run.dropped, run.tokens = data["verdict"], len(data["dropped"]), data["usage"]["tokens"]
    return run


def options(script: str, description: str) -> argparse.ArgumentParser:
    """The flags of a live measurement over branches it builds: how many runs, the pause between them, or none, and
    the profile each review runs, the shipped default unless one is named."""
    parser = argparse.ArgumentParser(prog=script, description=description)
    parser.add_argument("--runs", type=int, default=3, help="reviews of each branch (each calls the model)")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds to wait between runs (a quota's worth)")
    parser.add_argument("--build-only", action="store_true", help="build the branches, review nothing")
    parser.add_argument("--profile", help="the profile each review runs, as the command takes it; default unless named")
    return parser


def review_command(repo: Path, branch: str, report: Path, profile: str | None = None) -> list[str]:
    """The command one run executes: the branch against the demo's base, its JSON to `report`, under `profile` where
    one is named. Measured before: every live script reviewed with `default`, so a profile's own guidelines, the
    simplify pass's among them, had no way to be measured."""
    command = [sys.executable, "-m", COMMAND, str(repo), "--head", branch, "--base", BASE_REF, "--json", str(report)]
    return [*command, "--profile", profile] if profile else command


def reviewed(
    repo: Path, branches: Mapping[str, str], runs: int, pause: float, profile: str | None = None
) -> Iterator[tuple[str, int, dict | None]]:
    """The review of each branch against the demo's base, `runs` times, each a call to the model: by branch, the
    run's exit code and its JSON report, None where it wrote none; `pause` seconds before every run but the first;
    under `profile` where one is named."""
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "review.json"
        first = True
        for name, branch in branches.items():
            for number in range(runs):
                if not first and pause:
                    time.sleep(pause)
                first = False
                command = review_command(repo, branch, report, profile)
                done = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")
                data = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
                report.unlink(missing_ok=True)
                # The model and its arm, as the report names them: a measurement holds for the arm it ran on.
                on = f", {data['model']}" if data and data.get("model") else ""
                print(f"{name} run {number + 1}: exit {done.returncode}{on}", flush=True)
                yield name, done.returncode, data


def read_text(report: str, run: Run) -> Run:
    """A text report, as a person reads it: a model's findings at the line each names, for a review that decided
    nothing and wrote no JSON."""
    source, found = "", []
    for line in report.splitlines():
        if line.startswith(VERDICT):
            run.verdict = line.removeprefix(VERDICT).split(",")[0]
        elif line.startswith("### "):
            source = line[4:].split(" ")[0]
        elif (hit := FINDING.match(line)) and source.startswith(GUIDELINE):
            found.append((hit["path"], [int(hit["line"])], hit["severity"] or "folded"))
        elif line.startswith(DROPPED):
            run.dropped = int(line.removeprefix(DROPPED))
        elif spent := SPEND.match(line):
            run.tokens = int(spent["tokens"].replace(",", ""))
    credit(run, found)
    return run


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a Windows console is not UTF-8 by default
    parser = argparse.ArgumentParser(prog="measure", description="How often the guidelines find what the demo plants.")
    parser.add_argument("--runs", type=int, default=3, help="reviews of the planted branch (each calls the model)")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds to wait between runs (a quota's worth)")
    parser.add_argument("--all", action="store_true", help="review every file at the head, not the change")
    parser.add_argument("--profile", help="the profile each review runs, as the command takes it; default unless named")
    args = parser.parse_args(argv)
    subprocess.run([sys.executable, "scripts/make_demo_repo.py"], cwd=ROOT, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        encoding="utf-8",
    )
    command = [sys.executable, "-m", COMMAND, str(DEMO_REPO), "--head", "feature/payments"]
    command += ["--all"] if args.all else ["--base", "main"]
    command += ["--profile", args.profile] if args.profile else []
    who = "a guideline"
    runs: list[Run] = []
    with tempfile.TemporaryDirectory() as scratch:
        report_json = Path(scratch) / "review.json"
        for number in range(args.runs):
            if number and args.pause:
                time.sleep(args.pause)
            started, written = time.monotonic(), ["--json", str(report_json)]
            try:
                done = subprocess.run(
                    command + written, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace", timeout=600
                )
                code, report = done.returncode, done.stdout
            except subprocess.TimeoutExpired:
                code, report = None, ""
            run = Run(code, time.monotonic() - started)
            if report_json.is_file():
                runs.append(read_json(json.loads(report_json.read_text(encoding="utf-8")), run))
                report_json.unlink()
            else:  # a review that decided nothing and wrote no JSON
                runs.append(read_text(report, run))
            found = ", ".join(f"{name} ({severity})" for name, severity in sorted(run.named.items())) or "nothing"
            print(
                f"run {number + 1}: {run.verdict} (exit {code}); {who} named {found}; {run.stray} elsewhere", flush=True
            )
    total = len(runs)
    what = "the planted branch as a whole project" if args.all else "the planted branch"
    print(f"\nadk-review at {head.stdout.strip() or 'an unknown commit'}, {total} runs of {what}")
    for name in PLANTED:
        readings = Counter(run.named[name] for run in runs if name in run.named)
        said = ", ".join(f"{severity} x{count}" for severity, count in readings.most_common())
        print(f"- {name}: named by {who} in {sum(readings.values())} of {total}" + (f", as {said}" if said else ""))
    print(f"- {who}'s findings on no planted defect, an upper bound on its false ones: {sum(r.stray for r in runs)}")
    print(f"- {who}'s own blocker, the rules' set aside: in {sum(r.blockers > 0 for r in runs)} of {total} runs")
    verdicts = Counter(run.verdict for run in runs)
    print(f"- verdicts: {', '.join(f'{verdict} x{count}' for verdict, count in verdicts.most_common())}")
    print(f"- findings dropped for a quote the diff does not bear out: {sum(run.dropped for run in runs)} in all")
    tokens, seconds = sum(run.tokens for run in runs) // max(total, 1), sum(run.seconds for run in runs) / max(total, 1)
    print(f"- a run: {tokens:,} tokens, {seconds:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

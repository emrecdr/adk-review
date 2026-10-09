"""A labelled set: branches each holding one known defect, or none, to measure what the guidelines find and what they
invent. `uv run python scripts/labelled_set.py [--runs K] [--pause S] [--build-only]`.

Matching findings to known facts is reliable, at kappa 70.6 to 86.7, where scoring usefulness is not. Each branch is off
the demo's `main`: a modification that builds SQL from input, a loop that misses its last item, a function with no
test, a long file whose defect sits past the guidelines' cut, a container that runs privileged, and two clean
changes. Each is reviewed K times with the default profile; a labelled branch scores the runs in which a guideline
named a finding on the defect's lines, a clean branch the runs a guideline made a finding at major or above, a false
one. Injection attempts are `scripts/injection_suite.py`'s. Each review calls the model: run it only when a live
measurement is wanted; `--build-only` builds the branches and calls nothing.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from make_demo_repo import BASE_REF, CHARGE, _git, branch, build
from measure import judged, options, reviewed

#: Each branch: the files it writes over the demo's `main`, and its label — the file and the lines of its one
#: defect, or None for a clean change. A guideline names the defect when a finding of its stands on those lines.
LONG = "".join(f"def step_{n}(value: int) -> int:\n    return value + {n}\n\n\n" for n in range(120))
BRANCHES = {
    "sql-from-input": (
        {"src/payments/charge.py": CHARGE + '''

def find_charges(db, customer: str):
    return db.execute(f"SELECT * FROM charges WHERE customer = '{customer}'")
'''},
        ("src/payments/charge.py", range(23, 25)),
    ),
    "misses-the-last": (
        {"src/payments/report.py": '''"""The daily report."""


def total(charges: list[int]) -> int:
    result = 0
    for index in range(len(charges) - 1):
        result += charges[index]
    return result
'''},
        ("src/payments/report.py", range(5, 8)),
    ),
    "untested-function": (
        {"src/payments/refund.py": '''"""Refunds."""


def refund(amount_cents: int, refunded_cents: int) -> int:
    if refunded_cents > amount_cents:
        raise ValueError("refund larger than the charge")
    return amount_cents - refunded_cents
'''},
        ("src/payments/refund.py", range(4, 8)),
    ),
    "long-file": (
        {"src/payments/steps.py": '"""Steps."""\n\n\n' + LONG + '''def run(command: str) -> int:
    import subprocess

    return subprocess.run(command, shell=True, check=False).returncode
'''},
        ("src/payments/steps.py", range(484, 488)),
    ),
    "privileged-container": (
        {"deploy/pod.yaml": '''apiVersion: v1
kind: Pod
metadata:
  name: payments
spec:
  containers:
    - name: payments
      image: payments:latest
      securityContext:
        privileged: true
'''},
        ("deploy/pod.yaml", range(9, 11)),
    ),
    "clean-docs": ({"README.md": "# payments (demo)\n\nA tiny service the ADK demo reviews, in euro cents.\n"}, None),
    "clean-rename": (
        {"src/payments/charge.py": CHARGE.replace("def format_amount(amount_cents: int", "def format_amount(cents: int")
         .replace("{amount_cents / 100:.2f}", "{cents / 100:.2f}")},
        None,
    ),
}  # fmt: skip


def branches(repo: Path) -> dict[str, str]:
    """Build the demo at `repo` and one branch a label, each off `main`: by label, the branch to review."""
    build(repo)
    made = {}
    for name, (files, _label) in BRANCHES.items():
        made[name] = f"label/{name}"
        branch(repo, made[name], BASE_REF, files, f"label: {name}")
    _git(repo, "checkout", "-q", BASE_REF)
    return made


def scored(name: str, data: dict) -> bool:
    """One run of one branch: a labelled branch's defect named by a guideline, or a clean branch given a finding at
    major or above, a false one."""
    label = BRANCHES[name][1]
    found = judged(data)
    if label is None:
        return any(severity in ("major", "blocker") for _file, _lines, severity in found)
    path, lines = label
    return any(file == path and set(at) & set(lines) for file, at, _severity in found)


def table(results: dict[str, list[bool]]) -> list[str]:
    """Each branch's line: a defect named in N of K runs, or a clean change given a false finding in N of K."""
    lines = ["| branch | label | runs |", "|---|---|---|"]
    for name, hits in results.items():
        what = "clean: a false finding" if BRANCHES[name][1] is None else "its defect named"
        lines.append(f"| {name} | {what} | {sum(hits)} of {len(hits)} |")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = options("labelled_set", (__doc__ or "").splitlines()[0]).parse_args(argv)
    repo = Path(tempfile.gettempdir()) / "adk-labelled-repo"
    made = branches(repo)
    print(f"built {len(made)} branches in {repo}: {', '.join(made.values())}", flush=True)
    if args.build_only:
        return 0
    results: dict[str, list[bool]] = {}
    for name, _code, data in reviewed(repo, made, args.runs, args.pause):
        results.setdefault(name, []).append(scored(name, data) if data else False)
    print("\n".join(table(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

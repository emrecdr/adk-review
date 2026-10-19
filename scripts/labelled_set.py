"""A labelled set: branches each holding one known defect, or none, to measure what the guidelines find and what they
invent. `uv run python scripts/labelled_set.py [--runs K] [--pause S] [--profile NAME] [--branch NAME] [--build-only]`.

Matching findings to known facts is reliable, at kappa 70.6 to 86.7, where scoring usefulness is not. Each branch is off
the demo's `main`: a modification that builds SQL from input, a loop that misses its last item, a function with no test,
a long file whose defect sits past the guidelines' cut, a container that runs privileged, a file read again for each
item of a loop, a shared function patched for one caller's input, a function copied with one value changed, and two
clean changes. Each is reviewed K times, under `--profile` where one is named and the default profile otherwise: the
last three are what `simplify` and `code-review` add guidelines for. A labelled branch scores the runs in which a
guideline named a finding on the defect's lines; a clean branch scores the runs a guideline made a finding at major
or above, a false one; each says which guidelines did. Injection attempts are `scripts/injection_suite.py`'s. Each
review calls the model: run it only when a live measurement is wanted; `--build-only` builds the branches and calls
nothing.
"""

from __future__ import annotations

import tempfile
import tomllib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from make_demo_repo import BASE_REF, CHARGE, _git, branch, build
from measure import ROOT, judged, options, picked, reviewed

#: The characters of a file's diff a guideline reads, as the shipped config sets them: each review here runs the
#: command as it ships.
CAP = tomllib.loads((ROOT / "adk_review" / "config.toml").read_text(encoding="utf-8"))["review"]["diff_cap"]
#: Functions enough that the long file's defect lies past that cap, some fifty characters and four lines each.
STEPS = CAP // 32
LONG = "".join(f"def step_{n}(value: int) -> int:\n    return value + {n}\n\n\n" for n in range(STEPS))
#: Each branch: the files it writes over the demo's `main`, and its label — the file and the lines of its one
#: defect, or None for a clean change. A guideline names the defect when a finding of its stands on those lines.
BRANCHES: dict[str, tuple[dict[str, str], tuple[str, range] | None]] = {
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
        ("src/payments/steps.py", range(4 + 4 * STEPS, 8 + 4 * STEPS)),  # past the docstring, two blanks and the steps
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
    "repeated-io": (
        {"src/payments/ledger.py": '''"""Totals by customer, from the ledger file."""

import json
from pathlib import Path


def totals(customers: list[str], ledger: Path) -> dict[str, int]:
    result = {}
    for customer in customers:
        charges = json.loads(ledger.read_text(encoding="utf-8"))
        result[customer] = sum(c["amount_cents"] for c in charges if c["customer"] == customer)
    return result
'''},
        ("src/payments/ledger.py", range(9, 11)),
    ),
    "special-case": (
        {"src/payments/charge.py": CHARGE.replace(
            "def format_amount(amount_cents: int, currency: str) -> str:\n",
            "def format_amount(amount_cents: int, currency: str) -> str:\n"
            '    if currency == "eur":  # the import job sends lower-case codes, and the receipts showed "eur"\n'
            '        currency = "EUR"\n',
        )},
        ("src/payments/charge.py", range(19, 21)),
    ),
    "copied-block": (
        {"src/payments/totals.py": '''"""Totals by currency."""


def eur_total(amounts: list[tuple[int, str]]) -> int:
    total = 0
    for amount_cents, currency in amounts:
        if currency == "EUR":
            total += amount_cents
    return total


def usd_total(amounts: list[tuple[int, str]]) -> int:
    total = 0
    for amount_cents, currency in amounts:
        if currency == "USD":
            total += amount_cents
    return total
'''},
        ("src/payments/totals.py", range(4, 18)),
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


def named_by(name: str, data: dict) -> set[str]:
    """The guidelines a run scores by: on a labelled branch, those whose findings, kept or folded, stand on its
    defect's lines; on a clean one, those that made a finding at major or above, a false one. Measured live on Copilot:
    a clean rename drew a false major in 3 of 3 runs under `full`, and which guideline made it was unknown. A finding
    stands on a defect's lines whatever it says of them: one quoting a small file's whole function, a test missing for
    it say, stands on every line, so on such a branch every guideline can name the defect."""
    label = BRANCHES[name][1]
    found = judged(data)
    if label is None:
        return {check for check, _file, _at, severity in found if severity in ("major", "blocker")}
    path, lines = label
    return {check for check, file, at, _severity in found if file == path and set(at) & set(lines)}


def scored(name: str, data: dict) -> bool:
    """One run of one branch: a labelled branch's defect named by a guideline, or a clean branch given a finding at
    major or above, a false one."""
    return bool(named_by(name, data))


def table(results: dict[str, list[bool]], by: Mapping[str, Counter[str]]) -> list[str]:
    """Each branch's line: a defect named in N of K runs, or a clean change given a false finding in N of K, and by
    which guidelines in how many."""
    lines = ["| branch | label | runs | by |", "|---|---|---|---|"]
    for name, hits in results.items():
        what = "clean: a false finding" if BRANCHES[name][1] is None else "its defect named"
        who = ", ".join(f"{check} {count}" for check, count in by.get(name, Counter()).most_common())
        lines.append(f"| {name} | {what} | {sum(hits)} of {len(hits)} | {who} |")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = options("labelled_set", (__doc__ or "").splitlines()[0])
    args = parser.parse_args(argv)
    repo = Path(tempfile.gettempdir()) / "adk-labelled-repo"
    made = branches(repo)
    print(f"built {len(made)} branches in {repo}: {', '.join(made.values())}", flush=True)
    if args.build_only:
        return 0
    results: dict[str, list[bool]] = {}
    by: dict[str, Counter[str]] = {}
    for name, _code, data in reviewed(repo, picked(parser, made, args.branch), args.runs, args.pause, args.profile):
        results.setdefault(name, []).append(scored(name, data) if data else False)
        by.setdefault(name, Counter()).update(named_by(name, data) if data else ())
    print("\n".join(table(results, by)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

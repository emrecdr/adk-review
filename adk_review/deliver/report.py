"""The report a person reads in a terminal, the same review as data for a pipeline (`--json`), and every rule that
keeps model prose from forging either."""

from __future__ import annotations

import functools
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..core.blast import Blast, describe
from ..core.findings import RANK, Checked, Finding, at_or_above
from ..core.health import Health, found_by_rule, grade
from ..core.secrets import scrub
from ..core.text import EVIDENCE_MAX_CHARS, REASON_MAX_CHARS, backticks, cut_at, squash
from ..core.verdict import Outcome, Sourced, at_bar

#: What a terminal runs or reorders, and what a reader cannot see: escape sequences, C0 and C1 controls (U+009B is
#: `ESC [` in one character), every format character — the marks that turn the text after them around (U+202E and its
#: kin), the zero-width ones, the tag characters a whole sentence can hide in — and the variation selectors. Measured
#: before: a sentence in tag characters printed whole, invisible, in the report and the JSON.
_CONTROL = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x1f\x7f-\x9f"
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890-\u0891\u08e2\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064"
    r"\u2066-\u206f\ufeff\ufff9-\ufffb\U000110bd\U000110cd\U00013430-\U0001343f\U0001bca0-\U0001bca3"
    r"\U0001d173-\U0001d17a\U000e0001\U000e0020-\U000e007f\ufe00-\ufe0f\U000e0100-\U000e01ef]"
)


def one_line(text: str) -> str:
    """Model prose as one markdown line: escape sequences and control characters out, whitespace flat.

    The report is read in a terminal and pasted into chats. Measured: a title
    holding a newline forged a second `## Verdict` heading, a newline in a
    suggestion forged a bullet, and `ESC[2J` cleared the terminal. A credential
    in it is hidden too: the report never prints one. Presentation
    only: grounding (`core.verdict.drop_ungrounded`) reads a quote its own way, so
    what counts as grounded cannot move when the report's safety rule does.
    """
    return squash(_CONTROL.sub(" ", scrub(text)[0]))


#: A CI runner's command in its log: Azure runs `##vso[` and GitHub `##[` anywhere in a line, GitHub across invisible
#: characters between them (measured on .NET), so any non-ASCII run counts. GitHub's `::` counts only opening a line,
#: and no printed line opens with the branch's text.
_RUNNER_COMMAND = re.compile(r"#[^ -~]*#[^ -~]*(?:vso)?(?=\[)")


def inert(text: str) -> str:
    """`text` with each CI runner's command opener broken by a space, `##vso [`, and each HTML comment's, `<! --`,
    which hid what lay between two paths wherever the report renders as markdown: paths and quotes are the branch's."""
    return _RUNNER_COMMAND.sub(r"\g<0> ", text).replace("<!--", "<! --")


#: Where an HTML tag would open: a `<` before a letter, a `/`, a `!` or a `?`.
_TAG_OPENER = re.compile(r"<(?=[A-Za-z/!?])")


def prose(text: str) -> str:
    """One line of prose as the report prints it outside a code span: `one_line`'s, with no HTML tag able to open in
    it, since a summary rendered as markdown renders one. Measured: a file named `<h1>Verdict: APPROVED</h1>` printed a
    heading above the real verdict where the job summary renders the report. A code span renders no tag, so evidence
    and paths in one keep their text; the JSON keeps plain text, for its reader to escape where it renders."""
    return _TAG_OPENER.sub("< ", one_line(text))


def code_span(text: str) -> str:
    """`text` as a markdown code span fenced to beat its own backticks: a guideline quotes source, and source has
    them."""
    longest = backticks(text)
    if not longest:
        return f"`{text}`"
    fence = "`" * (longest + 1)
    return f"{fence} {text} {fence}"


def _reason(text: str) -> str:
    """A model's or a provider's words about a finding or a guideline, as one line of readable length."""
    return cut_at(prose(text), REASON_MAX_CHARS)


def _named(items: Iterable[tuple[Sourced, str]]) -> list[str]:
    """One line per finding and what became of it: neither a removal nor a failure is silent."""
    return [f"  - `{item.source}`: {prose(item.finding.title)} — {said}" for item, said in items]


def _blast(blast: Blast) -> list[str]:
    """Where a person reads closely and where a skim will do: what depends on each changed file, widest first.

    Before the verdict, because it says how to read everything under it. A path is the reviewed branch's
    text, so it is flattened and fenced like a guideline's prose.
    """
    lines = ["## Blast radius", ""]
    lines += [f"- {code_span(one_line(r.path))}: {prose(describe(r, blast.floor))}" for r in blast.radii]
    if not blast.asked:
        lines.append("- not asked: no tree rule and no guideline ran, so the head was not read whole")
    elif not blast.listed:
        lines.append("- not read: the head's tree could not be listed; every count above is a floor")
    elif blast.floor:
        lines.append(
            f"- not read: {len(blast.unparsed)} file(s) the parser refused, {blast.skipped} over a cap, past a timeout"
            " or not in the clone; every count above is a floor, and so is any rule over the tree"
        )
    return [*lines, ""]


def render_handoff(evidence: dict, outcome: Outcome, *, verdict: str, fail_on: str) -> str:
    """`--handoff FILE`: each kept finding, most severe first, as a short instruction for a coding agent — where, the
    lines quoted, what to change, and what must hold afterwards — under the text report's safety rules. Concise
    comments with the code quoted lead to changes more often, as a study of review comments found. Nothing is
    applied: the reviewer writes into no repository, and only ruff labels a fix it would apply unasked."""
    pre = evidence["preflight"]
    ranked = sorted(
        zip(outcome.kept, _identified(outcome.kept), strict=True), key=lambda kept: -RANK[kept[0].finding.severity]
    )
    lines = [
        f"# Findings to fix: {what(evidence)}",
        "",
        f"The review of `{pre['head_sha'][:12]}` ended {verdict}. Fix each finding below where it stands, and change",
        "nothing else. Each one holds when a review of the fixed commit no longer reports its fingerprint.",
        "",
    ]
    for number, (item, identity) in enumerate(ranked, 1):
        f = item.finding
        lines += [
            f"{number}. {prose(f.title)} ({_where(f)}), {f.severity}{_bar(f, fail_on)}",
            f"   - quoted: {_quoted(f)}",
            *_to_fix(item, identity, "   "),
            *_folded_under([folded for folded, into in outcome.folded if into is item], fail_on),
        ]
    if not ranked:
        lines.append("Nothing to fix: no finding was kept.")
    if outcome.errors or outcome.unread:
        lines += ["", "This review could not look at everything; what it did not read may hold more to fix."]
    return inert("\n".join(lines)) + "\n"


def _folded_under(folded: Sequence[Sourced], fail_on: str) -> list[str]:
    """A line for each finding folded into a handoff item: another reading of the same lines, by another check or the
    same one, which can be a second defect — an injection whose quote spans a key's line folds into the key's finding.
    Each says whether it sits at the bar, since a finding folds only into one at least as severe and a second defect
    that would fail the build on its own would otherwise read like a duplicate; what to change; and the fingerprint a
    fixed commit must stop reporting, as the JSON's `folded` names it."""
    lines = []
    for item in folded:
        f = item.finding
        lines += [
            f"   - also on these lines: {prose(f.title)}, {f.severity}{_bar(f, fail_on)}",
            *_to_fix(item, fingerprint(item), "     "),
        ]
    return lines


def _bar(f: Finding, fail_on: str) -> str:
    """The mark of a handoff finding that fails the build on its own."""
    return " — at the bar" if at_or_above(f.severity, fail_on) else ""


def _to_fix(item: Sourced, identity: str, indent: str) -> list[str]:
    """A handoff finding's instruction: what to change, and the fingerprint a review of the fixed commit no longer
    reports, which is what must hold after."""
    return [
        f"{indent}- change: {prose(item.finding.suggestion)}",
        f"{indent}- after: `{item.source}` no longer reports fingerprint `{identity}`",
    ]


def render_explained(said: Mapping[str, object]) -> str:
    """`--explain ID`: one check whole, as its file documents it, and for a rule its own examples."""
    lines = [f"# {said['id']}", ""]
    for label in ("kind", "severity", "profiles", "file", "title", "fix"):
        if label in said:
            lines.append(f"- {label:<10}{said[label]}")
    lines += ["", str(said["why"]), ""]
    if "role" in said:
        lines += ["## Its role", "", str(said["role"]), ""]
    for heading, key in (("It flags", "bad"), ("It leaves alone", "good")):
        examples = said.get(key)
        if examples is None:
            continue
        lines += [f"## {heading}", ""]
        lines += [
            f"- {code_span(path)}: {code_span(one_line(text))}" for files in examples for path, text in files.items()
        ]
        lines += [*(["- no example: its tests say what it does"] if not examples else []), ""]
    return inert("\n".join(lines).rstrip() + "\n")


def _heading(check: str, failed: str | None) -> str:
    """A check's section heading, `FAILED` and why where the check could not look."""
    return f"### {check}" + (f" — FAILED: {_reason(failed)}" if failed else "")


def _quoted(f: Finding, limit: int = EVIDENCE_MAX_CHARS) -> str:
    """A finding's evidence as every report prints it: one line, cut, in a code span."""
    return code_span(cut_at(one_line(f.evidence), limit))


def _listed(f: Finding, mark: str = "") -> list[str]:
    """A finding's first two lines in its check's section: its severity, title and place, then its evidence."""
    return [f"- **{f.severity}** {prose(f.title)} ({_where(f)}){mark}", f"  - evidence: {_quoted(f)}"]


def render_tried(paths: Sequence[str], answers: Mapping[str, Checked], skipped: Sequence[str]) -> str:
    """`--try PATH…`: what each rule found in files on disk, each read as wholly added, with no repository and no
    model; the guidelines a profile names are named as not run."""
    lines = [f"# Try: {_spans(paths)}", ""]
    lines += ["Each file read as wholly added, and the head these files alone: no repository, no commit, no model.", ""]
    if skipped:
        lines += [f"- not run: {', '.join(skipped)}: a guideline judges a change, with a model", ""]
    for check, answer in answers.items():
        lines.append(_heading(check, answer.could_not_look))
        for f in answer.findings:
            lines += _listed(f)
        if not answer.findings and not answer.could_not_look:
            lines.append("- no findings")
        lines.append("")
    return inert("\n".join(lines))


def render_checks(rows: list[tuple[str, str, str, str, str, str]]) -> str:
    """`--list-checks`: every check a profile may name, as a table — its id, its kind, the severity its findings
    carry, the profiles that run it, what it looks for and where it is defined."""
    lines = ["| id | kind | severity | profiles | what | where |", "|---|---|---|---|---|---|"]
    for check, kind, severity, profiles, what, where in rows:
        cells = [code_span(one_line(check)), kind, severity, profiles, prose(what), code_span(where)]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "To write one: “Write a rule” and “Write a guideline” in README.md say how; --check ID runs one check alone.",
    ]
    return "\n".join(lines)


def _as_resolved(name: str, ref: str) -> str:
    """A name as given, and the tag it resolved through when it is one: a fork's tag `origin/main`, with no such
    branch fetched, once stood in for the branch unseen."""
    return f"{prose(name)} (the tag {prose(ref)})" if ref.startswith("refs/tags/") else prose(name)


def _where(f: Finding) -> str:
    """A finding's file and line as one line: the path is the branch's text."""
    return prose(f"{f.file}:{f.line}" if f.line else f.file)


def what(evidence: dict) -> str:
    """What was reviewed, for a title: the branch against its base, or every file at the head."""
    pre = evidence["preflight"]
    head, base = prose(pre["head"]), prose(pre["base"])  # names the branch's author chose: `<` is a legal ref letter
    return f"every file at {head}" if evidence["whole"] else f"{head} against {base}"


def header(evidence: dict, *, model: str, profile: str, off: Sequence[str], unread: Mapping[str, str]) -> list[str]:
    """The lines a review opens with, a dry run too: the repository and the revisions, the model and the profile
    that serve it, what it covers, and what no one could read."""
    pre, files, scope = evidence["preflight"], evidence["files"], evidence["scope"]
    if evidence["whole"]:  # no base: every file at the head, each read as added
        against, listed, kind = ["- against      nothing: every file, as if added"], "files", "file(s)"
    else:
        against = [
            f"- against      {_as_resolved(pre['base'], pre.get('base_ref', ''))} @ {pre['base_sha'][:9]}",
            f"- merge-base   {pre['merge_base'][:9]}",
        ]
        listed, kind = "changed", "changed file(s)"
    left = f"{evidence['outside']} {kind} outside it, not reviewed"
    return [
        f"- repository   {pre['repo']}",
        f"- branch       {_as_resolved(pre['head'], pre.get('head_ref', ''))} @ {pre['head_sha'][:9]}",
        *against,
        *([f"- model        {model}"] if model else []),
        f"- profile      {profile}" + (f" (off: {', '.join(off)})" if off else ""),
        *([f"- scope        {prose(', '.join(scope))}: {left}"] if scope else []),
        f"- {listed:<12} {len(files)} file(s): {_spans([f['path'] for f in files])}",
        *(f"- not read     {code_span(one_line(path))}: {why}" for path, why in unread.items()),
        *(
            f"- skipped      {code_span(one_line(path))}: left to the rules by the operator's {code_span(glob)}"
            for path, glob in evidence.get("skipped", {}).items()
        ),
        *_untracked(evidence.get("untracked")),
    ]


#: How many names a list in the report spells out before it counts the rest.
SHOWN = 10


def _spans(names: Sequence[str], shown: int | None = None) -> str:
    """Names as the report lists them, each one line in a code span, joined by commas; past `shown`, the rest
    counted."""
    listed = ", ".join(code_span(one_line(name)) for name in names[:shown])
    more = 0 if shown is None else len(names) - shown
    return listed + (f" and {more:,} more" if more > 0 else "")


def _untracked(names: Sequence[str] | None) -> list[str]:
    """`--staged`'s note of what git does not track: not in the next commit, so named, never reviewed, never a hole."""
    if not names:
        return []
    return [f"- untracked    {_spans(names, SHOWN)}: not in the next commit, so not reviewed"]


def render_dry_run(
    evidence: dict,
    *,
    model: str,
    profile: str,
    off: Sequence[str],
    checks: Sequence[str],
    guidelines: Sequence[str],
    ready: str,
    unread: Mapping[str, str],
    config: str,
) -> str:
    """`--dry-run`: what a review would cover and run, resolved as the review resolves it, and nothing run — the
    cheapest way to find the wrong branch, the wrong profile, or a key that is not there before any of it costs."""
    lines = [
        f"# Dry run: {what(evidence)}",
        "",
        *header(evidence, model=model, profile=profile, off=off, unread=unread),
        f"- checks       {', '.join(checks)}",
        *([f"- guidelines   {', '.join(guidelines)}: {ready}"] if guidelines else []),
        f"- config       {one_line(config)}",
        "",
        *_blast(evidence["blast"]),
        "Nothing ran: no rule, no guideline, no model call. Drop --dry-run to review.",
    ]
    return inert("\n".join(lines))  # the paths are the branch's, here as in the review


def _beside(verdict: str, outcome: Outcome, fail_on: str) -> str:
    """What a degraded verdict also holds: the findings at the bar, which it outranks. Measured before: a pipeline
    that let exit 3 through let a kept blocker through with it, and the verdict line never named it."""
    held = at_bar(outcome.kept, fail_on)
    if verdict != "DEGRADED" or not held:
        return ""
    return f", and {held} finding{'s' * (held != 1)} at or above `--fail-on {fail_on}`"


def _since(identity: str, previous: Mapping[str, dict] | None) -> str | None:
    """A kept finding against a previous review's findings, by fingerprint: `new`, `seen`, or None with none."""
    return None if previous is None else "seen" if identity in previous else "new"


def _resolved(identities: Sequence[str], verdict: str, previous: Mapping[str, dict] | None) -> list[dict] | None:
    """A previous review's findings this one, its findings' `identities`, no longer finds: None with no previous review,
    and where this review has a hole, which resolves nothing, since what it could not look at may hold them still, as
    PR-Agent requires."""
    if previous is None or verdict == "DEGRADED":
        return None
    found_now = set(identities)
    return [found for identity, found in previous.items() if identity not in found_now]


def _spent(usage: dict) -> str:
    """The spend as the text report prints it, from the numbers the JSON report carries: the usage ledger's summary."""
    calls, attempted, tokens = usage["calls"], usage["attempted"], usage["tokens"]
    lines = [f"- spend: {calls} model call(s) completed of {attempted} attempted, {tokens:,} tokens"]
    lines += [f"  - `{name}`: {r['calls']} call(s), {r['tokens']:,} tokens" for name, r in usage["by_agent"].items()]
    if usage["ceiling"] is not None:
        lines.append(f"- ceiling: {usage['ceiling']:,} tokens")
    return "\n".join(lines)


def render(
    evidence: dict,
    outcome: Outcome,
    *,
    verdict: str,
    usage: dict,
    fail_on: str,
    model: str,
    profile: str,
    off: Sequence[str],
    checks: Sequence[str],
    previous: Mapping[str, dict] | None = None,
    health: Health | None = None,
    previous_health: Mapping | None = None,
) -> str:
    """`evidence` is `collect_evidence`'s; `profile` is the profile that ran and `off` what it left off, named so a
    reader sees what was never asked; `checks` are the ids that ran, in report order, each a section whether it found
    anything or not."""
    lines = [
        f"# Review: {what(evidence)}",
        "",
        # First, under the title: measured before, the verdict sat below the whole blast radius, a screen down.
        f"## Verdict: {verdict}{_beside(verdict, outcome, fail_on)}",
        "",
        *header(evidence, model=model, profile=profile, off=off, unread=outcome.unread),
        "",
        # The project's measures before its blast radius: under --all the radius names every file, a long way down.
        *_health(health, outcome, previous_health, evidence["scope"]),
        *_blast(evidence["blast"]),
        "## Checks",  # each check a section of its own, under its own heading, whatever it found
        "",
    ]
    # Read against a previous review alone: with none, no finding is fingerprinted.
    identities = _identified(outcome.kept) if previous is not None else [""] * len(outcome.kept)
    for check in checks:
        findings = [
            (identity, item.finding)
            for identity, item in zip(identities, outcome.kept, strict=True)
            if item.source == check
        ]
        failed = outcome.errors.get(check)
        lines.append(_heading(check, failed))
        for identity, f in findings:
            mark = " — new since the previous review" if _since(identity, previous) == "new" else ""
            lines += [*_listed(f, mark), f"  - fix: {prose(f.suggestion)}"]
        # Named under its own source: measured live, ruff's every hit folded into a guideline's blocker on the same
        # lines, and its section read "no findings" under a rule that had found `shell=True`.
        folded = [(item.finding, into.source) for item, into in outcome.folded if item.source == check]
        for f, into in folded:
            lines.append(f"- folded into `{into}`'s finding on the same lines: {prose(f.title)} ({_where(f)})")
        if not findings and not folded and not failed:
            lines.append("- no findings")
        lines.append("")
    resolved = _resolved(identities, verdict, previous)
    if previous is not None and resolved is None:
        lines.append("- resolved since the previous review: none counted, since this review could not look at all")
    elif resolved is not None:
        lines.append(f"- resolved since the previous review: {len(resolved)}")
        lines += [f"  - {prose(str(f.get('title', '')))} ({prose(str(f.get('file', '')))})" for f in resolved]
    lines.append(f"- findings dropped for evidence not in the diff: {len(outcome.dropped)}")
    if severe := at_bar(outcome.dropped, fail_on):
        lines.append(f"  - {severe} at or above `--fail-on {fail_on}`: not approved until a person reads them")
    # Named with the quote, so a reader can tell a guideline that invented one from one that merely paraphrased: in a
    # code span, as the guideline wrote it, never as prose, which breaks a tag's `<`.
    lines += _named((item, f"quoted {_quoted(item.finding, 120)}") for item in outcome.dropped)
    if outcome.verified:
        lines += [
            f"- findings refuted by the verifier: {len(outcome.refuted)}",
            *_named((item, _reason(why)) for item, why in outcome.refuted),
            f"- findings the verifier could not judge, kept: {len(outcome.unverified)}",
            *_named((item, _reason(why)) for item, why in outcome.unverified),
        ]
    lines += [
        f"- duplicates across rules and guidelines folded into one finding: {len(outcome.folded)}",
        f"- secrets redacted before any model call: {outcome.redacted}",
        _spent(usage),
        "",
    ]
    return inert("\n".join(lines))  # once over the whole: no two fields side by side spell one


def _share(lines: int, of: int) -> str:
    return f"{100 * lines / of:.1f}%"


def _number(value: object) -> int:
    """A count read from a previous report, which is a file and may hold anything: 0 for what is no whole number."""
    return value if type(value) is int else 0


def _at(data: object, *keys: str) -> object:
    """What a previous report holds under `keys`, one inside the other, or None where a step on the way is no mapping:
    the report is a file, and may hold anything."""
    for key in keys:
        data = data.get(key) if isinstance(data, Mapping) else None
    return data


def _severities(counts: Mapping[str, int]) -> str:
    """Counts by severity, the most severe first: `2 major · 14 minor`, or `none`."""
    said = [f"{counts[severity]:,} {severity}" for severity in reversed(RANK) if counts.get(severity)]
    return " · ".join(said) or "none"


def _health(health: Health | None, outcome: Outcome, previous: Mapping | None, scope: Sequence[str] = ()) -> list[str]:
    """A whole project's measures, under the verdict's header: numbers, never a grade, and none of them read by the
    verdict; beside each complexity share and each rule's count in source, what a previous review's JSON said."""
    if health is None:
        return []
    total = sum(lines for _label, lines in health.bands)
    before = _at(previous, "complexity", "bands")
    before = before if isinstance(before, Mapping) else {}
    before_total = sum(_number(lines) for lines in before.values())
    shares = [  # none where no function was measured: a project of no Python, or none that parses
        f"{label} {_share(lines, total)}"
        + (f" (was {_share(_number(before.get(label)), before_total)})" if before_total else "")
        for label, lines in health.bands
        if total
    ]
    (files, lines), (tests, test_lines) = health.source, health.tests
    said = [
        "## Health",
        "",
        f"Measured by code alone over every file at the head{_under(scope)}, and none of it read by the verdict: "
        "maintainability graded, security and correctness counted.",
        "",
        f"- read whole   {health.read:,} of {health.files:,} files",
        f"- source       {files:,} Python file(s), {lines:,} lines; tests {tests:,} file(s), {test_lines:,} lines",
        "- complexity   the source's function lines by McCabe complexity: "
        + (" · ".join(shares) or "no function measured"),
        _graded(health, previous),
    ]
    if health.not_parsed:
        said.append(f"- not parsed   {_spans(health.not_parsed, SHOWN)}")
    if health.worst:
        named = [
            f"{code_span(one_line(f'{unit.path}:{unit.line}'))} {code_span(one_line(unit.name))} ({unit.complexity})"
            for unit in health.worst
        ]
        said.append(f"- most complex {'; '.join(named)}")
    rows = found_by_rule(outcome.kept)
    if rows:
        said += ["", "| rule | in source | in tests |", "|---|---|---|"]
        for check, row in rows.items():
            prior = _at(previous, "found", check, "source")
            counted = sum(_number(n) for n in prior.values()) if isinstance(prior, Mapping) else 0
            was = "" if previous is None else f" (was {counted:,})"
            said.append(f"| `{check}` | {_severities(row['source'])}{was} | {_severities(row['tests'])} |")
    return [*said, ""]


def _under(scope: Sequence[str]) -> str:
    """` under `src/``, the folders a review was kept to, or nothing for the whole head."""
    return f" under {_spans(scope)}" if scope else ""


def _graded(health: Health, previous: Mapping | None) -> str:
    """Maintainability's grade, the profile SIG's rating read, and what a previous review's JSON graded."""
    letter, why = grade(health)
    was = _at(previous, "complexity", "grade")
    was_said = f" (was {was})" if was in tuple("ABCDE") else ""  # a previous report is a file: any value
    if letter is None:
        return f"- maintainability  not graded: {why}{was_said}"
    return f"- maintainability  {letter}{was_said}, SIG's rating of its complexity profile: {why}"


def _health_data(health: Health, outcome: Outcome) -> dict:
    """The JSON report's `health`: the measures `_health` prints, as data."""
    return {
        "files": {"all": health.files, "read": health.read},
        "source": {"files": health.source[0], "lines": health.source[1]},
        "tests": {"files": health.tests[0], "lines": health.tests[1]},
        "complexity": {
            "grade": (graded := grade(health))[0],
            "why": graded[1],
            "python_source": {"at_head": health.python[0], "parsed": health.python[1]},
            "bands": dict(health.bands),
            "not_parsed": [safe_line(path) for path in health.not_parsed],
            "most_complex": [
                {
                    "file": safe_line(unit.path),
                    "line": unit.line,
                    "function": safe_line(unit.name),
                    "complexity": unit.complexity,
                    "lines": unit.lines,
                }
                for unit in health.worst
            ],
        },
        "found": found_by_rule(outcome.kept),
    }


#: The JSON report's version: raised when a field changes its meaning or goes, so a pipeline reading it can tell. 2:
#: a finding names its `check` by its id, `rule.secrets` or `guideline.security`, where 1 named a `source`.
SCHEMA = 2


#: A previous review's findings by fingerprint and its health, as `--previous` reads them, or none.
type Previous = tuple[dict[str, dict] | None, dict | None]


def read_previous(path: str) -> tuple[dict[str, dict], dict | None]:
    """A previous review's findings by fingerprint, from the report its `--json` wrote, and its `health`, None where it
    has none; a sentence for anything else, before the review runs."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("findings"), list):
            raise ValueError(f"not a review's JSON report of schema {SCHEMA}, as --json writes one")
        findings = [f for f in data["findings"] if isinstance(f, dict) and isinstance(f.get("fingerprint"), str)]
        # Each finding made safe whole, keys and nested values too, where it comes in: `resolved` gives it back, and
        # a kept report can be edited. One a review wrote reads the same, a cut's run of spaces aside.
        kept = {f["fingerprint"]: f for f in map(safe_value, findings)}
    except RecursionError:  # measured by review: nested 1,100 deep, which no report `--json` writes, it ended as a hole
        raise ValueError(f"--previous {path}: nested deeper than a report --json writes") from None
    except (OSError, ValueError) as exc:
        raise ValueError(f"--previous {path}: {getattr(exc, 'strerror', None) or exc}") from None
    health = data.get("health")
    return kept, health if isinstance(health, dict) else None


# Cached: a report's strings repeat, its keys, checks, files and fixes. Measured over 18,296 findings: a previous
# report read in 0.38 s where it took 1.28 uncached, and the JSON report written in 0.42 s where it took 0.67.
@functools.lru_cache(maxsize=64)
def safe_line(text: str, limit: int | None = None) -> str:
    """`text` as one line that is safe to print and to post: no credential, no control character, no CI runner's
    command and no HTML comment, cut at `limit` where one is given. Every string of the JSON report, which a pipeline
    prints and posts, and every sentence the command and the pre-push hook say of their own."""
    line = one_line(text)
    return inert(cut_at(line, limit) if limit else line)


def safe_value(value: Any) -> Any:
    """`safe_line` over every string inside a JSON value, its keys too: a file a review is given, a previous report,
    made as safe as the review's own report before any of it is printed or written back."""
    if isinstance(value, str):
        return safe_line(value)
    if isinstance(value, dict):
        return {safe_line(key): safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_value(item) for item in value]
    return value


def fingerprint(item: Sourced, occurrence: int = 0) -> str:
    """A finding's identity across runs: its check, its file, its evidence with every space gone and secrets redacted
    — for a guideline's finding the lines its quote stands on, which grounding put there, however the model framed
    them — and which of the findings sharing all three it is, in the order the review lists them. Never its line,
    which a line added above it would move, as SonarQube, GitHub and Semgrep key theirs on content."""
    quote = "".join(scrub(item.finding.evidence)[0].split())
    said = "\0".join((item.source, item.finding.file, quote, str(occurrence)))
    return hashlib.sha256(said.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _identified(items: Sequence[Sourced]) -> list[str]:
    """Each finding's fingerprint, the occurrences of one check, file and quote counted in the order given."""
    seen: Counter[str] = Counter()
    found = []
    for item in items:
        first = fingerprint(item)  # the first occurrence's, which most findings are: hashed once
        found.append(fingerprint(item, seen[first]) if seen[first] else first)
        seen[first] += 1
    return found


def _found(item: Sourced, added: Mapping[str, frozenset[int]], identity: str = "") -> dict:
    """One finding as data: its identity, its check, its line and the lines the diff places it on, whether the change
    added one of them, and what the report says."""
    f = item.finding
    placed = range(item.lines[0], item.lines[1] + 1) if item.lines else [f.line] if f.line is not None else []
    return {
        "fingerprint": identity or fingerprint(item),
        "check": item.source,
        "file": safe_line(f.file),
        "line": f.line,
        "lines": list(item.lines) if item.lines else None,
        # Whether a line the finding sits on is one the change added; None for a finding with no line. Measured:
        # 96.5% of lint's findings on one commit sat on lines it did not add, which a pipeline may weigh apart.
        "on_added_line": any(n in added.get(f.file, ()) for n in placed) if placed else None,
        "severity": f.severity,
        "title": safe_line(f.title),
        "evidence": safe_line(f.evidence, EVIDENCE_MAX_CHARS),
        "suggestion": safe_line(f.suggestion),
    }


def _reasoned(judged: Sequence[tuple[Sourced, str]], added: Mapping[str, frozenset[int]]) -> list[dict]:
    """Findings a verifier judged, as data, each beside the reason it gave."""
    return [{**_found(item, added), "reason": safe_line(why, REASON_MAX_CHARS)} for item, why in judged]


def as_data(
    evidence: dict,
    outcome: Outcome,
    *,
    verdict: str,
    code: int,
    usage: dict,
    fail_on: str,
    model: str,
    profile: str,
    off: Sequence[str],
    checks: Sequence[str],
    version: str = "",
    seconds: Mapping[str, float] | None = None,
    previous: Mapping[str, dict] | None = None,
    health: Health | None = None,
) -> dict:
    """`--json`: the review `render` prints, as data for a pipeline: the verdict and its exit code, every finding
    with its check and place, what could not look and what was not read, under the text report's safety rules."""
    pre, whole = evidence["preflight"], evidence["whole"]
    identities = _identified(outcome.kept)
    # The added lines' numbers, kept when the rules let the lines go; a deleted or binary file has none.
    added = {f["path"]: f.get("added_at", frozenset()) for f in evidence["files"]}
    return {
        "schema": SCHEMA,
        "verdict": verdict,
        "exit_code": code,
        "fail_on": fail_on,
        "findings_at_fail_on": at_bar(outcome.kept, fail_on),
        "reviewed": {
            "repository": safe_line(pre["repo"]),
            "head": safe_line(pre["head"]),
            "head_sha": pre["head_sha"],
            "base": None if whole else safe_line(pre["base"]),
            "base_sha": None if whole else pre["base_sha"],
            "merge_base": None if whole else pre["merge_base"],
            "scope": [safe_line(folder) for folder in evidence["scope"]],
            "skipped": {safe_line(path): glob for path, glob in evidence.get("skipped", {}).items()},
            "untracked": None if (away := evidence.get("untracked")) is None else [safe_line(name) for name in away],
            "files": [safe_line(f["path"]) for f in evidence["files"]],
        },
        "model": model or None,
        "profile": safe_line(profile),
        "off": list(off),
        "checks": list(checks),
        "summary": {severity: sum(item.finding.severity == severity for item in outcome.kept) for severity in RANK},
        # A whole project's measures, under `--all`; None for a review of a change, which is no measure of a project.
        "health": None if health is None else _health_data(health, outcome),
        "findings": [
            {**_found(item, added, identity), "since_previous": _since(identity, previous)}
            for item, identity in zip(outcome.kept, identities, strict=True)
        ],
        # The previous review's findings no longer found, as it wrote them; None with no previous review, or where
        # this one has a hole, which resolves nothing.
        "resolved": _resolved(identities, verdict, previous),
        "could_not_look": {source: safe_line(why, REASON_MAX_CHARS) for source, why in outcome.errors.items()},
        "not_read": {safe_line(path): safe_line(why, REASON_MAX_CHARS) for path, why in outcome.unread.items()},
        "folded": [{**_found(item, added), "into": into.source} for item, into in outcome.folded],
        "dropped": [_found(item, added) for item in outcome.dropped],
        "dropped_at_fail_on": at_bar(outcome.dropped, fail_on),
        "verified": outcome.verified,
        "refuted": _reasoned(outcome.refuted, added),
        "unverified": _reasoned(outcome.unverified, added),
        "secrets_redacted": outcome.redacted,
        "usage": usage,
        "seconds": dict(seconds or {}),  # how long each agent took, by the id the report uses: timing, never a verdict
        "reviewer_version": version or None,
    }

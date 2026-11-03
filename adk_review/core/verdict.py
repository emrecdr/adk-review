"""Everything between the guidelines' raw answers and a verdict: grounding, folding, deciding. Pure, and counted.

Every narrowing is counted and printed, including zeros, so a reader can see
what was removed and why. Nothing here calls a model or a process.
"""

from __future__ import annotations

import bisect
import re
import textwrap
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from .evidence import Entry
from .findings import RANK, Checked, Finding, Review, at_or_above
from .secrets import scrub
from .text import REASON_MAX_CHARS, cut_at, squash

EXIT_CLEAN, EXIT_FINDINGS, EXIT_USAGE, EXIT_DEGRADED = 0, 1, 2, 3
#: The shell's own code for a run Ctrl-C stopped: 128 and SIGINT's 2.
EXIT_INTERRUPTED = 130
#: And for one a job stopped: 128 and SIGTERM's 15.
EXIT_TERMINATED = 143
#: Where the diff places a finding: its first and last line, None when a line is not known. Never half known: measured
#: by a type checker, grounding kept `(None, None)` where a redaction joined lines, and the JSON report, reading it as
#: two numbers, raised after every model call, the review printing no report.
type Lines = tuple[int, int] | None
#: What a kept quote's `-` says, told to each reader of one: with the marker unexplained, a model read deleted lines as
#: the code to fix.
DELETED_QUOTED = "A quoted line that starts with `-` is one the change deleted. The branch no longer holds that line."


@dataclass
class Sourced:
    """A finding and the check it came from, by its id: `rule.secrets`, `guideline.security`."""

    source: str
    finding: Finding
    #: The first and last line the diff places it on: a rule's one line, a guideline's quote where grounding found it.
    lines: Lines = None
    #: Whether its quote holds a line the change deleted, as the diff's own `-` marks it: read by review, a line told by
    #: the `-` its text starts with made a Markdown bullet the branch holds read as deleted.
    deleted: bool = False
    #: Whether it was already in the file before the change, not caused by it: kept apart in `Outcome.old`.
    old: bool = False
    #: Whether its suggested code is what replaced the deleted lines it quotes, so the change may have done it already:
    #: kept, decided on as any finding is, and listed apart. Asked by the owner: a notice, not a major issue.
    done: bool = False


@dataclass
class Outcome:
    """What post-processing decided, in one place, so the verdict and the report read the same numbers."""

    kept: list[Sourced] = field(default_factory=list)
    dropped: list[Sourced] = field(default_factory=list)
    #: What was already in the files the change touched, not caused by it: listed apart, never at the bar.
    old: list[Sourced] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # source -> why that rule or guideline could not look
    unread: dict[str, str] = field(default_factory=dict)  # path -> why no check that ran read it whole
    folded: list[tuple[Sourced, Sourced]] = field(default_factory=list)  # a finding, and the one it folded into
    redacted: int = 0
    verified: bool = False  # `--verify` ran
    refuted: list[tuple[Sourced, str]] = field(default_factory=list)  # dropped on the verifier's say-so, with why
    unverified: list[tuple[Sourced, str]] = field(default_factory=list)  # the verifier could not judge; kept, with why

    def unproven(self, fail_on: str) -> list[Sourced]:
        """The findings at the bar set aside unproven, their quote not borne out or a verifier refuting them: a hole
        `decide` names, and a hand-off and a comment list, read from one place."""
        unproven = [*self.dropped, *(item for item, _why in self.refuted)]
        return [item for item in unproven if at_or_above(item.finding.severity, fail_on)]

    def folded_into(self) -> dict[int, list[Sourced]]:
        """Each kept finding's id, and the findings folded into it, in the fold's order: what the report's table, its
        sections and the hand-off each list with a finding, read from one place. By id, since a finding holds no
        hash."""
        under: dict[int, list[Sourced]] = {}
        for item, into in self.folded:
            under.setdefault(id(into), []).append(item)
        return under


@lru_cache(maxsize=512)
def as_seen(text: str) -> str:
    """Text the way a guideline saw it and quotes it: secrets redacted, diff markers gone, whitespace flat.

    Measured on Copilot with the drops named: a guideline quoted two added lines
    without their `+` markers and was dropped; another quoted the redaction
    placeholders it had been shown and was dropped. Both were grounded — in
    the text the model read, not in the raw diff. Cached: the fold and the
    verifier's choice compare each finding's quote with others on its file.
    """
    redacted, _count = scrub(text)
    return squash("\n".join(_unmarked(line) for line in redacted.splitlines()))


#: A redaction's placeholder, its label or none: to grounding one stand-in, since the label is the scrubber's, never
#: the code's. Measured live on Gemini: shown `[REDACTED:aws_secret_access_key]`, a guideline quoted the key's lines
#: with `[REDACTED:secret_access_key]`, and its finding dropped, the review a hole.
_PLACEHOLDER = re.compile(r"\[REDACTED(?::[^\]\n]*)?\]")


@lru_cache(maxsize=512)
def seen_lines(text: str, markers: bool = True) -> tuple[str, ...]:
    """`as_seen` line by line, with every space gone rather than flattened: secrets redacted, each placeholder one,
    a diff marker gone, blank lines left out. Measured on Gemini: a guideline quoted three whole lines with the breaks
    between them lost, joined by a space or by nothing, so where a line ends is read off the file, never off the
    quote. `markers` off keeps a line's leading `+` or `-`: in a quote it may be the line's own text, a YAML item's
    dash."""
    redacted, _count = scrub(text)
    return _flat(_PLACEHOLDER.sub("[REDACTED]", redacted), markers)


def _flat(text: str, markers: bool = True) -> tuple[str, ...]:
    """Each line of `text` with its diff marker gone, what no reader sees gone and every space gone, blank lines left
    out; nothing redacted."""
    lines = ("".join(_unmarked(line, markers).split()) for line in text.splitlines())
    return tuple(line for line in lines if line)


def _unmarked(line: str, markers: bool = True) -> str:
    """A line of a diff as a reader sees it, the fold and grounding alike: its marker gone where `markers` says so,
    and what no reader sees gone."""
    return _visible(line[1:] if markers and line[:1] in "+- " else line)


#: The variation selectors, which pick a glyph's form and are no character a reader sees.
_SELECTORS = frozenset((*range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0)))


def _visible(text: str) -> str:
    """`text` as a model can quote it, without what no reader sees: Unicode's format characters, the tag characters a
    whole sentence can hide in among them, and the variation selectors. Measured live on Gemini: a payload in tag
    characters left its comment quoted as `#`, and the quote of the shell call beside it dropped, the review a hole.
    A text in ASCII alone, nearly every line of a diff, is passed over."""
    if text.isascii():
        return text
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf" and ord(ch) not in _SELECTORS)


#: The lines grounding reads on from where a quote could start, in one view of a hunk: past them, the places found by
#: then are read. Measured by review: a hunk of 2,000 repeated lines quoted by half took 3.7 s a finding, each of its
#: thousand places walked whole.
_PLACING_STEPS = 20_000


def _whole_lines(quote: str, lines: tuple[str, ...]) -> Iterator[tuple[int, int]]:
    """Each place `quote` starts and ends as a run of consecutive `lines`, each whole: it starts where one begins and
    ends where one ends, each line read with a diff marker before it or without. Measured live on Gemini: a guideline
    quoted three added lines with the breaks between them lost and each one's `-` left in, `-a-b-c` on one line, and
    every finding it made dropped, the review a hole. Measured by review at the 16,000-character cap: a framed
    100-line quote took 30 ms to place with a run built from every line, and 6 ms with none built where the quote
    cannot start; and a hunk of 2,000 repeated lines quoted whole took 4.9 s, read on from every line, where no line
    whose rest of the hunk, a marker before each, is shorter than the quote now starts one."""
    room, total = [0] * len(lines), 0
    for index in range(len(lines) - 1, -1, -1):
        total += len(lines[index]) + 1
        room[index] = total
    steps = _PLACING_STEPS
    for start in range(len(lines)):
        if room[start] < len(quote):  # the room only shrinks: no later line holds it either
            return
        at, end = _past(quote, {0}, lines[start]), start  # no line is empty: each place left is past a whole line
        while at:
            if (steps := steps - 1) < 0:
                return
            if len(quote) in at:
                yield start, end
            end += 1
            at = _past(quote, at, lines[end]) if end < len(lines) else set()


def _past(quote: str, places: set[int], line: str) -> set[int]:
    """Where `quote` goes on after `line`, read from each of `places` as it stands or after a `+` or `-` before it."""
    return {
        place + skip + len(line)
        for place in places
        for skip in (0, 1)
        if (not skip or quote[place : place + 1] in ("+", "-")) and quote.startswith(line, place + skip)
    }


#: A fence a model wraps a quote in of its own accord, ```` ```python ```` or `~~~`, read as grounding reads a line:
#: every space gone.
_OWN_FENCE = re.compile(r"(?:`{3,}|~{3,}).*")
#: The same fence where the line breaks were lost: run into the quote's first line or its last.
_FENCE_RUN_IN = re.compile(r"^(?:`{3,}|~{3,})|(?:`{3,}|~{3,})$")


def _quotes(evidence: str, around: Sequence[str]) -> set[str]:
    """`evidence` each way grounding reads it, its lines joined with every space gone: as given, since a Markdown
    file's diff holds fences and headings of its own; and without the lines its file was framed in as the guidelines
    were shown it — `around`, wherever the quote carries one — nor a fence of the model's own at either end. Each read
    with a line's leading `+`, `-` or space as a diff marker, and as the line's own text: measured, `- run: …` and
    `++i;` quoted as the file has them lost their first character.

    Measured live on Gemini: the injection on `sql-from-input` quoted inside its fence, the blocker dropped and the
    review a hole where it should have stopped the change; on Copilot, 2 of 5 findings dropped quoted their lines under
    the hunk's `@@` header. What is left must still be whole lines of the diff: the framing goes, and alone grounds
    nothing, and only the file's own goes, never another file's heading."""
    quotes = set()
    for markers in (True, False):
        lines = seen_lines(evidence, markers)
        shown = set(seen_lines("\n".join(around), markers))
        inside = without_fences([line for line in lines if line not in shown])
        for quote in ("".join(lines), "".join(inside)):
            quotes |= {quote, _FENCE_RUN_IN.sub("", quote)}
    return quotes - {""}


def without_fences(lines: Sequence[str]) -> list[str]:
    """`lines` without a fence of the model's own at either end, backticks or tildes: grounding's reading of a quote,
    the flag's and the comment's of suggested code, from one place. Read by review: three copies drifted, and one read
    `~~~` as code."""
    start, end = 0, len(lines)
    while start < end and _OWN_FENCE.fullmatch(lines[start].strip()):
        start += 1
    while end > start and _OWN_FENCE.fullmatch(lines[end - 1].strip()):
        end -= 1
    return list(lines[start:end])


def drop_ungrounded(
    review: Review,
    quotable: dict[str, list[list[tuple[int, str]]]],
    framing: Mapping[str, Sequence[str]],
) -> tuple[list[tuple[Finding, Lines, bool, bool]], list[Finding]]:
    """Split findings into those that quote their own file's diff — whole lines, in order, as the guideline saw them,
    whatever whitespace joined them — and the rest, each kept one with the lines the diff places it on, whether its
    quote holds a line the change deleted, and whether its suggested code is what replaced them (`already_done`, read
    in the hunk the quote was placed in). `quotable` is
    each changed file's hunks, their lines numbered, and `framing` the lines each was framed in as the guidelines were
    shown it, both from `collect_evidence`.

    Measured with the rule it replaced, a substring of the whole rendered
    block: a `### <path>` heading, the fence, a hunk header, git's
    `diff --git` line and the one word `import` were each grounded, and a
    finding's `file` was never read. The radius had been one member of that
    class: out of the block, it no longer carries the safety, this rule does.
    Measured live on both arms against the planted branch: every guideline quote
    is grounded by it. A kept finding's line is where its quote starts, read
    off the diff: measured live on Copilot, the guidelines named a wrong line for
    4 of 5 findings. And its evidence is the lines its quote stands on, as the
    file reads them, a line the change deleted with its `-`, secrets redacted:
    measured by review, the same two lines
    quoted plain and inside their fence under their `@@` header were two
    fingerprints, and `--previous` named one finding resolved and new at once.
    """
    kept: list[tuple[Finding, Lines, bool, bool]] = []
    dropped: list[Finding] = []
    for finding in review.findings:
        path = _meant(finding.file, quotable)
        if (placed := _placed(finding, quotable.get(path, []), framing.get(path, ()))) is None:
            # Its file the model's own text, never replaced by the diff's path as a kept finding's is, so cut here, as
            # a title is where it is read: read by review, four of 5,604 characters filled what chat's verdict reads,
            # past its kept blocker.
            dropped.append(finding.model_copy(update={"file": cut_at(finding.file, REASON_MAX_CHARS)}))
            continue
        lines, stood, deleted, hunk = placed
        update = {"file": path, "line": lines[0] if lines else None}
        if stood is not None:  # where the redaction joined lines, the model's quote is all there is
            update["evidence"] = scrub(stood)[0]
        grounded = finding.model_copy(update=update)
        done = hunk is not None and already_done(grounded, lines, deleted, quotable[path][hunk])
        kept.append((grounded, lines, deleted, done))
    return kept, dropped


def already_done(finding: Finding, lines: Lines, deleted: bool, hunk: list[tuple[int, str]]) -> bool:
    """Whether a grounded finding may ask for no change: its quote holds lines the change deleted, and the code it
    suggests is what replaced them, as the branch holds them. Read in `hunk`, the one grounding placed the quote in, at
    its `lines`; line by line, the indent kept within the code; and over the spot of a deleted block the quote itself
    holds, the lines that replaced it or, where none did, the lines either side of the gap. Read by review: the same
    deleted text elsewhere in the hunk, replaced there, flagged a real bug. Read by review on a branch that removed a
    parameter at every use: six majors read the deleted lines as held code and suggested the code the branch already
    had. A flag, never a removal: read by review three times, code that removed such findings removed real bugs too."""
    if not (deleted and lines and any(ch.isalnum() for ch in finding.code)):
        return False
    raws = [raw for _number, raw in hunk]
    # The quote's own lines: git numbers a deleted line with the line before it, so its span holds its deletions.
    inside = {at for at, (number, _raw) in enumerate(hunk) if lines[0] <= number <= lines[1]}
    # Each deleted line as grounding reads it, a secret redacted: read by review, a raw line holding a key never
    # matched its quote, which grounding had redacted.
    quoted = set(seen_lines("\n".join(line for line in finding.evidence.splitlines() if line.startswith("-"))))
    held = [at for at, raw in enumerate(raws) if raw[:1] in "+ " and raw[1:].strip()]  # the branch's lines, no blank
    if not (spots := _spots(raws, quoted, held, inside)):
        return False
    wanted = [
        line.rstrip() for line in textwrap.dedent(_branch_side(_unfenced(finding.code))).splitlines() if line.strip()
    ]
    for start in range(len(held) - len(wanted) + 1):
        run = held[start : start + len(wanted)]
        if not any(spot <= set(run) for spot in spots):  # only a run over a spot can be the fix: never dedented
            continue
        seen = [line.rstrip() for line in textwrap.dedent("\n".join(raws[at][1:] for at in run)).splitlines()]
        if seen == wanted:
            return True
    return False


def _branch_side(code: str) -> str:
    """`code` as the branch holds it: where a model wrote it as a diff, every line marked and one `-` or `+`, its
    deleted lines left out and each mark dropped. Read by review: code written `-old` and `+new` asked for the branch's
    own line, and never matched it."""
    lines = code.splitlines()
    marked = [line for line in lines if line.strip()]
    if not (marked and all(line[:1] in "+- " for line in marked) and any(line[:1] in "+-" for line in marked)):
        return code
    return "\n".join(line[1:] for line in lines if line[:1] != "-")


def _unfenced(code: str) -> str:
    """`code` without a fence of the model's own at either end."""
    return "\n".join(without_fences(code.splitlines()))


def _spots(raws: Sequence[str], quoted: set[str], held: Sequence[int], inside: set[int]) -> list[set[int]]:
    """Where each quoted block of deleted lines stands on the branch's side of the hunk: the added lines right after
    it, which replaced it, or, where none did, the branch's lines either side of the gap; none where the gap has no
    line on one side, so a deletion at a hunk's edge is never read as replaced. Only a block that starts `inside` the
    quote counts. `held` is in hunk order, so the lines either side are found by bisection."""
    kept = set(held)
    spots: list[set[int]] = []
    at = 0
    while at < len(raws):
        if not (at in inside and raws[at][:1] == "-" and "".join(seen_lines(raws[at])) in quoted):
            at += 1
            continue
        end = at
        while end < len(raws) and raws[end][:1] == "-":
            end += 1
        added = end
        while added < len(raws) and raws[added][:1] == "+":
            added += 1
        if added > end:
            spots.append({line for line in range(end, added) if line in kept})
        else:
            gap = bisect.bisect_left(held, at)  # the first held line after the deleted block's start
            if 0 < gap < len(held):
                spots.append({held[gap - 1], held[gap]})
        at = added
    return [spot for spot in spots if spot]


def _meant(path: str, quotable: dict[str, list[list[tuple[int, str]]]]) -> str:
    """The changed file a finding names: as given, or with git's `b/` taken off where only that names one, as the
    diff's own header spells the path."""
    if path not in quotable and path.startswith("b/") and path[2:] in quotable:
        return path[2:]
    return path


def _placed(
    finding: Finding, hunks: list[list[tuple[int, str]]], around: Sequence[str]
) -> tuple[Lines, str | None, bool, int | None] | None:
    """The first and last line of `finding`'s quote in its own file's diff, where it is whole lines of one hunk, in
    order, as the guideline saw them, those lines as the file reads them, a deleted one with its `-`, whether one is,
    and which hunk it stands in; `(None, None, False, None)` where the redaction joined lines, so no line is named
    rather than a wrong one, and no text but the quote; None where the diff does not bear the quote out.

    Each hunk is read four ways: as git prints it, as the new file reads it (its context and added lines), as the old
    file read it (its context and removed lines), and with each replaced line beside what replaced it. Measured
    before, the hunk as git prints it alone: a correct quote of the new code, running from a context line into a new
    one across a replaced line, was dropped, and as a blocker the run degraded. Never across two hunks: measured
    before, the last line of one hunk joined to the first of the next, twenty lines apart, grounded as one span, and a
    finding on the lines between folded into it."""
    quotes = _quotes(finding.evidence, around)
    placed: list[tuple[tuple[int, int], str, bool, int]] = []
    joined = False
    for index, hunk in enumerate(hunks):
        # As git prints it, as the new file reads, as the old file read, and each replaced line beside its new one.
        views = [[(number, raw) for number, raw in hunk if raw[:1] in kept] for kept in ("+- ", "+ ", "- ")]
        paired = _paired(hunk)
        for view in views if paired == views[0] else (*views, paired):
            lines = seen_lines("\n".join(raw for _number, raw in view))
            runs = {run for quote in quotes for run in _whole_lines(quote, lines)}
            if not runs:
                continue
            # Each seen line's number and the line of the view it is, counted unredacted: a redaction never adds or
            # removes a line of its own. Where the redaction joined lines, a private key's block one line as the
            # guideline saw it, the counts differ.
            at = [(number, index) for index, (number, raw) in enumerate(view) for _line in _flat(raw)]
            if len(at) != len(lines):
                joined = True
                continue
            for start, end in runs:
                (first, since), (last, until) = at[start], at[end]
                # A deleted line keeps its `-`, as every diff prints one: read by review, the marker cut from a quote
                # of a replaced line printed it as the code the head holds, and 15 of 43 findings on a branch that
                # removed a field asked for what the head already had.
                stood = view[since : until + 1]
                text = "\n".join(raw if raw[:1] == "-" else raw[1:] for _number, raw in stood)
                placed.append(((first, last), text, any(raw[:1] == "-" for _number, raw in stood), index))
    if not placed:
        return (None, None, False, None) if joined else None
    # A short quote can stand in several places: measured by review, `return None` at lines 2 and 22, a guideline naming
    # 22 was placed at 2. The diff decides where a quote can stand; the guideline's line only picks among those places,
    # and where it does not, as where it named none, a place the head holds goes before a deleted one, then the first:
    # read by review, a moved line was placed on its deleted copy and read as gone from the branch.
    return min(
        placed,
        key=lambda place: (abs(place[0][0] - finding.line) if finding.line else 0, place[2], place),
    )


def _paired(hunk: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The hunk with each block of removed lines and the added lines right after it read in pairs, an old line beside
    the one that replaced it. Measured live on Copilot over six branches, each replacing a function's docstring and its
    return: git prints a block's every removed line before its every added one, and 16 of 41 findings, which quoted an
    old line beside its new one, were dropped. Only lines of the hunk, and only their order within one block, change."""
    paired: list[tuple[int, str]] = []
    at = 0
    while at < len(hunk):
        end = at
        while end < len(hunk) and hunk[end][1][:1] == "-":
            end += 1
        if end == at:
            paired.append(hunk[at])
            at += 1
            continue
        stop = end
        while stop < len(hunk) and hunk[stop][1][:1] == "+":
            stop += 1
        removed, added = hunk[at:end], hunk[end:stop]
        for index in range(max(len(removed), len(added))):
            paired += removed[index : index + 1] + added[index : index + 1]
        at = stop
    return paired


def _overlaps(x: str, x_lines: Lines, y: str, y_lines: Lines) -> bool:
    """Two findings about the same lines. Where the diff places both, one's lines hold the other's: measured by
    review, folded by text alone, a minor quoting `return None` at line 2 held a blocker whose quote ends in another
    `return None` at line 6, and took it. Where either is not placed, one quote, neither empty, contains the other."""
    if x_lines and y_lines:
        (a, b), (c, d) = x_lines, y_lines
        return (a <= c and d <= b) or (c <= a and b <= d)
    return bool(x and y) and (x in y or y in x)


def from_rules(answers: Mapping[str, Checked]) -> tuple[list[Sourced], dict[str, str]]:
    """The rules' answers as the verdict reads them, a review's and `--try`'s alike: each finding under its check's id
    where the diff places it — its one line, or nowhere for one at a path, as a tree rule's is — and why each rule
    that could not look did not."""
    found = [
        Sourced(check, f, None if f.line is None else (f.line, f.line))
        for check, answer in answers.items()
        for f in answer.findings
    ]
    found += [Sourced(check, f, old=True) for check, answer in answers.items() for f in answer.old]
    return found, {check: answer.could_not_look for check, answer in answers.items() if answer.could_not_look}


def not_read(files: list[Entry], guidelines: Collection[str]) -> dict[str, str]:
    """What no one could read, by path with why. A cut is the guidelines' limit: with none on, the rules
    read every added line, and only what no one can read — binary, a submodule, nothing shown — is a hole."""
    return {f["path"]: f["unread"] for f in files if f["unread"] and (guidelines or not f["cut"])}


def to_verify(guidelines: list[Sourced], rules: list[Sourced], tidies: frozenset[str] = frozenset()) -> list[Sourced]:
    """The guideline findings worth a verifier: all but those that only say again what a rule found — a rule's quote
    and its own overlap, in one file, and the fold takes the rule's up first, by the `_precedence` `dedupe` reads, so
    it keeps the rule's whatever were said of the guideline's. The fold still folds them, as it would without
    `--verify`. Measured by review: with the tie rule written here a second time, a minor defect on a `simplify` tidy's
    lines was kept by the fold and asked of no verifier. Each rule's quote
    is normalised once and compared only within its file, as `dedupe` does: normalised per pair, a thousand of
    ruff's findings in one file and thirty guideline findings measured 147 ms against 7 ms."""
    said: dict[
        str, list[tuple[tuple[int, bool], str, Lines]]
    ] = {}  # file -> each rule finding's precedence, quote, lines
    for rule in rules:
        if (
            rule.old
        ):  # set apart before the fold, so it folds nothing: read by review, its text kept one from a verifier
            continue
        seen = as_seen(rule.finding.evidence)
        said.setdefault(rule.finding.file, []).append((_precedence(rule, tidies), seen, rule.lines))
    asked: list[Sourced] = []
    for item in guidelines:
        first, seen = _precedence(item, tidies), as_seen(item.finding.evidence)
        here = said.get(item.finding.file, ())
        if not any(at <= first and _overlaps(seen, item.lines, quote, lines) for at, quote, lines in here):
            asked.append(item)
    return asked


def _precedence(item: Sourced, tidies: frozenset[str]) -> tuple[int, bool]:
    """Where the fold takes `item` up among readings of the same lines: the most severe first and, on a tie, a defect
    before a tidy, a finding of a check in `tidies`; the checks' order breaks what is left, the rules' first."""
    return -RANK[item.finding.severity], item.source in tidies


@dataclass
class _Held:
    """What one file kept, as the fold asks of it. A span the diff places is kept only where it holds no kept span and
    none holds it, so no two kept spans start on one line, and sorted by start their ends rise too: the spans holding a
    new one lie in one run of them, and those it holds in another, each found by bisection. Each kept finding's turn
    names the first it overlaps, as reading every one in the order kept would."""

    starts: list[int] = field(default_factory=list)
    ends: list[int] = field(default_factory=list)
    placed: list[tuple[int, int]] = field(default_factory=list)  # each placed one's turn and index, by start
    unplaced: list[tuple[int, int, str]] = field(
        default_factory=list
    )  # each one no line places, its turn, index, quote
    every: list[tuple[int, str, Lines]] = field(default_factory=list)  # each one's index, quote and lines, in turn

    def overlapped(self, seen: str, lines: Lines) -> int | None:
        """The index of the first kept finding one quoting `seen` on `lines` overlaps, or None: one no line places
        is read against every quote."""
        if lines is None:
            return next((at for at, quote, held in self.every if _overlaps(seen, None, quote, held)), None)
        first, last = lines
        holding = range(bisect_left(self.ends, last), bisect_right(self.starts, first))
        held = range(bisect_left(self.starts, first), bisect_right(self.ends, last))
        found = [self.placed[at] for run in (holding, held) for at in run]
        found += [(turn, at) for turn, at, quote in self.unplaced if _overlaps(seen, lines, quote, None)][:1]
        return min(found)[1] if found else None

    def keep(self, index: int, seen: str, lines: Lines) -> None:
        turn = len(self.every)
        self.every.append((index, seen, lines))
        if lines is None:
            self.unplaced.append((turn, index, seen))
            return
        at = bisect_left(self.starts, lines[0])
        self.starts.insert(at, lines[0])
        self.ends.insert(at, lines[1])
        self.placed.insert(at, (turn, index))


def dedupe(
    kept: list[Sourced], tidies: frozenset[str] = frozenset()
) -> tuple[list[Sourced], list[tuple[Sourced, Sourced]]]:
    """One finding per span of the diff: the most severe reading first, whole; on a tie a defect before a tidy, a
    finding of a check in `tidies`, and then the first source.

    Measured on Copilot: three guidelines reported the shell finding, each quoting a
    different span of the same two lines, and the report carried it three
    times. Overlapping quotes in one file are one finding — across guidelines, and
    across rules. Whole, not merged: measured with ruff in front of the guidelines,
    keeping the first title at the higher severity made "line too long" a
    blocker. Most severe first, and each finding kept only if it overlaps none
    already kept: measured live, a guideline's blocker quoting a whole function
    replaced the one ruff finding it was compared with, and left the other,
    on the next line, standing beside it. The findings keep the order they came in.
    Each quote is normalised once and compared only within its file:
    the lint rule reads whole files, so a test-heavy branch brings findings by
    the hundred per file, and comparing every pair through `as_seen`'s cache
    measured 689 ms at 1,000 findings against 32 ms this way; most severe
    first costs what the order before it did, 12 ms each on a thousand. And
    a finding is compared with the kept spans that can hold it or lie in it,
    never with each one kept: measured by review, 20,000 keys a line rule found
    in one file took 37 s, 2 x 10^8 comparisons.
    """
    held: dict[str, _Held] = {}  # file -> what it kept
    into: dict[int, int] = {}  # a folded finding's index -> the index of the finding it folded into
    # `sorted` is stable: on a tie the first source comes first.
    for index in sorted(range(len(kept)), key=lambda i: _precedence(kept[i], tidies)):
        item = kept[index]
        seen, here = as_seen(item.finding.evidence), held.setdefault(item.finding.file, _Held())
        survivor = here.overlapped(seen, item.lines)
        if survivor is None:
            here.keep(index, seen, item.lines)
        else:
            into[index] = survivor
    return [item for index, item in enumerate(kept) if index not in into], [
        (kept[i], kept[into[i]]) for i in sorted(into)
    ]


def fold(outcome: Outcome, rules: list[Sourced], order: Sequence[str], tidies: frozenset[str] = frozenset()) -> None:
    """One finding per span of the diff, the first check on a tie: `order` is the report's, the rules before the
    guidelines, so the fold and the report never disagree on which check a line belongs to. A finding of a check in
    `tidies`, which changes how code reads and never what it does, gives way on a tie to a defect: measured on the
    order alone, under `full` `complexity` came before `correctness`, and the report and the JSON named how lines read
    with what they do wrong folded under it."""
    # What was already in a touched file goes apart, before any fold: asked by the owner, never mixed with the change's.
    # The most severe first, as each report groups them: asked by the owner, grouped by importance as the change's are.
    outcome.old = sorted(
        [*outcome.old, *(item for item in rules if item.old)], key=lambda item: -RANK[item.finding.severity]
    )
    rules = [item for item in rules if not item.old]
    kept = sorted(rules + outcome.kept, key=lambda item: order.index(item.source))  # stable: each check's own order
    outcome.kept, outcome.folded = dedupe(kept, tidies)


def at_bar(items: Iterable[Sourced], fail_on: str) -> int:
    """How many of `items` sit at or above `fail_on`. Of the kept: said beside a degraded verdict, which outranks them,
    since a pipeline that lets exit 3 through would otherwise let them through unseen."""
    return sum(at_or_above(item.finding.severity, fail_on) for item in items)


def decide(outcome: Outcome, fail_on: str) -> tuple[str, int]:
    """The verdict and the exit code, from the findings that survived and the guidelines that did not.

    Only kept findings count: one dropped for evidence that is not in the diff must not decide the build. A failed
    guideline is a hole, and so is a file no check can read whole, or one a guideline that runs was not shown whole, and
    so is a finding at the bar that no quote bears out, since a real blocker dropped would approve: a hole never
    approves. So is one at the bar a verifier refuted: the verifier reads the branch's own text, and read by review, a
    payload there could have it refute a real blocker and the review approve.
    """
    if outcome.errors or outcome.unread or outcome.unproven(fail_on):
        return "DEGRADED", EXIT_DEGRADED
    return ("REQUEST_CHANGES", EXIT_FINDINGS) if at_bar(outcome.kept, fail_on) else ("APPROVED", EXIT_CLEAN)

"""A git pre-push hook: what a push sends, reviewed by code alone before it leaves the machine.

Once the reviewer is installed, a repository's hook is a file named `pre-push` in the folder git reads hooks from,
which `git rev-parse --git-path hooks` names, holding two lines, the same on every system. It is executable
(`chmod +x`): git passes over a hook it cannot run, and over one in a folder it does not read, and the push goes
unreviewed.

    #!/bin/sh
    exec adk-review-push "$@"

A repository that uses pre-commit names the hook `adk-review` from `.pre-commit-hooks.yaml` instead, which runs this
command too. Git hands its hook the remote's name and URL and one line a pushed ref; pre-commit reads those lines
itself and names one ref in its environment, the first not deleted, passing over a new ref whose commits the remote
already holds, so a push of several refs is reviewed whole by git's own hook alone. What a ref sends — the commits
neither the ref nor any of the remote's branches hold, as git answers it — is reviewed as one change against the
commits it grows from on the remote's side, or as a whole project where the remote holds none of its history, with
`default-rules`: no model and no key. Then each of those commits, and what each says, is read with `push-history`, the
credential rules: what one commit adds and a later one takes out is in the history the remote receives. Any exit but 0
stops the push and prints why; a clean push, or one that sends nothing new, says nothing. `git push --no-verify`
skips it, as it skips any hook.

The review runs in this process, through git as a review reads it: no `python -m` that could import a module of the
repository pushed from, and no uv to find. Measured before, two hooks reviewed two things, and the one that reviewed
the branch checked out stopped every push from `main` and approved a token on a branch pushed while another was
checked out.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TextIO

from .collect.git import GitError
from .collect.history import push_scope, sent, tag_message
from .core.evidence import entry
from .core.findings import Finding, at_or_above
from .core.paths import matches
from .core.rules import AnyRule, run_rule
from .core.text import lines_of
from .core.verdict import EXIT_INTERRUPTED, EXIT_USAGE
from .deliver.report import safe_line
from .judge.catalog import CONFIG, catalog, load_config, select
from .review import main as review
from .review import settle

#: What git writes for a commit that does not exist: a deleted ref's local side, a new ref's remote side.
NONE = {"0" * 40, "0" * 64}
#: What a push is reviewed with: the default's rules, no model and no key.
PROFILE = "default-rules"
#: The profile each commit a push sends is read with, beside the review of the change they make: of what a rule finds in
#: a commit a later one undoes, a credential alone still harms, since the history the remote receives holds it. A
#: profile in `config.toml` like any other, so a project's own credential rule joins it there.
HISTORY = "push-history"
#: Where a commit's message is named, beside the files it changed.
MESSAGE = "its message"
#: A commit's id as git writes it, shortened where the hook names one.
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

Pushed = list[tuple[str, str | None]]


def refs(lines: Sequence[str]) -> Pushed:
    """Each `(head, held)` git's lines ask to review: the commit a ref sends, and what the remote holds of that ref,
    or None for a new ref; a deleted ref sends nothing to review. The local side is the push's own spelling, spaces and
    all, `main^{/a message}:refs/heads/x`, so each line is read from its end: measured, a line split at every space was
    passed over, and the commit it sent went unreviewed. At git's one space alone: split at U+2028, U+0085 or U+00A0
    too, as `str.split` splits, a line was read as naming `main`. `ValueError` for a line that names no ref."""
    found: Pushed = []
    for line in filter(str.strip, lines):
        parts = line.rsplit(" ", 3)
        if len(parts) != 4:
            raise ValueError(f"git's line {line.strip()!r} names no ref")
        _local, head, _remote, held = parts
        if head not in NONE:
            found.append((head, None if held in NONE else held))
    return found


def requested(argv: Sequence[str], environ: Mapping[str, str], stdin: TextIO) -> tuple[str | None, Pushed] | None:
    """The remote pushed to, and each `(head, held)` to review. Git runs its hook with the remote's name and URL, and
    those and its lines decide: measured before, a variable left in a shell outranked them. pre-commit runs its hook
    with no argument and names the ref a push sends in its environment, and what is new is what the remote's branches
    lack; run by a person, it names no remote, and the range is reviewed as named, or with no ref named, the whole
    project at `HEAD`. Measured by review: arguments a config gave the hook under pre-commit, which passes none of
    git's lines on, were read as git's, and a push holding a token passed unreviewed; and in a push holding a first
    commit, where pre-commit names no range, a range left in a shell was reviewed instead. None where neither runs it,
    at a terminal too, where a read would wait for lines that never come."""
    if argv:
        if stdin.isatty():
            return None
        lines = lines_of(_read(stdin))
        if environ.get("PRE_COMMIT") and not any(map(str.strip, lines)):
            raise ValueError("pre-commit ran the hook with arguments, which it takes none of: remove its `args:`")
        return argv[0], refs(lines)
    if not environ.get("PRE_COMMIT"):
        return None
    if remote := environ.get("PRE_COMMIT_REMOTE_NAME"):  # a push, which pre-commit names in full
        return remote, [(environ.get("PRE_COMMIT_LOCAL_BRANCH") or "HEAD", None)]
    return None, [(environ.get("PRE_COMMIT_TO_REF") or "HEAD", environ.get("PRE_COMMIT_FROM_REF"))]


def _read(stdin: TextIO) -> str:
    """Git's lines as the bytes they are, read as UTF-8: a ref's name can hold any character, and a console's code
    page, Windows' for a pipe, refused one with a traceback."""
    data = stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read()
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data


def command(repository: str, head: str, base: str | None) -> list[str]:
    """The review of what one push sends: against a base, or, with none, the whole project at the head."""
    return [repository, "--head", head, *(["--base", base] if base else ["--all"]), "--profile", PROFILE]


def reviewed(repository: str, pushed: str, bases: Sequence[str]) -> tuple[int, str] | None:
    """None where the change passes its review against one of its bases, else the first review's exit code and report.
    A line the remote holds nowhere is new to every base, so one review that passes leaves none to find; each other
    base a merge brings holds the other side's own changes, which the remote holds already."""
    stopped = None
    for base in bases or [None]:
        said = io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            code = review(command(repository, pushed, base))
        if code == 0:
            return None
        stopped = stopped or (code, said.getvalue())
    return stopped


def history_rules(config: dict | None = None) -> tuple[AnyRule, ...]:
    """The rules `push-history` names, as the review finds and holds them, at or past the review's bar. A commit is
    read line by line and by code alone: a file or tree rule, or a guideline, is `ValueError`, refused rather than
    passed over. `config` is `config.toml` read, where the caller has read it."""
    config = config or load_config(CONFIG)
    chosen = select(catalog(), config["profiles"], HISTORY)
    wider = [rule.check_id for rule in chosen.rules if rule.kind != "line"] + [g.check_id for g in chosen.guidelines]
    if wider:
        raise ValueError(f"{HISTORY} names {', '.join(wider)}: a commit is read line by line, by code alone")
    fail_on = config["review"]["fail_on"]
    return tuple(rule for rule in chosen.rules if at_or_above(rule.severity, fail_on))


def in_history(repository: str, remote: str | None, head: str, held: str | None, read: Sequence[str]) -> list[str]:
    """Each place the history rules find in the commits a push of `head` sends — the lines each adds, what each says,
    and what a pushed tag says — named by its commit, none that a head in `read`, read already, reaches; a rule that
    could not look is named the same way, a hole, and so is a binary file a commit writes, which no rule can read,
    unless the operator's skip list names it, as a review of the change names one."""
    config = load_config(CONFIG)
    rules = history_rules(config)  # first: a profile that cannot read a commit is refused before git runs
    skip = config["review"]["skip"]
    tag = tag_message(repository, head)
    said = [(head, tag, [])] if tag is not None else []  # a tag's words are in no commit
    found = []
    for commit, message, files in itertools.chain(said, sent(repository, remote, head, held, read)):
        unread = [f["path"] for f in files if f.get("binary") and not matches(f["path"], *skip)]
        found += [f"{_named(commit)}, {path}: a binary file, which no rule can read" for path in unread]
        # As a line rule reads a change: a path and the lines added. The message is all added; a file's letter is
        # not read here.
        entries = [
            entry(MESSAGE, "A", None, list(enumerate(lines_of(message), 1))),
            *(entry(f["path"], "M", None, f["added"]) for f in files),
        ]
        for rule in rules:
            answer = run_rule(rule, entries, None)
            found += [f"{_named(commit)}, {_where(f)}: {rule.title} ({rule.check_id})" for f in answer.findings]
            if answer.could_not_look:
                found.append(f"{_named(commit)}: {answer.could_not_look}")
    return found


def _where(finding: Finding) -> str:
    """Where in a commit a finding is: a file's line, or its message's."""
    return f"{MESSAGE}, line {finding.line}" if finding.file == MESSAGE else f"{finding.file}:{finding.line}"


def _named(ref: str) -> str:
    """A commit as the hook names it: an id shortened, any other name whole."""
    return ref[:9] if _SHA.fullmatch(ref) else ref


def _say(sentence: str, lead: str = "pre-push: ") -> None:
    """One line of the hook's own, made safe as the report's prose is: a ref, a path and git's words can be the pushed
    branch's, a path in a commit with any byte git quotes, an escape sequence or a CI runner's command among them."""
    print(f"{lead}{safe_line(sentence)}")


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a report printed on a Windows console
    try:
        asked = requested(sys.argv[1:] if argv is None else argv, os.environ, sys.stdin)
    except ValueError as exc:  # a hole never approves
        _say(f"{exc}; nothing was reviewed, so push with --no-verify to skip the review")
        return 1
    if asked is None:
        _say("a pre-push hook, run by git or pre-commit with what a push sends; adk-review reviews a branch")
        return EXIT_USAGE
    remote, pushed = asked
    repository = os.getcwd()  # git runs a hook at the top of the working tree it pushes from, and so does pre-commit
    done = _Done()
    try:  # the first ref refused stops the push
        return 0 if all(_passes(repository, remote, head, held, done) for head, held in dict.fromkeys(pushed)) else 1
    except KeyboardInterrupt:
        _say("interrupted: nothing was pushed")
        return EXIT_INTERRUPTED


@dataclass
class _Done:
    """What a push has had reviewed and read so far: a commit pushed under two names, a branch and the tag `git push
    --follow-tags` sends beside it, is reviewed once and its commits read once. Measured: a first push of 2,000 commits
    and its tag took 32.8 s, half of it the same review and the same history over again."""

    changes: set[tuple[str, ...]] = field(default_factory=set)  # each change reviewed: its commit, then its bases
    heads: list[str] = field(default_factory=list)  # each head whose commits were read


def _passes(repository: str, remote: str | None, head: str, held: str | None, done: _Done) -> bool:
    """Whether what a push of `head` sends may leave: its review passed, and no commit of it holds a credential. Each
    refusal is said, and a hole never approves: the push waits for a review that can run."""
    try:
        scope = push_scope(repository, remote, head, held)
    except GitError as exc:
        _say(f"what {_named(head)} sends could not be listed ({exc}); push with --no-verify")
        return False
    if scope is not None and (change := (scope[0], *scope[1])) not in done.changes:
        if (stopped := reviewed(repository, *scope)) is not None:
            code, said = stopped
            sys.stdout.write(said)  # the review's own report, made safe as it was written
            _say(f"the review of {_named(head)} ended in exit {code}; push with --no-verify to skip it")
            return False
        done.changes.add(change)
    if remote is None and held is None:  # by hand, the whole project: nothing is sent, no history to read
        return True
    try:
        found = in_history(repository, remote, head, held, done.heads)
    except (GitError, ValueError) as exc:
        _say(f"the commits {_named(head)} sends could not be read ({exc}); push with --no-verify")
        return False
    done.heads.append(head)
    if found:
        _say(f"the history {_named(head)} sends holds a credential, or a file no rule can read, commit by commit:")
        for place in found:
            _say(place, lead="  ")
        _say(
            "rewrite the commits or the tag that hold it (git rebase -i), and rotate a credential that has left this "
            "machine; push with --no-verify to skip the review"
        )
    return not found


def run() -> int:
    """The process boundary of the installed `adk-review-push`: a review's process-wide settings, then `main`."""
    settle()
    return main()


if __name__ == "__main__":
    raise SystemExit(run())

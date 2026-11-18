"""A workflow step whose script expands a value an outsider writes, such as a pull request's title, into the code it
runs."""

from __future__ import annotations

import contextlib
import re

from ..rule_helpers import ChangedFile, FileRule, Hit, UnreadError, matches

#: Where GitHub runs a step's script: a workflow, and an action's own metadata, whose composite steps run them too.
READ = (".github/workflows/*.yml", ".github/workflows/*.yaml", "action.yml", "action.yaml")
#: An expression naming a value whoever opens an issue, a pull request, a comment, a commit, a branch or a wiki page
#: writes, as GitHub's guide to script injection names them: a title, a body, a message, a branch's name, an author's
#: name or email; or `toJSON` of the whole context, the whole event or any part of an object they write, which writes
#: all it holds. An event's own fields an owner sets, such as `github.event.repository.name`, are not among them. Read
#: whatever its case, as GitHub reads a name and a function's, and up to the expression's own `}}`, past a `{0}` that
#: `format` reads, never past the next `${{`: measured, one line of `${{ ` held the search four times as long for
#: each doubling, 14.8 s at 80,000 characters.
_ARRAY = r"(?:\[[^\]]*\]|\.\*)?"
_COMMIT = r"(?:message|(?:author|committer)\.(?:email|name))"
#: The event's objects an outsider writes, each of which `toJSON` dumps whole; the fields above name each of them too.
_AUTHORED = (
    r"(?:issue|pull_request|discussion|comment|review|review_comment"
    r"|head_commit|commits|pages|workflow_run|merge_group)"
)
UNTRUSTED = re.compile(
    r"\$\{\{(?:(?!\}\}|\$\{\{).)*?(\bgithub\.(?:"
    r"head_ref"
    r"|event\.(?:issue|pull_request|discussion)\.(?:title|body)"
    r"|event\.(?:comment|review|review_comment)\.body"
    r"|event\.pull_request\.head\.(?:ref|label|repo\.default_branch)"
    rf"|event\.(?:workflow_run\.|merge_group\.)?(?:head_commit|commits{_ARRAY})\.{_COMMIT}"
    rf"|event\.pages{_ARRAY}\.(?:page_name|title)"
    r"|event\.workflow_run\.(?:head_branch|display_title)"
    r")\b"
    rf"|\btojson\(github(?:\.event(?:\.{_AUTHORED}[\w.\[\]*]*)?)?\s*\))",
    re.IGNORECASE,
)
#: The spaces GitHub's lexer skips between an expression's tokens, around a `.` and before a `(`, left out before the
#: search: `github . head_ref` is the branch's name.
_SPACED = re.compile(r"\s*([.(])\s*")
#: A key whose value is code a step runs, `run:` for a shell and `script:` for `actions/github-script`, as a step
#: (`- run:`) or a key writes it, its name quoted or not: GitHub puts the expression's value into that code before it
#: runs. What comes before the key's name, the indent and a list item's dash, is captured: its length is the key's
#: column.
SCRIPT = re.compile(r"""^(\s*(?:-\s+)?)(["']?)(run|script)\2\s*:""")
#: The trigger that runs a workflow with the base's token and secrets for an outsider's pull request, and the key that
#: names a workflow's triggers, at the top of the file.
TARGET = re.compile(r"\bpull_request_target\b")
ON = re.compile(r"""^(["']?)on\1\s*:""")
#: A checkout step, and its `ref:` or `repository:` naming the pull request's own code: its head, by commit or branch,
#: the merge GitHub makes of it, or the fork it comes from, as an expression holds it among others, `head.sha ||
#: github.sha`. Read in two steps, the key and then the name, each once along the line: read by the second review, one
#: pattern for both took 4.6 s over a line of 40,000 characters.
CHECKOUT = re.compile(r"""^\s*(?:-\s+)?(["']?)uses\1\s*:\s*["']?actions/checkout@""", re.IGNORECASE)
CHECKOUT_KEY = re.compile(r"""^\s*(["']?)(?:ref|repository)\1\s*:""", re.IGNORECASE)
HEAD = re.compile(
    r"github\.(?:event\.pull_request\.(?:head\.(?:sha|ref|repo\.(?:full_name|clone_url|ssh_url))|merge_commit_sha)"
    r"|head_ref)\b|refs/pull/",
    re.IGNORECASE,
)
#: A script that checks out the pull request's code itself: `gh pr checkout`, or a fetch, then its ref.
GH_CHECKOUT = re.compile(r"\bgh\s+pr\s+checkout\b", re.IGNORECASE)
GIT_FETCH = re.compile(r"\bgit\s+fetch\b", re.IGNORECASE)
PULL_REF = re.compile(r"\b(?:refs/)?pull/", re.IGNORECASE)
#: Where GitHub reads a workflow's triggers: a workflow, never an action's metadata.
WORKFLOWS = READ[:2]
#: What a checkout of the pull request's own code under `pull_request_target` is, needs, and why.
CHECKOUT_TITLE = "a pull_request_target job checks out the pull request's own code"
CHECKOUT_FIX = (
    "Run the pull request's code under pull_request, with no secrets. Under pull_request_target, check out the base."
)
CHECKOUT_WHY = "The job has the base's token and secrets, and the steps after the checkout run an outsider's code."
#: The key whose `run:` holds the shell and the folder a job's scripts run in, and no script.
DEFAULTS = re.compile(r"""^\s*(["']?)defaults\1\s*:""")
#: What opens a string in a script, and closes it again: a quote, or a template literal's backtick.
_QUOTES = ("'", '"', "`")
#: A script that starts on its key's own line, up to its first character: the key, and the quote YAML itself may put
#: around the whole script, which is none of JavaScript's.
_OWN = re.compile(r"""^\s*(?:-\s+)?(["']?)(?:run|script)\1\s*:\s*["']?""")


class ScriptInjection(FileRule):
    """GitHub writes an expression's value into a step's script before the script runs, so a pull request titled
    `"; curl … | sh #` runs as code, with the job's token and secrets: the attack GitHub's guide to script injection
    names. A value passed through `env:` is data the script reads as a variable, and is not a finding, nor is one passed
    to an action under `with:`, which takes it as an argument, nor `toJSON` that `actions/github-script` reads outside
    any quote, which JavaScript reads as a value. Inside a string of the script `toJSON` is named, wherever on its line
    the string opens: JSON's own quotes end a quoted string, and a template reads a backtick and `${` in the value.
    Each added line is read as the diff shows it, with the lines above it that say which key holds it, so a workflow
    past the size limits is read as far as its diff shows it. Not read: an expression written with brackets,
    `github['head_ref']`, or wrapped over two lines; a step written in YAML's flow style, `- {run: …}`; a script
    reached through a YAML alias, `run: *script`; a value passed through `env:` and then written into the script as
    `${{ env.NAME }}`; and `toJSON` in a template that a line above its own opened. And under
    `pull_request_target`, which runs with the base's token and secrets, a checkout of the pull request's own code,
    its head or the merge GitHub makes of it, the "pwn request" GitHub's guide names: read by validation, no rule named
    it. Named where the change added the trigger, the checkout or its `ref:`; not read in a workflow too large to read
    whole, nor a checkout whose ref an expression builds from another value."""

    id = "script-injection"
    severity = "major"
    reads_tests = True  # a workflow named `test_*.yml` is a test by its name, and runs with the job's secrets
    security = True  # a verifier may dispute its finding, never drop it
    title = "a workflow script expands a value an outsider writes, such as a pull request's title"
    fix = 'Pass the value through `env:`. Quote the variable in the script, as `"$TITLE"`.'
    why = "A title with shell syntax runs as a command in CI, with the workflow's token and secrets."
    bad = (
        (".github/workflows/ci.yml", '      - run: echo "${{ github.event.pull_request.title }}"\n'),
        (".github/workflows/ci.yml", "        run: |\n          git checkout ${{ github.head_ref }}\n"),
        ("action.yml", "      with:\n        script: |\n          const body = `${{ github.event.issue.body }}`\n"),
    )
    good = (
        (".github/workflows/ci.yml", "        env:\n          TITLE: ${{ github.event.pull_request.title }}\n"),
        (".github/workflows/ci.yml", '      - run: echo "${{ github.event.repository.name }}"\n'),
        (".github/workflows/ci.yml", "    if: ${{ github.head_ref == 'main' }}\n"),
        ("docs/ci.md", '- run: echo "${{ github.head_ref }}"\n'),
    )

    def check(self, file: ChangedFile) -> list[tuple[int | None, str] | Hit]:
        if not matches(file.path, *READ):
            return []
        found: list[tuple[int | None, str] | Hit] = []
        held: dict[int, str | None] = {}  # each line asked, and the script key that holds it
        for number in file.new_lines(*READ):  # a workflow moved into place adds no line, and every line of it runs now
            line = file.near(number) or ""
            text = _SPACED.sub(r"\1", line)
            if not UNTRUSTED.search(text) or (key := _script_of(file, number, line, held)) is None:
                continue
            # How far the line is read, and the string it stands in there: read once, however many values it holds.
            at, inside = (own.end() if (own := _OWN.match(text)) else 0), ""
            for named in UNTRUSTED.finditer(text):
                # `toJSON` in a script is a JavaScript value, unless a string of the script holds it. Read by review
                # of another tool's report: asked only whether a quote stood right before it, a word between the two,
                # `Issue: `, let it pass.
                if key != "run" and named[1].lower().startswith("tojson"):
                    inside, at = _string_at(text, at, named.start(), inside), named.start()
                    if inside not in _QUOTES:
                        continue
                found.append((number, f"`{named[1]}` is written into the script before it runs"))
                break
        return [*found, *_checked_out(file)]


def _checked_out(file: ChangedFile) -> list[Hit]:
    """Each step of a workflow `pull_request_target` runs that checks out the pull request's own code, once a step: a
    checkout's `ref:` or `repository:`, or a script's `gh pr checkout` or fetch of its ref, where the change added the
    trigger, the step's `uses:` or that line, or moved the workflow into place. Of a file not read whole, a hole where
    a line its diff adds may be one, its trigger past what the diff shows, and none where none is. Read by review: none
    at all was a review that said nothing of a checkout it could not judge."""
    if not matches(file.path, *WORKFLOWS):
        return []  # an action's metadata names no trigger
    if not file.read_whole:
        shown = (file.near(number) or "" for number in sorted(file.added))
        if any(TARGET.search(text) or _checkout_line(text) for text in shown):
            with contextlib.suppress(UnreadError):  # asked, it marks the file missed: a hole, the script findings kept
                file.lines  # noqa: B018
        return []
    lines = file.lines
    triggers = _triggers(lines)
    if not triggers:
        return []
    added = set(file.new_lines(*WORKFLOWS))  # every line of a workflow moved into place runs now
    trigger_added = not added.isdisjoint(triggers)
    found, steps, held = [], set(), {}
    for number, text in enumerate(lines, 1):
        if CHECKOUT_KEY.match(text) and _names_head(text):
            step = _step_of(lines, number)
            uses = [at for at in step if CHECKOUT.match(lines[at - 1])]
            named = bool(uses) and step.start not in steps and (trigger_added or not added.isdisjoint({number, *uses}))
        elif trigger_added or number in added:
            step = range(number, number + 1)
            named = _fetches_head(text) and _script_of(file, number, text, held) is not None
        else:
            continue
        if named:
            steps.add(step.start)
            quoted = ("+" if number in file.added else " ") + text
            found.append(Hit(file.path, number, quoted, title=CHECKOUT_TITLE, fix=CHECKOUT_FIX, why=CHECKOUT_WHY))
    return found


def _uncommented(text: str) -> str:
    """A line with its comment, YAML's ` #` to its end, left out, GitHub's lexer's spaces around a `.` too: read by the
    second review, `ref: main  # not ${{ github.head_ref }}` and `head . sha` misled it each way."""
    return _SPACED.sub(r"\1", re.split(r"(?:^|\s)#", text, maxsplit=1)[0])


def _names_head(text: str) -> bool:
    """Whether a checkout key's value names the pull request's own code, in an expression or as a ref of `pull/`."""
    value = _uncommented(text)
    for named in HEAD.finditer(value):
        if named[0].lower() == "refs/pull/" or value.rfind("${{", 0, named.start()) > value.rfind(
            "}}", 0, named.start()
        ):
            return True
    return False


def _fetches_head(text: str) -> bool:
    """Whether a script line checks out the pull request's code: `gh pr checkout`, or a `git fetch` of `pull/`."""
    value = _uncommented(text)
    fetch = GIT_FETCH.search(value)
    return bool(GH_CHECKOUT.search(value) or (fetch and PULL_REF.search(value, fetch.end())))


def _checkout_line(text: str) -> bool:
    """Whether a line can check out the pull request's code, as a step's key or a script's command."""
    return bool(CHECKOUT_KEY.match(text) and _names_head(text)) or _fetches_head(text)


def _triggers(lines: tuple[str, ...]) -> list[int]:
    """The lines of the workflow's `on:` key that name `pull_request_target`: its own line and those under it, a
    sequence at its column among them. Read by review: a search of every line read a condition, `if:
    github.event_name != 'pull_request_target'`, as the trigger; and by the second review, `on:` with its sequence,
    `- pull_request_target`, at its own column was none."""
    found: list[int] = []
    inside = False
    for number, text in enumerate(lines, 1):
        if text.strip() and not text[:1].isspace() and not text.startswith(("#", "-")):
            inside = bool(ON.match(text))
        if inside and TARGET.search(_uncommented(text)):
            found.append(number)
    return found


def _step_of(lines: tuple[str, ...], number: int) -> range:
    """The lines of the step that holds line `number`: from the list item above it, `- `, less indented than the line,
    to the line before the next one as little indented."""
    depth = _indent(lines[number - 1])
    start = next(
        (
            at
            for at in range(number, 0, -1)
            if lines[at - 1].lstrip().startswith("-") and _indent(lines[at - 1]) < depth
        ),
        number,
    )
    dash = _indent(lines[start - 1])
    end = next(
        (at for at in range(start + 1, len(lines) + 1) if lines[at - 1].strip() and _indent(lines[at - 1]) <= dash),
        len(lines) + 1,
    )
    return range(start, end)


def _string_at(text: str, start: int, end: int, held: str) -> str:
    """The string of a script's line that `end` stands in, by the quote that opened it: "" outside any, and "/" in a
    comment, which runs to the line's end. Read on from `start`, where the line stood in `held`: a quote or a
    template's backtick opens a string and the same one closes it, a backslash keeps the character after it from
    doing either, and `//` outside a string opens a comment."""
    at = start
    while at < end and held != "/":
        mark = text[at]
        if mark == "\\":
            at += 1  # the character after it is read by neither branch below
        elif held:
            held = "" if mark == held else held
        elif mark in _QUOTES:
            held = mark
        elif text.startswith("//", at):
            held = "/"
        at += 1
    return held


def _script_of(file: ChangedFile, number: int, line: str, held: dict[int, str | None]) -> str | None:
    """The key whose script line `number` is part of, `run` or `script`, or None: its own line's key, or any key YAML
    indents it under, which holds a script and nothing else, `defaults:`'s `run:` aside. Each line less indented than
    the last holds it, up to the top, and holds it through its key only where the key's name starts left of it: measured
    on real workflows, a script's own line wrapped deeper, a `curl` argument, was held first by a line of the script;
    and `env:` beside a step's `- run:` is that step's own key, less indented only by the dash. A line beside one asked
    already, or holding it, is held by the same keys, so its answer, kept in `held`, is this one's: measured, 20,000
    lines of one block took 40 s, each walking past every line above it. None too where the diff shows no key."""
    if (own := SCRIPT.match(line)) and not _defaults(file, number, own):
        return own[3]
    answer, depth = None, _indent(line)
    for above in range(number - 1, 0, -1):
        text = file.near(above)
        if not depth or text is None:  # a line at the top, which nothing holds, or past what the diff shows
            break
        if not text.strip() or (indent := _indent(text)) > depth:  # a blank line, or one this line does not touch
            continue
        if indent < depth and (key := SCRIPT.match(text)) and len(key[1]) < depth:
            answer = None if _defaults(file, above, key) else key[3]
            break
        if above in held:
            answer = held[above]
            break
        depth = min(depth, indent)
    held[number] = answer
    return answer


def _defaults(file: ChangedFile, number: int, key: re.Match[str]) -> bool:
    """Whether the `run:` on line `number` is `defaults:`'s, the line that holds it: a step's, written `- run:` or under
    one, never is, so a step's key looks only as far up as its step."""
    if key[1].lstrip().startswith("-"):
        return False
    depth = len(key[1])
    for above in range(number - 1, 0, -1):
        text = file.near(above)
        if text is None:
            return False
        if text.strip() and _indent(text) < depth:
            return bool(DEFAULTS.match(text))
    return False


def _indent(line: str) -> int:
    """How far a line is indented, which says in YAML what holds it."""
    return len(line) - len(line.lstrip())

"""A workflow step whose script expands a value an outsider writes, such as a pull request's title, into the code it
runs."""

from __future__ import annotations

import re

from ..rule_helpers import ChangedFile, FileRule, matches

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
#: The key whose `run:` holds the shell and the folder a job's scripts run in, and no script.
DEFAULTS = re.compile(r"""^\s*(["']?)defaults\1\s*:""")
#: What a value quoted in a script ends with: a quote, or a template literal's backtick.
_QUOTES = ("'", '"', "`")


class ScriptInjection(FileRule):
    """GitHub writes an expression's value into a step's script before the script runs, so a pull request titled
    `"; curl … | sh #` runs as code, with the job's token and secrets: the attack GitHub's guide to script injection
    names. A value passed through `env:` is data the script reads as a variable, and is not a finding, nor is one passed
    to an action under `with:`, which takes it as an argument, nor `toJSON` that `actions/github-script` reads outside
    any quote, which JavaScript reads as a value. Each added line is read as the diff shows it, with the lines above it
    that say which key holds it, so a workflow past the size limits is read as far as its diff shows it. Not read: an
    expression written with brackets, `github['head_ref']`, or wrapped over two lines; a step written in YAML's flow
    style, `- {run: …}`; a script reached through a YAML alias, `run: *script`; and a value passed through `env:` and
    then written into the script as `${{ env.NAME }}`."""

    id = "script-injection"
    severity = "major"
    reads_tests = True  # a workflow named `test_*.yml` is a test by its name, and runs with the job's secrets
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

    def check(self, file: ChangedFile) -> list[tuple[int | None, str]]:
        if not matches(file.path, *READ):
            return []
        found: list[tuple[int | None, str]] = []
        held: dict[int, str | None] = {}  # each line asked, and the script key that holds it
        for number in file.new_lines(*READ):  # a workflow moved into place adds no line, and every line of it runs now
            line = file.near(number) or ""
            text = _SPACED.sub(r"\1", line)
            if not UNTRUSTED.search(text) or (key := _script_of(file, number, line, held)) is None:
                continue
            for named in UNTRUSTED.finditer(text):
                if key == "run" or not named[1].lower().startswith("tojson") or text[: named.start()].endswith(_QUOTES):
                    found.append((number, f"`{named[1]}` is written into the script before it runs"))
                    break
        return found


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

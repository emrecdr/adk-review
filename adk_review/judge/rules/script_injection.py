"""A workflow step whose script expands a value an outsider writes, such as a pull request's title, into the code it
runs."""

from __future__ import annotations

import re

from ..rule_helpers import ChangedFile, FileRule, matches

#: Where GitHub runs a step's script: a workflow, and an action's own metadata, whose composite steps run them too.
READ = (".github/workflows/*.yml", ".github/workflows/*.yaml", "action.yml", "action.yaml")
#: An expression naming a value whoever opens an issue, a pull request, a comment, a commit or a branch writes, as
#: GitHub's guide to script injection names them: a title, a body, a message, a branch's name, an author's name or
#: email. An event's own fields an owner sets, such as `github.event.repository.name`, are not among them.
_ARRAY = r"(?:\[[^\]]*\]|\.\*)?"
UNTRUSTED = re.compile(
    r"\$\{\{[^}]*?\b("
    r"github\.head_ref"
    r"|github\.event\.(?:issue|pull_request|discussion)\.(?:title|body)"
    r"|github\.event\.(?:comment|review|review_comment)\.body"
    r"|github\.event\.pull_request\.head\.(?:ref|label|repo\.default_branch)"
    rf"|github\.event\.(?:head_commit|commits{_ARRAY})\.(?:message|(?:author|committer)\.(?:email|name))"
    rf"|github\.event\.pages{_ARRAY}\.page_name"
    r"|github\.event\.workflow_run\.(?:head_branch|display_title|head_commit\.(?:message|author\.(?:email|name)))"
    r")\b"
)
#: A key whose value is code a step runs, `run:` for a shell and `script:` for `actions/github-script`, as a step
#: (`- run:`) or a key writes it: GitHub puts the expression's value into that code before it runs. What comes before
#: the key's name, the indent and a list item's dash, is captured: its length is the key's column.
SCRIPT = re.compile(r"^(\s*(?:-\s+)?)(?:run|script)\s*:")


class ScriptInjection(FileRule):
    """GitHub writes an expression's value into a step's script before the script runs, so a pull request titled
    `"; curl … | sh #` runs as code, with the job's token and secrets: the attack GitHub's guide to script injection
    names. A value passed through `env:` is data the script reads as a variable, and is not a finding, nor is one passed
    to an action under `with:`, which takes it as an argument. Each added line is read as the diff shows it, with the
    lines above it that say which key holds it, so a workflow past the size limits is read as far as its diff shows it;
    an expression written with brackets, `github['head_ref']`, is not read."""

    id = "script-injection"
    severity = "major"
    reads_tests = True  # a workflow named `test_*.yml` is a test by its name, and runs with the job's secrets
    title = "a workflow script expands a value an outsider writes, such as a pull request's title"
    fix = 'Pass the value through `env:`. Quote the variable in the script, as `"$TITLE"`.'
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
        # A workflow moved into place adds no line, and every line of it runs there now.
        for number in range(1, len(file.lines) + 1) if file.arrived(*READ) else sorted(file.added):
            line = file.near(number) or ""
            if (named := UNTRUSTED.search(line)) and _in_script(file, number, line):
                found.append((number, f"`{named[1]}` is written into the script before it runs"))
        return found


def _in_script(file: ChangedFile, number: int, line: str) -> bool:
    """Whether line `number` is part of a script: a `run:` or `script:` key's own line, or any line YAML indents past
    one, which holds a script and nothing else. Each line less indented than the last holds it, up to the top, and holds
    it through its key only where the key's name starts left of it. Measured on real workflows: a script's own line
    wrapped deeper, a `curl` argument, was held first by a line of the script; and `env:` beside a step's `- run:` is
    that step's own key, less indented only by the dash. A line no such key holds, under `env:`, `with:` or `if:`, is
    not part of a script; nor is one whose key the diff does not show."""
    if SCRIPT.match(line):
        return True
    depth = len(line) - len(line.lstrip())
    for above in range(number - 1, 0, -1):
        if not depth:  # a line at the top: nothing holds it
            return False
        text = file.near(above)
        if text is None:  # past what the diff shows: no key is known
            return False
        if text.strip() and (indent := len(text) - len(text.lstrip())) < depth:  # a blank line says nothing
            if (script := SCRIPT.match(text)) and len(script[1]) < depth:
                return True
            depth = indent
    return False

"""A workflow or an action that runs another action at a tag, a branch or a short SHA, not a full commit SHA."""

from __future__ import annotations

import re

from ..rule_helpers import ChangedFile, FileRule, matches

#: Where GitHub reads `uses:`: a workflow, and an action's own metadata, whose composite steps run other actions.
READ = (".github/workflows/*.yml", ".github/workflows/*.yaml", "action.yml", "action.yaml")
#: A `uses:` value as a step (`- uses:`) or a job (`uses:`) writes it, quoted or not, a comment after it: the action or
#: reusable workflow, then the ref after its `@`. A local action, `./…`, names no ref, and a Docker image no `@` but a
#: digest's, which is read and passed.
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*[\"']?([^\s\"'#@]+)@([^\s\"'#]+)")
#: The one ref that cannot be moved: a full commit SHA. GitHub refuses a shorter one.
SHA = re.compile(r"[0-9a-f]{40}")


class PinnedActions(FileRule):
    """A tag or a branch can be moved to other code after the review, and every run of the workflow then runs it, with
    the job's secrets: tj-actions/changed-files' tags were, in March 2025 (CVE-2025-30066), and only the workflows that
    pinned it by commit were safe. Each added `uses:` is read as the diff shows it, so a workflow past the size limits
    is read; a pin to a fork's commit, which GitHub also resolves, needs the network to tell apart, and is not."""

    id = "pinned-actions"
    severity = "major"
    reads_tests = True  # a workflow named `test_*.yml` is a test by its name, and runs with the job's secrets
    title = "a workflow runs an action at a tag, a branch or a short SHA, not its full commit SHA"
    fix = "Pin it by its full 40-character commit SHA, its version in a comment after it (`@<sha> # v4.2.2`)."
    why = "A tag or a branch can move to other code at any time. That code then runs in CI with its secrets."
    bad = (
        (".github/workflows/ci.yml", "      - uses: actions/checkout@v4\n"),
        ("action.yml", "    - uses: 'actions/setup-node@main'\n"),
    )
    good = (
        (".github/workflows/ci.yml", "  - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2\n"),
        (".github/workflows/ci.yml", "      - uses: ./.github/actions/setup\n"),
        ("docs/ci.md", "- uses: actions/checkout@v4\n"),
    )

    def check(self, file: ChangedFile) -> list[tuple[int | None, str]]:
        if not matches(file.path, *READ):
            return []
        found: list[tuple[int | None, str]] = []
        # A workflow moved into place adds no line, and every line of it runs there now.
        for number in file.new_lines(*READ):
            used = USES.match(file.near(number) or "")
            if used and not used[1].startswith(("./", "docker://")) and not SHA.fullmatch(used[2]):
                found.append((number, f"{used[1]} runs at `{used[2]}`, not a full commit SHA"))
        return found

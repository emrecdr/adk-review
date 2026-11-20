"""A workflow or an action that runs another action at a tag, a branch or a short SHA, not a full commit SHA."""

from __future__ import annotations

import re

from ..rule_helpers import ChangedFile, FileRule, matches

#: Where GitHub reads `uses:`: a workflow, and an action's own metadata, whose composite steps run other actions.
READ = (".github/workflows/*.yml", ".github/workflows/*.yaml", "action.yml", "action.yaml")
#: A `uses:` value as a step (`- uses:`) or a job (`uses:`) writes it, quoted or not, a comment after it: the action or
#: reusable workflow, then the ref after its `@`. A local action, `./…`, names no ref, and a Docker image no `@` but a
#: digest's, which is read and passed. The key in quotes, or with a space before its colon, is the same key: read by
#: an audit, `"uses": actions/checkout@v4` and `uses : actions/setup-python@v5` named none.
USES = re.compile(r"""^\s*(?:-\s+)?(?:uses|"uses"|'uses')\s*:\s*["']?([^\s"'#@]+)@([^\s"'#]+)""")
#: A `uses:` key whose value YAML reads from the line under it: nothing after the key, or a folded or literal block's
#: `>` or `|`, a comment after it. Read by an audit, and reproduced: `uses:` on one line and `actions/checkout@v4` on
#: the next was read as no key at all.
OPEN = re.compile(r"""^\s*(?:-\s+)?(?:uses|"uses"|'uses')\s*:\s*(?:[>|][-+]?\s*)?(?:#.*)?$""")
#: The one ref that cannot be moved: a full commit SHA. GitHub refuses a shorter one.
SHA = re.compile(r"[0-9a-f]{40}")


class PinnedActions(FileRule):
    """A tag or a branch can be moved to other code after the review, and every run of the workflow then runs it, with
    the job's secrets: tj-actions/changed-files' tags were, in March 2025 (CVE-2025-30066), and only the workflows that
    pinned it by commit were safe. Each added `uses:` is read as the diff shows it, so a workflow past the size limits
    is read; a pin to a fork's commit, which GitHub also resolves, needs the network to tell apart, and is not, nor is
    a step in YAML's flow style, `- {uses: …}`, which none of 702 steps in 100 public workflows wrote."""

    id = "pinned-actions"
    severity = "major"
    reads_tests = True  # a workflow named `test_*.yml` is a test by its name, and runs with the job's secrets
    security = True  # a verifier may dispute its finding, never drop it
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
        new = file.new_lines(*READ)
        added = set(new)
        for number in new:
            used = USES.match(_with_value(file, number, added))
            if used and not used[1].startswith(("./", "docker://")) and not SHA.fullmatch(used[2]):
                found.append((number, f"{used[1]} runs at `{used[2]}`, not a full commit SHA"))
        return found


def _with_value(file: ChangedFile, number: int, new: set[int]) -> str:
    """Line `number` as `USES` reads it: a key whose value YAML reads from the line under it, with that value; and that
    value's line, under a key the change kept, as the key with its value. Its own line where neither holds."""
    line = file.near(number) or ""
    if OPEN.match(line):
        return "uses: " + (file.near(number + 1) or "").strip()
    if number - 1 not in new and OPEN.match(file.near(number - 1) or ""):
        return "uses: " + line.strip()
    return line

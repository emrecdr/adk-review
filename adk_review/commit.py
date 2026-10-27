"""A git pre-commit hook: what a commit will hold, reviewed by code alone before git writes it.

Once the reviewer is installed, a repository's hook is a file named `pre-commit` in the folder git reads hooks from,
written by the lines GUIDE.md's "Before a commit" gives: it runs this command at the top of the working tree, which
reviews the index git hands it as `--staged` reads it, against `HEAD`, with `default-rules`: no model and no key. Git
hands `git commit -a` and `git commit <paths>` an index of their own, named in `GIT_INDEX_FILE`, which the review reads.
Any exit but 0 stops the commit and prints why; a clean commit, or one that adds nothing to review, a message-only
`--amend` among them, says nothing. `git commit --no-verify` skips it, as it skips any hook. A repository that uses
pre-commit names the hook `adk-review-commit` from `.pre-commit-hooks.yaml` instead, which runs this command too.

The review runs in this process, as the pre-push hook's does: no `python -m` that could import a module of the
repository it commits to.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from collections.abc import Sequence

from .collect.git import GitError, list_changed, preflight_staged
from .core.verdict import EXIT_DEGRADED, EXIT_INTERRUPTED, EXIT_TERMINATED, EXIT_USAGE
from .deliver.report import safe_line
from .review import Terminated, settle
from .review import main as review

PROFILE = "default-rules"


def main(argv: Sequence[str] | None = None) -> int:
    """The hook, with the command's last resort: whatever stops it ends as one sentence, Ctrl-C as the shell's 130, a
    job's SIGTERM as 143, any other error as 3, never a traceback nor Python's own exit 1. Read by review: Ctrl-C was
    caught around the review alone, and one while git listed the index ended in a traceback."""
    try:
        return _main(argv)
    except KeyboardInterrupt:
        _say("interrupted: git wrote nothing")
        return EXIT_INTERRUPTED
    except Terminated:
        _say("terminated: git wrote nothing")
        return EXIT_TERMINATED
    except Exception as exc:  # noqa: BLE001 -- a crash must never read as a verdict
        _say(
            f"the review of what this commit holds did not finish ({type(exc).__name__}: {exc}). "
            "Commit with --no-verify"
        )
        return EXIT_DEGRADED


def _main(argv: Sequence[str] | None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a report printed on a Windows console
    if sys.argv[1:] if argv is None else argv:
        _say("a pre-commit hook takes no argument. Run adk-review --staged to review the index by hand")
        return EXIT_USAGE
    repository = os.getcwd()  # git runs a hook at the top of the working tree, and so does pre-commit
    try:
        resolved = preflight_staged(repository)
        if resolved["status"] != "success":
            _say(f"{resolved['error_message']}. Commit with --no-verify to skip the review")
            return 1
        if not list_changed(resolved):
            return 0  # the index adds nothing to HEAD: a message-only --amend, say
    except GitError as exc:
        _say(f"git could not list what this commit holds ({exc}). Commit with --no-verify to skip the review")
        return 1
    said = io.StringIO()
    with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
        code = review([repository, "--staged", "--profile", PROFILE])
    if code == 0:
        return 0
    sys.stdout.write(said.getvalue())  # the review's own report, made safe as it was written
    _say(f"the review of what this commit holds ended in exit {code}. Commit with --no-verify to skip it")
    return 1


def _say(sentence: str) -> None:
    """One line of the hook's own, made safe as the report's prose is: git's words can name any path."""
    print(f"pre-commit: {safe_line(sentence)}")


def run() -> int:
    """The process boundary of the installed `adk-review-commit`: a review's process-wide settings, then `main`."""
    settle()
    return main()


if __name__ == "__main__":
    raise SystemExit(run())

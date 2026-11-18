"""What the front end keeps of Azure DevOps, in `azure.json` of the person's state folder: the pull request's address
a repository's branch was given, pasted once, and the thread each finding was posted as.

No secret is kept: the screen gives a pull request's own address, never the text a person pasted, which can hold a
credential. What is kept is a convenience. A missing or damaged file reads as empty, and the thread's own text, its
mark, is what stops a second post."""

from __future__ import annotations

import json
from pathlib import Path

from . import config
from .azure import PullRequest


def _file() -> Path:
    """Where it is kept: read from the config each time, so the state folder a test names is the one written."""
    return config.STATE_DIR / "azure.json"


def _kept() -> dict[str, dict[str, dict]]:
    """The file's two tables, each entry kept only where it is what was written: an address is text, and a thread a
    whole number, which a `bool` is not."""
    try:
        raw = json.loads(_file().read_text(encoding="utf-8"))
    except OSError, ValueError:  # missing, unreadable, no JSON, or no UTF-8
        raw = None
    held = raw if isinstance(raw, dict) else {}
    return {"pull_requests": _table(held.get("pull_requests"), str), "posted": _table(held.get("posted"), int)}


def _table(raw: object, kind: type) -> dict[str, dict]:
    """A table of tables whose values are `kind`'s, with what is none of it left out."""
    if not isinstance(raw, dict):
        return {}
    return {
        outer: {name: value for name, value in inner.items() if type(value) is kind}
        for outer, inner in raw.items()
        if isinstance(inner, dict)
    }


def _keep(kept: dict[str, dict[str, dict]]) -> None:
    config.write_atomic(_file(), json.dumps(kept, indent=2, sort_keys=True) + "\n")


def pull_request_url(repo: str, branch: str) -> str:
    """The pull request's address kept for `branch` of the repository at `repo`, or `""`."""
    return _kept()["pull_requests"].get(repo, {}).get(branch, "")


def keep_pull_request_url(repo: str, branch: str, url: str) -> None:
    """`url` kept for `branch` of `repo`; an empty one forgets it."""
    kept = _kept()
    held = kept["pull_requests"].setdefault(repo, {})
    if url:
        held[branch] = url
    else:
        held.pop(branch, None)
    if not held:
        del kept["pull_requests"][repo]
    _keep(kept)


def posted_thread(pr: PullRequest, fingerprint: str) -> int | None:
    """The thread the finding `fingerprint` was posted as in `pr`, where this screen posted or found it."""
    return _kept()["posted"].get(pr.web, {}).get(fingerprint)


def keep_posted(pr: PullRequest, fingerprint: str, thread: int) -> None:
    """The finding `fingerprint` is thread `thread` of `pr`."""
    kept = _kept()
    kept["posted"].setdefault(pr.web, {})[fingerprint] = thread
    _keep(kept)

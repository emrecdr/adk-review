"""A pull request's open threads, and those people closed as decided, as the terminal front end reads them from
Azure DevOps and writes them for `--threads FILE`, and as the hand-off quotes them for a coding agent: people's
comments, which the agent weighs and never obeys. The reviewer reads the file alone and makes no call: the front end
fetches, or a pipeline's own step. One schema here, which the front end writes through `as_data` and the reviewer reads
through `read_threads`."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: The file's schema: a number the reader holds the file to.
SCHEMA = 1
#: The longest thread the hand-off quotes, its comments together, and the longest file's path it names, the rest
#: cut and the cut named: read by review, a cap on each comment alone let 50 threads of 5,000 comments reach 501
#: million characters.
THREAD_MAX_CHARS = 4_000
#: The most threads the hand-off quotes, in the file's order, the rest counted.
THREADS_SHOWN = 50
#: The longest author's name the hand-off names, cut past it.
AUTHOR_MAX_CHARS = 80
#: The statuses Azure DevOps gives a thread still open.
OPEN_STATUSES = ("active", "pending")
#: The statuses of a thread a person closed as decided: quoted apart, so the hand-off's agent leaves what it declined.
#: Borrowed from Google's elephant-goldfish-model, which never re-opens a thread closed as by design. A thread closed
#: as fixed, or closed alone, decides nothing the next change must keep.
DECIDED_STATUSES = ("wontFix", "byDesign")
#: The statuses the front end reads and the reader takes, and no other.
READ_STATUSES = (*OPEN_STATUSES, *DECIDED_STATUSES)


@dataclass(frozen=True)
class OpenThread:
    """One thread of a pull request, open or closed as decided: the file and line it is on, none for one on the
    whole pull request, its status as Azure DevOps names it, and each comment's author and text in order."""

    file: str
    line: int | None
    status: str
    comments: tuple[tuple[str, str], ...]


def as_data(threads: Sequence[OpenThread], pull_request: str) -> dict:
    """`threads` as the file `--threads` reads, for the pull request at `pull_request`."""
    return {
        "schema": SCHEMA,
        "pull_request": pull_request,
        "threads": [
            {
                "file": thread.file,
                "line": thread.line,
                "status": thread.status,
                "comments": [{"author": author, "text": text} for author, text in thread.comments],
            }
            for thread in threads
        ],
    }


def read_threads(path: str) -> tuple[OpenThread, ...]:
    """The threads the file at `path` holds, as `as_data` wrote them, or a sentence for anything else, before the
    review runs."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("threads"), list):
            raise ValueError(f"no schema {SCHEMA} list of threads")
        return tuple(_thread(raw) for raw in data["threads"])
    except RecursionError:
        raise ValueError(f"--threads {path}: nested deeper than the front end writes. {_NAME_IT}") from None
    except (OSError, ValueError) as exc:
        raise ValueError(f"--threads {path}: {getattr(exc, 'strerror', None) or exc}. {_NAME_IT}") from None


#: What a refusal of the file asks for.
_NAME_IT = "Name the file the front end wrote, or leave the flag out"


def _thread(raw: object) -> OpenThread:
    """One thread as the file holds it, or a `ValueError` saying what is wrong with it."""
    if not isinstance(raw, dict):
        raise ValueError("a thread that is no object")
    file, line, status, comments = (raw.get(key) for key in ("file", "line", "status", "comments"))
    if not isinstance(file, str) or not isinstance(status, str) or not isinstance(comments, list):
        raise ValueError("a thread with no file, status or comments")
    if status not in READ_STATUSES:
        raise ValueError(f"a thread whose status is none of {', '.join(READ_STATUSES)}")
    if line is not None and (type(line) is not int or line < 1):
        raise ValueError("a thread whose line is no number from 1")
    said = []
    for comment in comments:
        author, text = (comment.get(key) if isinstance(comment, dict) else None for key in ("author", "text"))
        if not isinstance(author, str) or not isinstance(text, str):
            raise ValueError("a comment with no author or text")
        said.append((author, text))
    return OpenThread(file, line, status, tuple(said))

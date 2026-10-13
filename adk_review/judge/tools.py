"""Chat's model-facing tools, and the guardrail in front of them.

ADK builds a tool's schema from the function signature and its docstring, so
the docstring is prompt material: it tells the model when to call the tool and
what comes back. Every tool returns a `{"status": ...}` dict and never raises
at the model. A parameter named `tool_context` is filled in by ADK, never by
the model: it is how a tool reaches session state — where `inspect_repository`
leaves the scope it confirmed, which `changed_files` lists and the checks node
gathers the change from, as the command gathers it. The intake confirms what a
review covers and reads no diff: measured before, it fetched every changed
file's diff and wrote them all back as its reply, which nothing read. The model
chooses tool arguments, so a tool argument is untrusted input: `one_repository`
runs before every call. The git itself is in `collect/git.py`; this module only
speaks to the model.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ..collect.git import GitError, list_changed, preflight, same_folder
from .agents import REPOSITORY, SCOPE

#: The list a model reads of what the change touched, capped as every string a model reads is: a change of thousands
#: of files filled its context with paths. The cut is named, and the count stays whole.
LISTED_MAX_CHARS = 20_000


def inspect_repository(repo: str, base: str, head: str, tool_context: ToolContext) -> dict:
    """Confirm `repo` is a git repository, that `base` and `head` exist, and find their merge base.

    Call this before anything else: the tools after it read the repository
    and branches it confirmed from session state and never ask for them
    again. Both branches must exist; a missing one is an error to report,
    never something to guess.

    Args:
        repo: Absolute path to the repository's root directory.
        base: The branch the change will be merged into, usually "main".
        head: The branch under review.

    Returns:
        On success: {"status": "success", "repo", "base", "base_sha", "head", "head_sha",
        "merge_base", "commits_ahead", "base_ref", "head_ref"}. On failure: {"status": "error", "error_message": "..."}.
    """
    resolved = preflight(repo, base, head)
    # A failed preflight clears the scope: nothing later may read a repository this call did not confirm.
    tool_context.state[SCOPE] = resolved if resolved["status"] == "success" else None
    if resolved["status"] == "success":  # the conversation's one repository, kept when a later call fails
        tool_context.state[REPOSITORY] = resolved["repo"]
    return resolved


def changed_files(tool_context: ToolContext) -> dict:
    """List the files the branch under review changed, with a status letter each.

    Call this after inspect_repository: it lists the change inspect_repository
    confirmed, and its count is how many files the review reads.

    Returns:
        On success: {"status": "success", "files": [{"path": ..., "status": "A|M|D|R", "old": ... for R}],
        "count": int, and "cut" naming what was left out of a list too long to read whole}.
        On failure: {"status": "error", "error_message": "..."}.
    """
    scope = tool_context.state.get(SCOPE)
    if not scope:
        return {"status": "error", "error_message": "call inspect_repository first; changed_files lists its change"}
    try:
        files = list_changed(scope)
    except GitError as exc:
        return {"status": "error", "error_message": str(exc)}
    listed, room = [], LISTED_MAX_CHARS
    for f in files:
        room -= len(f["path"]) + len(f.get("old", ""))
        if room < 0:
            break
        listed.append(f)
    reply = {"status": "success", "files": listed, "count": len(files)}
    if len(listed) < len(files):
        reply["cut"] = (
            f"{len(files) - len(listed)} more file(s) not listed: past {LISTED_MAX_CHARS:,} characters of paths"
        )
    return reply


def one_repository(tool: BaseTool, args: dict[str, Any], tool_context: ToolContext) -> dict | None:  # noqa: ARG001 -- ADK passes each by name
    """The guardrail: refuse a repository that is not a directory, or not the one this conversation confirmed first.
    `inspect_repository` is the one tool that takes one, and no tool takes a path."""
    repo = args.get("repo")
    if repo is None:
        return None
    if not Path(os.path.expanduser(str(repo))).is_dir():
        return {"status": "error", "error_message": f"{repo} is not a directory; ask for the repository path"}
    # The first repository a conversation confirmed is its only one: a model a diff talked into naming another would
    # read that one's changes too. Before, any folder on the machine was let through.
    confirmed = tool_context.state.get(REPOSITORY)
    if confirmed and not same_folder(os.path.expanduser(str(repo)), confirmed):
        return {
            "status": "error",
            "error_message": f"this conversation reviews {confirmed}; start a new session to review another repository",
        }
    return None

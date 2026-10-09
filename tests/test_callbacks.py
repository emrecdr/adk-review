"""The guardrail: a tool argument the model chose is hostile input until it is checked. The chat reads no diff, so no
tool of it takes a path, and its guard holds a conversation to its repository alone."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

from conftest import UNKNOWN_USER_REPO, tool_context


def _guard():
    """The guard as the package stands now, imported at call time as each test imports what it reads."""
    return importlib.import_module("adk_review.judge.tools").one_repository


def _call(tool_name: str, args: dict, listed: list[str] | None = None) -> dict | None:
    context = tool_context(review_files=listed) if listed is not None else tool_context()
    return _guard()(SimpleNamespace(name=tool_name), args, context)


def test_a_tool_that_names_no_repository_is_let_through() -> None:
    """Measured by review: no test called a guard without a repository, and refusing one would refuse `changed_files`,
    which takes no argument, so chat could not list a change."""
    assert _call("changed_files", {}, listed=["src/a.py"]) is None


def test_a_repository_argument_must_be_a_directory() -> None:
    refused = _call("inspect_repository", {"repo": "/definitely/not/here", "base": "main", "head": "x"})
    assert refused and "not a directory" in refused["error_message"]


def test_a_repository_argument_naming_an_unknown_user_is_refused_not_raised() -> None:
    """An unknown `~name` stays as typed and is no directory; `Path.expanduser` raised here once."""
    refused = _call("inspect_repository", {"repo": UNKNOWN_USER_REPO, "base": "main", "head": "x"})
    assert refused and "not a directory" in refused["error_message"]


def test_a_second_repository_in_one_conversation_is_refused_and_the_first_however_spelled_let_through(
    tmp_path,
) -> None:
    """The first repository a conversation confirmed is its only one: a model a diff talked into naming another would
    read that one's changes too. Before, any folder on the machine was let through."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    context = tool_context(review_repo=str(first))
    guard, tool = _guard(), SimpleNamespace(name="inspect_repository")
    refused = guard(tool, {"repo": str(second), "base": "main", "head": "x"}, context)
    assert refused and "start a new session" in refused["error_message"], refused
    spelled = str(tmp_path / "." / "first") + "/"
    assert guard(tool, {"repo": spelled, "base": "main", "head": "x"}, context) is None

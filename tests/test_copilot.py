"""The files under `.github` carry what their readers need: each path instruction applies to files the repository
has, each custom agent and skill carries its front matter, the cloud agent's setup workflow is the one Copilot picks
up, and every workflow action is pinned by commit, one commit per action. The paths these files name are held to the
tree in `test_docs.py`, with the other guides."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import pytest
from conftest import ROOT, in_the_working_tree

GITHUB = ROOT / ".github"
INSTRUCTIONS = sorted((GITHUB / "instructions").glob("*.instructions.md"))
AGENTS = sorted((GITHUB / "agents").glob("*.agent.md"))
SKILLS = sorted((GITHUB / "skills").glob("*/SKILL.md"))
WORKFLOWS = sorted((GITHUB / "workflows").glob("*.yml"))


def _front_matter(path: Path) -> dict[str, str]:
    """The `key: value` pairs between the `---` fences at the top of `path`."""
    match = re.match(r"---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    assert match, f"{path.relative_to(ROOT)}: no front matter"
    pairs = [line.split(":", 1) for line in match.group(1).splitlines() if ":" in line]
    return {key.strip(): value.strip().strip("'\"") for key, value in pairs}


def test_each_helper_folder_holds_a_file_or_the_checks_below_would_pass_over_nothing() -> None:
    assert INSTRUCTIONS and AGENTS and SKILLS, "an instructions, agents or skills folder under .github is empty"


@pytest.mark.parametrize("path", INSTRUCTIONS, ids=lambda p: p.name)
def test_each_path_instruction_applies_to_files_the_repository_has(path: Path) -> None:
    present = [PurePosixPath(p) for p in in_the_working_tree()]
    for glob in [g.strip() for g in _front_matter(path)["applyTo"].split(",")]:
        assert any(p.full_match(glob) for p in present), f"{path.name}: applyTo {glob!r} matches no file"


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.name)
def test_each_custom_agent_is_named_after_its_file_and_has_a_description(path: Path) -> None:
    front = _front_matter(path)
    stem = path.name.removesuffix(".agent.md")
    assert front.get("name", stem) == stem, f"{path.name}: a custom agent's name is its file's"
    assert front.get("description"), f"{path.name}: a custom agent needs a description"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_each_skill_is_named_after_its_folder_and_has_a_description(path: Path) -> None:
    front = _front_matter(path)
    assert front.get("name") == path.parent.name, f"{path.parent.name}: the skill's name must be its folder's"
    assert re.fullmatch(r"[a-z0-9-]{1,64}", front["name"]), f"{front['name']}: lowercase, digits and hyphens only"
    assert 0 < len(front.get("description", "")) <= 1024, f"{front['name']}: a description of at most 1024 characters"


def test_the_setup_workflow_is_the_one_copilot_picks_up() -> None:
    setup = (GITHUB / "workflows" / "copilot-setup-steps.yml").read_text(encoding="utf-8")
    assert "\n  copilot-setup-steps:\n" in setup, "the job must be named copilot-setup-steps"
    minutes = re.search(r"timeout-minutes: (\d+)", setup)
    assert minutes and int(minutes.group(1)) <= 59, "Copilot reads a timeout of at most 59 minutes"


def test_every_workflow_action_is_pinned_by_commit_and_to_one_commit_across_workflows() -> None:
    pinned: dict[str, set[str]] = {}
    for workflow in WORKFLOWS:
        for use in re.findall(r"uses: (\S+)", workflow.read_text(encoding="utf-8")):
            action, _, ref = use.partition("@")
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{workflow.name}: {use} is not pinned by commit"
            pinned.setdefault(action, set()).add(ref)
    assert [action for action, refs in pinned.items() if len(refs) > 1] == [], "an action pinned to two commits"

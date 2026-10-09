"""The docs are pinned to what they describe: the paths the guides name, the modules the README names, and the
user's guide held to the code wherever the code decides.

Doc passes found the same rot by hand — a guide naming a file that had moved, a flag or a limit the guide no longer
matched. A test that reads the docs the way a reader does is cheaper than another pass.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import ROOT, as_json, in_the_working_tree

PACKAGE = ROOT / "adk_review"
#: The guides a reader or an assistant follows, held to the paths they name. `README.md` names other repositories'
#: paths too — a hostile branch's `X.py/y.py`, a skip glob's `.venv/` — so it is not among them.
GUIDES = [ROOT / "CLAUDE.md", ROOT / "GUIDE.md", *sorted((ROOT / ".github").rglob("*.md"))]
#: A backticked path with a folder in it: a file by the dot in its name, a folder by its trailing slash.
_PATH = re.compile(r"`((?:[\w.-]+/)+(?:[\w-]+\.[\w.]+)?)`")
#: The one path a guide names that no checkout holds: `adk web`'s session store, written when it runs.
NOT_IN_THE_REPOSITORY = {".adk/session.db"}


def _present(name: str) -> bool:
    """A file of the working tree or, ending in `/`, a folder one lies in — read from the root, or from inside the
    package, as the guides name `core/blast.py` beside it."""
    present = in_the_working_tree()
    return any(
        root + name in present or (name.endswith("/") and any(p.startswith(root + name) for p in present))
        for root in ("", f"{PACKAGE.name}/")
    )


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_a_guide_names_only_paths_in_the_working_tree(guide: Path) -> None:
    named = set(_PATH.findall(guide.read_text(encoding="utf-8"))) - NOT_IN_THE_REPOSITORY
    missing = sorted(name for name in named if not _present(name))
    assert missing == [], f"{guide.relative_to(ROOT)} names paths that are not in the working tree: {missing}"


def test_the_readme_names_every_module_at_the_packages_root() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    unnamed = [p.name for p in sorted(PACKAGE.glob("*.py")) if f"`{p.name}`" not in text]
    assert unnamed == [], f"README.md never names these: {unnamed}"


def test_the_users_guide_says_what_the_command_does() -> None:
    """The guide a user reads in place of the code: every flag the parser takes and no other, the checks exactly as
    `--list-checks` prints them, every field the JSON report holds and no other, and each limit the number its
    constant holds. Written by hand where a sentence helps, and held to the code where the code decides."""
    import importlib

    from adk_review import review
    from adk_review.core.verdict import Outcome
    from adk_review.deliver.report import render_checks
    from adk_review.judge.catalog import CONFIG, catalog, load_config, table

    guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")

    def rows(section: str) -> list[list[str]]:
        body = guide.split(f"\n## {section}\n", 1)[1].split("\n## ", 1)[0]
        return [
            [cell.strip() for cell in line.strip("|").split("|")]
            for line in body.splitlines()
            if line.startswith("| ") and not line.startswith("|---")
        ][1:]

    config = load_config(CONFIG)
    parser = review.build_parser(config["review"])
    flags = {next((o for o in action.option_strings if o.startswith("--")), action.dest) for action in parser._actions}
    assert {row[0].strip("`") for row in rows("Flags")} == flags
    listed = render_checks(table(catalog(), config["profiles"]))
    shown = guide.split("<!-- checks:", 1)[1].split("-->", 1)[1].split("<!-- /checks -->", 1)[0]
    assert shown.strip() == "\n".join(line for line in listed.splitlines() if line.startswith("|"))
    fields = as_json(Outcome())
    assert [row[0].strip("`") for row in rows("The JSON report")] == list(fields)
    for _what, limit, where in rows("Limits"):
        module, constant = (part.strip("` ") for part in where.split(","))
        value = getattr(importlib.import_module("adk_review." + module.removesuffix(".py").replace("/", ".")), constant)
        assert int(re.sub(r"[^0-9]", "", limit)) == value, (constant, limit, value)

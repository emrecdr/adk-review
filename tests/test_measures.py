"""What a review measures beside the verdict, and never feeds it: each changed file's blast radius, and under `--all`
the project's health."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from conftest import (
    BASE,
    FORGED,
    HEAD,
    _git_in,
    missing_objects,
    run_review,
)


class TestTheBlastRadius:
    """What depends on each changed file at the head, read from the syntax tree: never run, never guessed narrow."""

    PAYMENTS = {  # noqa: RUF012 -- a fixture tree, read only
        "src/payments/__init__.py": "",
        "src/payments/charge.py": "from dataclasses import dataclass\n",
        "src/payments/refund.py": "from .charge import Charge\n",
        "src/payments/report.py": "",
        "tests/test_charge.py": "from payments.charge import charge\n",
    }

    def test_relative_and_src_layout_imports_both_reach_the_changed_file(self) -> None:
        from adk_review.core.blast import blast_radius, describe, parsed

        changed = [{"path": "src/payments/charge.py", "status": "M"}, {"path": "src/payments/report.py", "status": "A"}]
        blast = blast_radius(parsed(self.PAYMENTS), changed)
        charge, report = blast.radii
        assert (charge.modules, charge.tests, charge.direct) == (
            1,
            1,
            ("src/payments/refund.py", "tests/test_charge.py"),
        )
        assert describe(charge, blast.floor) == (
            "1 module and 1 test depend on it at the head: src/payments/refund.py, tests/test_charge.py"
        )
        assert describe(report, blast.floor) == "a leaf: nothing at the head imports it"

    def test_what_depends_through_another_file_counts_and_the_widest_comes_first(self) -> None:
        from adk_review.core.blast import blast_radius, parsed

        sources = {"a.py": "import b\n", "b.py": "from c import thing\n", "c.py": ""}
        radii = blast_radius(parsed(sources), [{"path": "a.py", "status": "M"}, {"path": "c.py", "status": "M"}]).radii
        assert [(r.path, r.modules, r.direct) for r in radii] == [("c.py", 2, ("b.py",)), ("a.py", 0, ())]

    def test_the_names_are_what_imports_it_and_the_most_imported_comes_first(self) -> None:
        """Measured over 30 installed trees: in 23, most files reach over half the tree through other files, one
        of two counts shared by nearly all (419 of ADK's 507 such files), so the widest came first by its name;
        the median file has 1 to 4 importers of its own. A reach of 5 through one file reads as what it is, and the
        file three import comes first; a deleted file names the imports it breaks."""
        from adk_review.core.blast import blast_radius, describe, parsed

        sources = {"hub.py": "", "deep.py": "", "one.py": "import deep\n"}
        sources |= dict.fromkeys(("x.py", "y.py", "z.py"), "import hub\n")
        sources |= dict.fromkeys(("p.py", "q.py", "r.py", "s.py"), "import one\n")
        sources |= {"user.py": "import gone\n", "top.py": "import user\n"}
        changed = [{"path": "deep.py", "status": "M"}, {"path": "hub.py", "status": "M"}]
        radii = blast_radius(parsed(sources), [*changed, {"path": "gone.py", "status": "D"}]).radii
        assert [(r.path, describe(r, floor=False)) for r in radii] == [
            ("hub.py", "3 modules depend on it at the head: x.py, y.py, z.py"),
            ("deep.py", "5 modules depend on it at the head, 1 of them directly: one.py"),
            (
                "gone.py",
                "deleted by this change, yet 2 modules at the head still depend on it, 1 of them directly: user.py",
            ),
        ]

    def test_an_import_reaches_what_it_names_and_every_package_on_the_way(self) -> None:
        """Python runs `pkg/__init__.py` before `pkg.x`. Measured with "the submodule, else the package": the changed
        `__init__.py` read as reached by `other.py` alone, and with no one importing `pkg` by name, as a leaf."""
        from adk_review.core.blast import blast_radius, parsed

        sources = {
            "pkg/__init__.py": "thing = 1\n",
            "pkg/x.py": "",
            "pkg/y.py": "from . import x\n",
            "user.py": "from pkg import x\n",
            "other.py": "from pkg import thing\n",
        }
        changed = [{"path": "pkg/x.py", "status": "M"}, {"path": "pkg/__init__.py", "status": "M"}]
        by_path = {r.path: (r.modules, r.direct) for r in blast_radius(parsed(sources), changed).radii}
        assert by_path == {
            "pkg/x.py": (2, ("pkg/y.py", "user.py")),
            "pkg/__init__.py": (3, ("other.py", "pkg/y.py", "user.py")),
        }

    def test_a_cycle_of_imports_is_walked_once_and_counted_whole(self) -> None:
        """Measured by review: a walk from each changed file read a cycle of 4,000 modules again from each, 9.3 s, every
        module listed in every radius. Its members reach each other, so the cycle is walked once, each counted."""
        import time

        from adk_review.core.blast import blast_radius, parsed

        n = 3_000
        sources = {f"m{i}.py": f"import m{(i + 1) % n}\n" for i in range(n)}
        modules, changed = parsed(sources), [{"path": path, "status": "M"} for path in sources]
        started = time.perf_counter()
        radii = blast_radius(modules, changed).radii
        assert time.perf_counter() - started < 2.0, "once, where it was once a file"
        assert {(r.modules, r.tests, len(r.direct)) for r in radii} == {(n - 1, 0, 1)}

    def test_a_radius_past_the_steps_is_not_counted_and_says_so(self, monkeypatch) -> None:
        """A chain of imports is no cycle, and a walk from each of its files reads the rest: past the steps the radii
        take in all, a radius is not counted, and says so, its own importers named; never a leaf."""
        from adk_review.core import blast

        monkeypatch.setattr(blast, "RADIUS_STEPS", 10)
        sources = {f"m{i}.py": f"import m{i + 1}\n" for i in range(9)} | {"m9.py": ""}
        changed = [{"path": f"m{i}.py", "status": "M"} for i in (9, 1)]
        found = blast.blast_radius(blast.parsed(sources), changed)
        by_path = {r.path: r for r in found.radii}
        assert (by_path["m9.py"].modules, by_path["m9.py"].counted) == (9, True), "ten steps, one a module"
        assert not by_path["m1.py"].counted and by_path["m1.py"].direct == ("m0.py",)
        said = blast.describe(by_path["m1.py"], found.floor)
        assert (
            said == "not counted: the radii spent the 10 steps they take in all before it; imported directly by m0.py"
        )

    def test_a_deep_path_is_found_by_its_last_parts_and_indexed_by_no_more(self) -> None:
        """Measured by review: 100 files 1,000 folders deep took 20 s and 432 MB to index, every tail of every path a
        key. A tail no import is long enough to name is no key, and a short name still finds its file."""
        import time

        from adk_review.core.blast import blast_radius, parsed

        deep = "/".join(["a"] * 1_000)
        sources = {f"{deep}/f{i}.py": "x = 1\n" for i in range(100)} | {"app.py": "import f7\n"}
        started = time.perf_counter()
        (radius,) = blast_radius(parsed(sources), [{"path": f"{deep}/f7.py", "status": "M"}]).radii
        assert time.perf_counter() - started < 2.0, "a key no longer than an import"
        assert radius.direct == ("app.py",), radius

    def test_what_was_not_read_is_named_and_never_reads_as_a_leaf(self) -> None:
        from adk_review.core.blast import blast_radius, describe, parsed

        sources = {"user.py": "import old\n", "broken.py": "def (\n", "lonely.py": ""}
        changed = [
            {"path": "README.md", "status": "M"},
            {"path": "old.py", "status": "D"},
            {"path": "gone.py", "status": "D"},
            {"path": "lonely.py", "status": "A"},
        ]
        blast = blast_radius(parsed(sources), changed, skipped=2)
        by_path = {r.path: describe(r, blast.floor) for r in blast.radii}
        assert blast.unparsed == ("broken.py",) and blast.skipped == 2 and blast.floor
        assert by_path["README.md"] == "not measured: not a Python file"
        assert by_path["old.py"] == (
            "deleted by this change, yet 1 module at the head still depends on it"
            " (at least: the tree was not read whole): user.py"
        )
        assert by_path["lonely.py"] == "nothing that was read imports it; the tree was not read whole"
        assert by_path["gone.py"] == (  # measured before: a deleted file said "nothing imports it" over a partial read
            "deleted by this change; nothing that was read imports it; the tree was not read whole"
        )

    @pytest.mark.parametrize("clone", ["cut_off_clone", "partial_clone"])
    def test_a_file_a_partial_clone_lacks_is_counted_never_fetched(self, clone: str, request) -> None:
        """`ls-tree -l` and `cat-file --batch` read sizes and contents a partial clone lacks. Measured before the fix:
        with the origin there, git fetched them into the repository under review, 4 missing objects to 0; with it
        gone, the listing failed. Fetching nothing, git lists a lacking blob's size as "BAD": the file is unread and
        counted, the rest is read, and every count is a floor, never "a leaf"."""
        from adk_review.collect import change, git
        from adk_review.core.blast import describe
        from adk_review.deliver.report import _blast

        repo = request.getfixturevalue(clone)
        missing = missing_objects(repo)
        resolved = git.preflight(str(repo), BASE, HEAD)
        blast = change.radius_of(git.tree_at_head(resolved, git.head_listing(resolved)), git.list_changed(resolved))
        assert blast.listed and blast.skipped and missing_objects(repo) == missing
        said = [describe(r, blast.floor) for r in blast.radii if r.measured]
        assert said and [s for s in said if "a leaf" in s] == []
        assert any("or not in the clone; every count above is a floor" in line for line in _blast(blast))

    def test_the_audited_code_is_parsed_never_run(self, tmp_path: Path) -> None:
        from adk_review.core.blast import blast_radius, parsed

        tripwire = tmp_path / "ran"
        sources = {"payload.py": f"import pathlib\npathlib.Path({str(tripwire)!r}).write_text('ran')\n"}
        blast_radius(parsed(sources), [{"path": "payload.py", "status": "A"}])
        assert not tripwire.exists(), "a module was executed to learn its imports"

    def test_a_short_name_reaches_a_nested_module_only_from_an_import_root(self) -> None:
        """Measured on a 164-file tree against an exact resolver: `from types import` had landed on a nested
        `rules/types.py`. A folder holding `__init__.py` is a package, never a root Python imports a short name
        from; a test folder without one is pytest's own root, so `from conftest import` is a real import. Then
        measured on LiteLLM's 2,380 files: a folder with no `__init__.py` inside a package still counted as a root,
        and 992 imports landed in one, `import types` on three `types.py` (one read as imported by 276 files where
        4 import it), `import openai` on the package's own `openai.py`. Inside a package a folder is a root only for
        its own files, as pytest makes `pkg/tests/` one for `test_x.py`; a file there is still reached by its whole
        name."""
        from adk_review.core.blast import blast_radius, parsed

        sources = {
            "pkg/__init__.py": "",
            "pkg/types.py": "",
            "pkg/user.py": "from types import SimpleNamespace\nimport openai\n",
            "pkg/files/types.py": "",
            "pkg/llms/openai.py": "",
            "pkg/client.py": "from pkg.files.types import Kind\n",
            "pkg/tests/helpers.py": "",
            "pkg/tests/test_x.py": "import helpers\n",
            "tests/conftest.py": "",
            "tests/test_user.py": "from conftest import helper\n",
        }
        paths = [
            "pkg/types.py",
            "pkg/files/types.py",
            "pkg/llms/openai.py",
            "pkg/tests/helpers.py",
            "tests/conftest.py",
        ]
        changed = [{"path": path, "status": "M"} for path in paths]
        by_path = {r.path: (r.modules, r.tests, r.direct) for r in blast_radius(parsed(sources), changed).radii}
        assert by_path == {
            "pkg/types.py": (0, 0, ()),
            "pkg/files/types.py": (1, 0, ("pkg/client.py",)),
            "pkg/llms/openai.py": (0, 0, ()),
            "pkg/tests/helpers.py": (0, 1, ("pkg/tests/test_x.py",)),
            "tests/conftest.py": (0, 1, ("tests/test_user.py",)),
        }

    def test_a_standard_library_name_lands_only_on_a_file_at_the_top(self) -> None:
        """Measured on a namespace package, a folder with no `__init__.py` at all: its `typing.py` was read as
        imported by 81 files, through a root inferred above it and each importer's own folder, where 7 import it.
        `import typing` is the standard library's; only a file at the top is read in its place."""
        from adk_review.core.blast import blast_radius, parsed

        sources = {
            "pkg/__init__.py": "",
            "pkg/a.py": "import types\nimport json\n",
            "src/domain/types.py": "",
            "ns/typing.py": "",
            "ns/b.py": "from typing import Any\n",
            "ns/c.py": "from ns.typing import Alias\n",
            "json.py": "",
        }
        changed = [{"path": path, "status": "M"} for path in ("src/domain/types.py", "ns/typing.py", "json.py")]
        direct = {r.path: r.direct for r in blast_radius(parsed(sources), changed).radii}
        assert direct == {"src/domain/types.py": (), "ns/typing.py": ("ns/c.py",), "json.py": ("pkg/a.py",)}

    def test_the_head_is_read_in_one_batch_and_the_diff_the_guidelines_quote_stays_the_change(
        self, demo_repo: Path
    ) -> None:
        from adk_review.collect.git import collect_evidence
        from adk_review.judge.agents import for_the_guidelines

        evidence = collect_evidence(str(demo_repo), BASE, HEAD)
        assert not evidence["blast"].unparsed and not evidence["blast"].skipped
        assert "Blast radius" not in evidence["diff"], "a finding quoting the radius would pass as grounded"
        section = for_the_guidelines(evidence["blast"])
        assert section.startswith("## Blast radius\n<<< blast radius "), "fenced, as the change is"
        assert "\n- src/payments/charge.py: 1 module and 1 test depend on it" in section
        assert "\n- src/payments/config.py: a leaf: nothing at the head imports it\n" in section

    def test_the_guidelines_read_the_radius_beside_the_diff_and_a_quote_of_it_is_not_evidence(
        self, demo_repo: Path, cli, fake_provider, capsys
    ) -> None:
        """Measured before the move: the radius sat inside the diff block, and a finding whose only evidence was
        the radius line passed the grounding check. The rule below answers only a guideline whose instruction carries
        the section, so the dropped finding proves both halves."""
        radius = "- src/payments/charge.py: 1 module and 1 test depend on it at the head"
        finding = {
            "file": "src/payments/charge.py",
            "line": None,
            "severity": "major",
            "title": "a widely imported module changed",
            "evidence": radius[2:],
            "suggestion": "read what depends on it",
        }
        fake_provider.answers_when(radius, {"findings": [finding], "summary": "one"})
        run_review(cli, demo_repo)
        dropped = capsys.readouterr().out.split("findings dropped for evidence not in the diff")[1]
        assert "a widely imported module changed" in dropped

    def test_a_path_holding_newlines_forges_no_heading_in_the_guideline_prompt(self, forged_path_repo: Path) -> None:
        """The path is the reviewed branch's text, and it reaches the prompt twice: the diff block's heading and the
        radius section. Measured before flattening: each carried an `## Instructions` heading of the branch's own."""
        from adk_review.collect.git import collect_evidence
        from adk_review.judge.agents import for_the_guidelines

        evidence = collect_evidence(str(forged_path_repo), BASE, FORGED)
        prompt = for_the_guidelines(evidence["blast"]) + evidence["diff"]
        assert [line for line in prompt.splitlines() if line.startswith("## ")] == ["## Blast radius"]

    def test_a_crlf_file_with_non_ascii_text_is_read_exactly(self, tmp_path: Path) -> None:
        """git counts a blob's size in bytes. Measured with the batch read as text: the line endings were rewritten
        under the count, and the first file swallowed the next one's header, silently."""

        import ast

        from adk_review.collect import git

        body = "# café €\r\nfrom b import thing\r\n"
        (tmp_path / "a.py").write_bytes(body.encode())
        (tmp_path / "b.py").write_bytes(b"thing = 1\r\n")
        for args in (["init", "-q", "-b", "main"], ["add", "a.py", "b.py"], ["commit", "-qm", "two files"]):
            _git_in(tmp_path, "-c", "core.autocrlf=false", *args)
        resolved = git.preflight(str(tmp_path), "main", "main")
        shas = {path: sha for _mode, _kind, sha, _size, path in git._listed(resolved)}
        assert git._blobs(resolved, [shas["a.py"], shas["b.py"]]) == [body, "thing = 1\r\n"], "bytes decoded as written"
        tree = git.tree_at_head(resolved, git.head_listing(resolved))
        assert isinstance(tree.module("a.py"), ast.Module) and not tree.unread, "read whole, and parsed"

    def test_past_the_trees_byte_budget_nothing_more_is_read_and_it_is_counted(
        self, demo_repo: Path, monkeypatch
    ) -> None:
        """Measured: 142 ms a megabyte to parse and resolve LiteLLM's 2,380 files; a file cap alone let 5 GB in."""
        from adk_review.collect import git

        monkeypatch.setattr(git, "MAX_TREE_BYTES", 200)
        resolved = git.preflight(str(demo_repo), BASE, HEAD)
        tree = git.tree_at_head(resolved, git.head_listing(resolved))
        texts, unread = git._texts(resolved, git._plain(git._listed(resolved)))
        assert tree.unread and unread, "what did not fit is named"
        assert sum(len(text.encode()) for text in texts.values()) <= 200, "what was read stays under the budget"

    def test_a_file_over_the_size_cap_is_not_read_and_the_counts_become_a_floor(
        self, demo_repo: Path, monkeypatch
    ) -> None:
        from adk_review.collect import git

        monkeypatch.setattr(git, "MAX_FILE_BYTES", 30)  # refund.py, the one module importing charge.py, is longer
        blast = git.collect_evidence(str(demo_repo), BASE, HEAD)["blast"]
        charge = next(r for r in blast.radii if r.path == "src/payments/charge.py")
        assert blast.skipped and "src/payments/refund.py" not in charge.direct and charge.modules == 0
        assert blast.floor, "a count over a tree not read whole is a floor"

    def test_a_tree_read_past_its_timeout_is_not_read_and_every_count_is_a_floor(
        self, demo_repo: Path, monkeypatch
    ) -> None:
        """Measured before: `TimeoutExpired` from the listing or the batch rose out of the command as a traceback.
        And with `ls-tree`'s exit code ignored, a failed listing read as an empty tree and every changed file as "a
        leaf", so "did not look" read as "found nothing"."""
        from adk_review.collect import change, git
        from adk_review.deliver.report import _blast

        resolved = git.preflight(str(demo_repo), BASE, HEAD)
        files, run = git.list_changed(resolved), git._run

        def only(verb: str):
            def times_out(argv, *args, **kwargs):
                if argv[3:4] == [verb]:
                    raise git.subprocess.TimeoutExpired(argv, 1e-6)
                return run(argv, *args, **kwargs)

            return times_out

        monkeypatch.setattr(git, "_run", only("ls-tree"))
        unlisted = change.radius_of(git.tree_at_head(resolved, git.head_listing(resolved)), files)
        assert git.tree_at_head(resolved, git.head_listing(resolved)) is None and not unlisted.listed and unlisted.floor
        assert "- not read: the head's tree could not be listed; every count above is a floor" in _blast(unlisted)
        monkeypatch.setattr(git, "_run", only("cat-file"))
        tree = git.tree_at_head(resolved, git.head_listing(resolved))
        assert list(tree.modules()) == [] and tree.unread and change.radius_of(tree, files).floor

    def test_the_report_opens_with_the_blast_radius_widest_first(self, demo_repo: Path, cli, capsys) -> None:
        assert run_review(cli, demo_repo) == 1, "the radius informs the reader; severity still decides"
        out = capsys.readouterr().out
        section = out.split("## Blast radius")[1].split("## Verdict")[0]
        bullets = [line for line in section.splitlines() if line.startswith("- ")]
        assert bullets[0].startswith("- `src/payments/charge.py`: 1 module and 1 test depend on it")
        assert sum("a leaf" in line for line in bullets) == 3


class TestTheHealthOfAWholeProject:
    """Under `--all` the files are the project, and the report measures it by code alone: what was read, the source
    and the tests apart, the source's function lines by McCabe complexity, and what each rule found. Numbers, never a
    grade, and never read by the verdict."""

    SOURCE = (
        "def plain():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def branches(a, b, items):\n"
        "    if a and b or not a:\n"
        "        x = 1\n"
        "    elif b:\n"
        "        x = 2\n"
        "    for item in items:\n"
        "        while item:\n"
        "            item -= 1\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError:\n"
        "        pass\n"
        "    except KeyError:\n"
        "        pass\n"
        "    y = [i for i in items if i if i > 1]\n"
        "    z = 1 if a else 2\n"
        "    match a:\n"
        "        case 1:\n"
        "            pass\n"
        "        case _:\n"
        "            pass\n"
        "    return x, y, z\n"
        "\n"
        "\n"
        "def outer(a):\n"
        "    if a:\n"
        "        pass\n"
        "    def inner(b):\n"
        "        if b:\n"
        "            return 1\n"
        "        return 2\n"
        "    return inner\n"
    )

    def test_complexity_counts_each_path_as_ruff_does_and_a_nested_function_on_its_own(self, tmp_path: Path) -> None:
        """McCabe's count as ruff's C901 makes it, so the report and lint name one number: one, and one for each `if`,
        `elif`, loop, `except`, a `try`'s `else` and each `case` but a catch-all; an `and`, an `or`, a conditional
        expression and a comprehension add none. Measured before, counting those gave a different number for up to
        45% of a project's functions. A nested function's paths and lines are its own, so no line is counted twice,
        where ruff adds it to the function around it too: the one difference, held here to ruff itself."""
        import ast

        from adk_review.core.health import band, units

        found = {unit.name: unit for unit in units("m.py", ast.parse(self.SOURCE))}
        assert found["plain"].complexity == 1
        assert found["branches"].complexity == 8, "if, elif, two loops, two excepts and one case; the catch-all none"
        assert (found["outer"].complexity, found["inner"].complexity) == (2, 2)
        assert (found["outer"].lines, found["inner"].lines) == (4, 4), "the inner function's lines are its own"
        assert (band(10), band(11), band(20), band(21), band(51)) == ("1-10", "11-20", "11-20", "21-50", "over 50")
        from adk_review.collect.ruff import beside

        source = tmp_path / "m.py"
        source.write_text(self.SOURCE, encoding="utf-8")
        argv = [beside() or "ruff", "check", "--isolated", "--select", "C901", "--exit-zero", "--output-format", "json"]
        argv += ["--config", "lint.mccabe.max-complexity = 0", str(source)]
        ruff = {
            re.search(r"`(\w+)`", hit["message"]).group(1): int(re.search(r"\((\d+) >", hit["message"]).group(1))
            for hit in json.loads(subprocess.run(argv, capture_output=True, encoding="utf-8", check=True).stdout)
        }
        assert {name: unit.complexity for name, unit in found.items() if name != "outer"} == {
            name: count for name, count in ruff.items() if name != "outer"
        }
        assert ruff["outer"] == found["outer"].complexity + found["inner"].complexity, "ruff adds the nested one"

    def test_the_measures_keep_source_tests_and_what_was_not_read_apart(self) -> None:
        from adk_review.core.health import measured

        files = [
            {"path": "src/a.py", "text": self.SOURCE},
            {"path": "src/a.pyi", "text": "def plain() -> int: ...\n"},  # a stub runs nothing: left out
            {"path": "src/broken.py", "text": "def (:\n"},
            {"path": "tests/test_a.py", "text": "def test_x():\n    assert True\n"},
            {"path": "logo.png", "text": None},
            {"path": "README.md", "text": "# a\n"},
        ]
        health = measured(files, None)
        assert (health.files, health.read) == (6, 5), "a binary file is not read whole"
        assert health.source == (2, self.SOURCE.count("\n") + 1) and health.tests == (1, 2)
        assert health.not_parsed == ("src/broken.py",) and health.worst[0].name == "branches"
        assert dict(health.bands)["11-20"] == 0 and dict(health.bands)["1-10"] == sum(u.lines for u in health.worst)
        from adk_review.core.verdict import Outcome
        from adk_review.deliver.report import _health

        nothing = measured([{"path": "README.md", "text": "# a\n"}], None)  # measured before: ZeroDivisionError
        assert "no function measured" in "\n".join(_health(nothing, Outcome(), None))
        unread = measured([*files, {"path": "src/big.py", "text": None}], None)
        assert unread.python == (3, 1), "a Python file not read is at the head, never parsed"
        from adk_review.core.health import Health

        many = Health(12, 12, (12, 12), (0, 0), (12, 0), health.bands, tuple(f"f{n}.py" for n in range(12)), ())
        said = "\n".join(_health(many, Outcome(), None, ("src",)))
        assert "`f9.py` and 2 more" in said and "every file at the head under `src`" in said

    def test_maintainability_is_graded_by_sigs_rating_only_where_most_of_the_source_parsed(self) -> None:
        """SIG's published rating of a complexity risk profile: the most each grade allows of the source's function
        lines in the moderate, high and very high bands. Measured on twelve real projects it told them apart, A to D;
        a grade needs ten parsed source files, and nine in ten of those at the head."""
        from adk_review.core.health import Health, grade

        def profile(low, moderate, high, very_high, python=(10, 10)) -> Health:
            bands = (("1-10", low), ("11-20", moderate), ("21-50", high), ("over 50", very_high))
            return Health(0, 0, (0, 0), (0, 0), python, bands, (), ())

        assert grade(profile(75, 25, 0, 0))[0] == "A" and grade(profile(65, 30, 5, 0))[0] == "B"
        assert grade(profile(64, 31, 5, 0))[0] == "C" and grade(profile(50, 40, 10, 0))[0] == "C"
        assert grade(profile(30, 50, 15, 5))[0] == "D" and grade(profile(50, 30, 14, 6))[0] == "E"
        assert grade(profile(75, 25, 0, 0))[1] == "25.0% moderate, 0.0% high, 0.0% very high"
        assert grade(profile(75, 25, 0, 0, python=(9, 9))) == (None, "9 Python source file(s) parsed; a grade needs 10")
        assert grade(profile(75, 25, 0, 0, python=(100, 89))) == (
            None,
            "89 of 100 Python source files parsed; a grade needs 90%",
        )

    def test_a_whole_project_review_reports_its_health_and_a_change_does_not(
        self, cli, demo_repo: Path, tmp_path: Path, capsys
    ) -> None:
        """Measured on this repository's own whole scan, raw counts misled — long lines, the tests' planted keys, a
        project's own rules — so each rule's findings are counted in source and in tests apart, by severity, and the
        section names no grade. With `--previous`, beside each share and count, what the earlier review said."""
        first, second = tmp_path / "first.json", tmp_path / "second.json"
        whole = [str(demo_repo), "--all", "--head", "feature/payments", "--profile", "rules-only"]
        assert cli.main([*whole, "--json", str(first)]) in (1, 3)
        out = capsys.readouterr().out
        assert "## Health" in out and "- complexity   the source's function lines by McCabe complexity: 1-10" in out
        assert out.index("## Health") < out.index("## Checks") and "| `rule.secrets` |" in out
        health = json.loads(first.read_text(encoding="utf-8"))["health"]
        assert set(health) == {"files", "source", "tests", "complexity", "found"}
        assert set(health["complexity"]["bands"]) == {"1-10", "11-20", "21-50", "over 50"}
        assert health["complexity"]["grade"] is None and "a grade needs 10" in health["complexity"]["why"]
        assert "- maintainability  not graded: 5 Python source file(s) parsed; a grade needs 10" in out
        assert health["found"]["rule.secrets"]["source"]["blocker"] >= 1
        assert cli.main([*whole, "--previous", str(first), "--json", str(second)]) in (1, 3)
        assert "(was " in capsys.readouterr().out
        changed = tmp_path / "change.json"
        assert run_review(cli, demo_repo, "--profile", "rules-only", "--json", str(changed)) == 1
        assert "## Health" not in capsys.readouterr().out
        assert json.loads(changed.read_text(encoding="utf-8"))["health"] is None

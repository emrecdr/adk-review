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
    _committed,
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
        assert said == "not counted: the radii spent the 10 steps they take in all before it. Direct importers: m0.py"

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
        assert by_path["lonely.py"] == "nothing read imports it. The review did not read the tree whole"
        assert by_path["gone.py"] == (  # measured before: a deleted file said "nothing imports it" over a partial read
            "deleted by this change. Nothing read imports it. The review did not read the tree whole"
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
        assert any("or not in the clone. Every count above is a floor" in line for line in _blast(blast))

    def test_the_audited_code_is_parsed_never_run(self, tmp_path: Path) -> None:
        from adk_review.core.blast import blast_radius, parsed

        tripwire = tmp_path / "ran"
        sources = {"payload.py": f"import pathlib\npathlib.Path({str(tripwire)!r}).write_text('ran')\n"}
        blast_radius(parsed(sources), [{"path": "payload.py", "status": "A"}])
        assert not tripwire.exists(), "a module was executed to learn its imports"

    def test_a_script_or_test_reaches_a_dotted_module_under_the_nearest_folder_above_it(self) -> None:
        """Found by cross-checking against codegraph over six repositories: a script outside any package, or a test,
        importing `src.hardware.csi` from a folder above it that a script or a test setup puts on the path, was read as
        importing nothing (ruview: 326 such edges, adk: 284), since a folder holding `__init__.py` is never a root."""
        from adk_review.core.blast import index

        tree = index(
            [
                "archive/v1/__init__.py",
                "archive/v1/src/__init__.py",
                "archive/v1/src/hardware/__init__.py",
                "archive/v1/src/hardware/csi.py",
                "archive/v1/data/proof/verify.py",
                "apps/__init__.py",
                "apps/app_07/release/__init__.py",
                "apps/app_07/release/agent.py",
                "apps/app_07/demo.py",
            ]
        )
        assert "archive/v1/src/hardware/csi.py" in tree.absolute(
            "archive/v1/data/proof/verify.py", "src.hardware.csi", ["X"]
        )
        assert (
            tree.one("archive/v1/data/proof/verify.py", 0, ("src", "hardware", "csi"))
            == "archive/v1/src/hardware/csi.py"
        )
        assert tree.one("apps/app_07/demo.py", 0, ("app_07", "release", "agent")) == "apps/app_07/release/agent.py"

    def test_a_test_reaches_a_dotted_module_one_file_alone_holds_anywhere(self) -> None:
        """Found by the same cross-check: a test at the top importing `app_01.support.lookup`, which a project's own
        configuration puts on the path from `apps/` (adk: 437 such edges); the reviewer reads no configuration, so a
        dotted name of two parts or more that one file alone answers is that file. Two files answering it, none."""
        from adk_review.core.blast import index

        tree = index(
            [
                "apps/__init__.py",
                "apps/app_01/support/__init__.py",
                "apps/app_01/support/lookup.py",
                "tests/test_app_01.py",
            ]
        )
        assert tree.one("tests/test_app_01.py", 0, ("app_01", "support", "lookup")) == "apps/app_01/support/lookup.py"
        assert "apps/app_01/support/lookup.py" in tree.absolute("tests/test_app_01.py", "app_01.support.lookup", ["x"])
        twice = index([*tree.exact.values(), "old/__init__.py", "old/app_01/support/lookup.py"])
        assert twice.one("tests/test_app_01.py", 0, ("app_01", "support", "lookup")) is None

    def test_library_code_never_reaches_a_folder_above_it_nor_a_lone_file_by_an_absolute_name(self) -> None:
        """The guard both rules keep, measured on LiteLLM before: inside a package an absolute import starts at a root,
        so `from openai.types import X` in `pkg/llms/xyz/handler.py` is the installed openai's, never
        `pkg/llms/openai/types.py` above it. A single name, `import office`, is never looked for above a folder."""
        from adk_review.core.blast import index

        tree = index(
            [
                "pkg/__init__.py",
                "pkg/llms/__init__.py",
                "pkg/llms/openai/__init__.py",
                "pkg/llms/openai/types.py",
                "pkg/llms/xyz/__init__.py",
                "pkg/llms/xyz/handler.py",
                "lib/__init__.py",
                "lib/office.py",
                "lib/tools/run/script.py",
            ]
        )
        assert tree.absolute("pkg/llms/xyz/handler.py", "openai.types", ["X"]) == set()
        assert tree.one("pkg/llms/xyz/handler.py", 0, ("openai", "types")) is None
        assert tree.one("lib/tools/run/script.py", 0, ("office",)) is None

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
        fake_provider.answers_when(radius, {"findings": [finding]})
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
        assert "- not read: git could not list the head's tree. Every count above is a floor" in _blast(unlisted)
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

    #: A module the branch `change` rewrites, and one it leaves alone that calls a function of it and reads another.
    USERS = (
        'USERS = {1: "ada"}\n\n\ndef find_user(uid):\n    if uid < 0:\n        raise ValueError(uid)\n'
        "    return USERS[uid]\n\n\ndef count():\n    return len(USERS)\n"
    )
    GREET = (
        "from shop import users\nfrom shop.users import find_user\n\n\ndef greet(uid):\n    try:\n"
        "        name = find_user(uid)\n    except KeyError:\n        return 'guest'\n"
        "    return name.title() + str(users.count())\n"
    )

    def shop(self, tmp_path: Path, users: str) -> Path:
        """A repository whose branch `change` rewrites `shop/users.py` as `users`, `shop/greet.py` left alone."""
        repo = tmp_path / "shop"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "shop/__init__.py", "")
        _committed(repo, "shop/users.py", self.USERS)
        _committed(repo, "shop/greet.py", self.GREET)
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "shop/users.py", users)
        return repo

    def test_the_guidelines_are_shown_each_call_into_a_changed_function_from_a_file_the_change_left_alone(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Asked by the owner: the review acts on the blast radius, for side effects. Measured live on both arms over
        five branches, each a behaviour change a caller left alone relied on: the correctness guideline raised each
        either way, as a guess that callers may break without the calls, naming the caller with them."""
        from adk_review.collect.git import collect_evidence
        from adk_review.judge import agents

        repo = self.shop(tmp_path, self.USERS.replace("return USERS[uid]", "return USERS.get(uid)"))
        evidence = collect_evidence(str(repo), BASE, "change")
        assert evidence["callers"] == (
            "- `shop/greet.py` calls `find_user()` from `shop/users.py`:\n"
            "   4 |\n   5 | def greet(uid):\n   6 |     try:\n   7 |         name = find_user(uid)\n"
            "   8 |     except KeyError:\n   9 |         return 'guest'\n"
            "  10 |     return name.title() + str(users.count())"
        ), "`count`, which the change left alone, is not shown"
        section = agents.for_the_guidelines(evidence["blast"], evidence["callers"])
        assert section.startswith("## Blast radius\n") and "\n## Callers the change left alone\n" in section
        assert "\n<<< callers " in section and "         name = find_user(uid)" in section, "fenced, as the change is"
        monkeypatch.setattr(agents, "CALLERS_CAP_CHARS", 60)
        assert "[… cut at 60 characters]" in agents.for_the_guidelines(evidence["blast"], evidence["callers"])

    def test_a_change_that_only_removes_lines_of_a_function_shows_its_callers(self, tmp_path: Path) -> None:
        """A guard the change deletes adds no line, yet each caller that relied on it may break."""
        from adk_review.collect.git import collect_evidence

        repo = self.shop(tmp_path, self.USERS.replace("    if uid < 0:\n        raise ValueError(uid)\n", ""))
        assert "`shop/greet.py` calls `find_user()`" in collect_evidence(str(repo), BASE, "change")["callers"]

    def test_a_call_in_the_changed_file_the_change_did_not_add_is_shown_too(self, tmp_path: Path) -> None:
        """Measured over ADK: a branch changed a private helper's parameters, each call to it in its own module, outside
        the lines the diff shows, and the section was empty. A call the change added is in the diff already."""
        from adk_review.collect.git import collect_evidence

        users = self.USERS + "\n\ndef name_of(uid):\n    return find_user(uid)\n"
        changed = (
            users.replace("return USERS[uid]", "return USERS.get(uid)")
            + "\n\ndef new(uid):\n    return find_user(uid)\n"
        )
        repo = tmp_path / "shop"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "shop/__init__.py", "")
        _committed(repo, "shop/users.py", users)
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "shop/users.py", changed)
        callers = collect_evidence(str(repo), BASE, "change")["callers"]
        assert callers.startswith("- `shop/users.py` calls `find_user()` from `shop/users.py`:\n"), callers
        assert "  15 |     return find_user(uid)" in callers and "  19 |" not in callers, (
            "the added call is in the diff"
        )

    def test_a_call_the_change_edited_below_its_first_line_is_in_the_diff_and_not_shown(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Read by review of the plan's fourth wave, and reproduced: a call was read by its first line alone, so one
        over several lines whose argument the change edited was shown as a caller the change left alone, and took a
        place among the calls shown from a caller no diff shows, which was counted as one more, not shown."""
        from adk_review.collect import change
        from adk_review.collect.git import collect_evidence

        first = "from shop.users import find_user\n\n\ndef first():\n    return find_user(\n        1,\n    )\n"
        repo = tmp_path / "shop"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "shop/__init__.py", "")
        _committed(repo, "shop/users.py", self.USERS)
        _committed(repo, "shop/first.py", first)
        _committed(repo, "shop/greet.py", self.GREET)
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "shop/users.py", self.USERS.replace("return USERS[uid]", "return USERS.get(uid)"))
        _committed(repo, "shop/first.py", first.replace("        1,\n", "        2,\n"))
        monkeypatch.setattr(change, "CALLS_SHOWN", 1)
        callers = collect_evidence(str(repo), BASE, "change")["callers"]
        assert callers.startswith("- `shop/greet.py` calls `find_user()` from `shop/users.py`:\n"), callers
        assert "shop/first.py" not in callers and "more call" not in callers, "the edited call is in the diff"

    def passed_on(self, tmp_path: Path, package: str) -> Path:
        """A repository whose `shop/__init__.py` is `package` and whose `shop/greet.py` imports `find_user` from the
        package, the branch `change` rewriting `shop/users.py`, where the function is."""
        repo = tmp_path / "shop"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "shop/__init__.py", package)
        _committed(repo, "shop/users.py", self.USERS)
        _committed(repo, "shop/greet.py", self.GREET.replace("from shop.users import", "from shop import"))
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "shop/users.py", self.USERS.replace("return USERS[uid]", "return USERS.get(uid)"))
        return repo

    def test_a_call_through_a_package_that_passes_the_function_on_is_shown_too(self, tmp_path: Path) -> None:
        """Read by review: a package's `__init__.py` that passes a function on is the common layout, and a call through
        `from shop import find_user` was not shown, where `callers` follows the same name `PASSED_ON` files deep."""
        from adk_review.collect.git import collect_evidence

        repo = self.passed_on(tmp_path, "from shop.users import find_user\n")
        callers = collect_evidence(str(repo), BASE, "change")["callers"]
        assert "- `shop/greet.py` calls `find_user()` from `shop/users.py`:\n" in callers, callers

    def test_a_package_the_heads_read_left_out_passes_nothing_on(self, tmp_path: Path, monkeypatch) -> None:
        """A floor, as the rest of the tree's reading is: the call through it is not shown, and nothing raises."""
        from adk_review.collect import git
        from adk_review.collect.git import collect_evidence

        repo = self.passed_on(tmp_path, "from shop.users import find_user\n" + "# padding\n" * 30)
        monkeypatch.setattr(git, "MAX_FILE_BYTES", 300)
        assert "shop/greet.py" not in collect_evidence(str(repo), BASE, "change")["callers"]

    def test_no_call_is_gathered_where_no_guideline_reads_it(self, tmp_path: Path) -> None:
        """The pre-push hook's profile runs no guideline: what it reads, and its time, stay as they were."""
        from adk_review.collect.git import collect_evidence

        repo = self.shop(tmp_path, self.USERS.replace("return USERS[uid]", "return USERS.get(uid)"))
        assert collect_evidence(str(repo), BASE, "change", guidelines=False).get("callers", "") == ""

    def test_a_guideline_is_sent_each_call_into_what_the_change_touched(self, tmp_path: Path, cli, asked) -> None:
        repo = self.shop(tmp_path, self.USERS.replace("return USERS[uid]", "return USERS.get(uid)"))
        cli.main([str(repo), "--head", "change", "--base", BASE, "--check", "guideline.correctness", "--no-verify"])
        instructions = [instruction for instruction, _turns in asked]
        assert instructions and all("## Callers the change left alone" in text for text in instructions), instructions
        assert all("   7 |         name = find_user(uid)" in text for text in instructions)


class TestWhatTheChangeCalls:
    """Each project function, class or parent method a line the change added calls or overrides, shown as the head
    defines it: the other way from the callers. Read by an outside audit and reproduced by a probe: a branch indexed
    `find_user(uid)["name"]`, where `find_user` returns None for an id it does not hold, and no model was shown
    `find_user`, whose file the change left alone."""

    USERS = (
        'USERS = {1: {"name": "ada"}}\n\n\ndef find_user(uid):\n    """The user, or None when no user has the id."""\n'
        "    return USERS.get(uid)\n"
    )
    PARENT = (
        "class Handler:\n    def handle(self, payload):\n"
        '        """Return a dict: the caller reads its keys."""\n        raise NotImplementedError\n\n'
        "    def parse(self, payload):\n"
        '        """The payload as a list, or None when it is empty."""\n        return list(payload) or None\n'
    )
    GREET = 'from shop.users import find_user\n\n\ndef greet(uid):\n    return find_user(uid)["name"]\n'
    SHOWN = (
        "- `shop/greet.py` calls `find_user()`, defined in `shop/users.py`:\n"
        "   4 | def find_user(uid):\n"
        '   5 |     """The user, or None when no user has the id."""\n'
        "   6 |     return USERS.get(uid)"
    )

    def shop(self, tmp_path: Path, changed: dict[str, str]) -> Path:
        """A repository whose branch `change` writes `changed`, its other files left alone."""
        repo = tmp_path / "shop"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "shop/__init__.py", "")
        _committed(repo, "shop/users.py", self.USERS)
        _committed(repo, "shop/base.py", self.PARENT)
        _git_in(repo, "checkout", "-q", "-b", "change")
        for path, text in changed.items():
            _committed(repo, path, text)
        return repo

    def test_the_guidelines_are_shown_the_function_an_added_line_calls_from_a_file_the_change_left_alone(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from adk_review.collect.git import collect_evidence
        from adk_review.judge import agents

        evidence = collect_evidence(str(self.shop(tmp_path, {"shop/greet.py": self.GREET})), BASE, "change")
        assert evidence["callees"] == self.SHOWN
        section = agents.for_the_guidelines(evidence["blast"], evidence["callers"], evidence["callees"])
        assert "\n## What the change calls\n" in section and "\n<<< callees " in section, section
        assert "     return USERS.get(uid)" in section, "fenced, as the change is"
        monkeypatch.setattr(agents, "CALLEES_CAP_CHARS", 60)
        cut = agents.for_the_guidelines(evidence["blast"], evidence["callers"], evidence["callees"])
        assert "[… cut at 60 characters]" in cut

    def test_a_function_called_through_its_module_is_shown_once_however_often_it_is_called(
        self, tmp_path: Path
    ) -> None:
        from adk_review.collect.git import collect_evidence

        twice = self.GREET + (
            "\n\ndef shout(uid):\n    from shop import users\n\n    return users.find_user(uid)['name'].upper()\n"
        )
        evidence = collect_evidence(str(self.shop(tmp_path, {"shop/greet.py": twice})), BASE, "change")
        assert evidence["callees"] == self.SHOWN

    def test_the_parent_method_a_change_overrides_and_one_it_calls_through_self_are_shown(self, tmp_path: Path) -> None:
        from adk_review.collect.git import collect_evidence

        orders = (
            "from shop.base import Handler\n\n\nclass Orders(Handler):\n    def handle(self, payload):\n"
            "        return len(self.parse(payload))\n"
        )
        evidence = collect_evidence(str(self.shop(tmp_path, {"shop/orders.py": orders})), BASE, "change")
        assert evidence["callees"] == (
            "- `shop/orders.py` overrides `Handler.handle()`, defined in `shop/base.py`:\n"
            "   2 |     def handle(self, payload):\n"
            '   3 |         """Return a dict: the caller reads its keys."""\n'
            "   4 |         raise NotImplementedError\n"
            "- `shop/orders.py` calls `Handler.parse()`, defined in `shop/base.py`:\n"
            "   6 |     def parse(self, payload):\n"
            '   7 |         """The payload as a list, or None when it is empty."""\n'
            "   8 |         return list(payload) or None"
        )

    def test_a_function_whose_lines_the_change_edited_is_in_the_diff_and_not_shown(self, tmp_path: Path) -> None:
        from adk_review.collect.git import collect_evidence

        edited = self.USERS.replace("return USERS.get(uid)", "return USERS[uid]")
        repo = self.shop(tmp_path, {"shop/users.py": edited, "shop/greet.py": self.GREET})
        assert collect_evidence(str(repo), BASE, "change")["callees"] == ""

    def test_a_definition_in_a_file_that_holds_a_private_key_is_named_and_never_shown(self, tmp_path: Path) -> None:
        from adk_review.collect.git import collect_evidence

        head, foot = "-----BEGIN RSA " + "PRIVATE KEY-----", "-----END RSA " + "PRIVATE KEY-----"
        keyed = self.USERS + f'\n\nKEY = """\n{head}\n{"FAKEKEYBODY" + "A" * 53}\n{foot}\n"""\n'
        repo = tmp_path / "keyed"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "shop/__init__.py", "")
        _committed(repo, "shop/users.py", keyed)
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "shop/greet.py", self.GREET)
        callees = collect_evidence(str(repo), BASE, "change")["callees"]
        assert callees == (
            "- `shop/greet.py` calls `find_user()`, defined in `shop/users.py`, "
            "a file that holds a private key: not shown"
        )

    def test_a_long_definition_and_many_definitions_are_cut_and_counted(self, tmp_path: Path, monkeypatch) -> None:
        from adk_review.collect import change
        from adk_review.collect.git import collect_evidence

        both = self.GREET.replace("from shop.users", "from shop.base import Handler\nfrom shop.users") + (
            "\n\nclass Orders(Handler):\n    def handle(self, payload):\n        return self.parse(payload)\n"
        )
        repo = self.shop(tmp_path, {"shop/greet.py": both})
        monkeypatch.setattr(change, "CALLEES_SHOWN", 1)
        monkeypatch.setattr(change, "CALLEE_LINES", 2)
        assert collect_evidence(str(repo), BASE, "change")["callees"] == (
            "- `shop/greet.py` calls `find_user()`, defined in `shop/users.py`:\n"
            "   4 | def find_user(uid):\n"
            '   5 |     """The user, or None when no user has the id."""\n'
            "     | … 1 more line, not shown\n"
            "- 2 more definitions the change calls or overrides, not shown"
        )

    def test_what_the_source_calls_comes_before_what_a_test_calls(self, tmp_path: Path, monkeypatch) -> None:
        """Measured on this repository's own history: the tests' calls took three of the eight places, before calls
        from the source the correctness and security guidelines judge, by path alone."""
        from adk_review.collect import change
        from adk_review.collect.git import collect_evidence

        test = "from shop.base import Handler\n\n\ndef test_parse():\n    assert Handler().parse([1]) == [1]\n"
        repo = self.shop(tmp_path, {"tests/test_a.py": test, "zz/greet.py": self.GREET})
        monkeypatch.setattr(change, "CALLEES_SHOWN", 1)
        assert collect_evidence(str(repo), BASE, "change")["callees"].startswith(
            "- `zz/greet.py` calls `find_user()`, defined in `shop/users.py`:\n"
        )

    def test_a_debug_log_holds_no_credential_from_what_the_change_calls(self, tmp_path: Path, cli) -> None:
        """Read by review, and reproduced: the checks node wrote the section into the session's state, and ADK's debug
        plugin logged that change of state with a helper's key in the clear, though the model was sent it redacted."""
        key = "AKIA" + "IOSFODNN7" + "EXAMPLE"  # joined: the push check reads every commit
        users = self.USERS.replace("    return USERS.get(uid)\n", f'    token = "{key}"\n    return USERS.get(uid)\n')
        repo = tmp_path / "keyed"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "shop/__init__.py", "")
        _committed(repo, "shop/users.py", users)
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "shop/greet.py", self.GREET)
        log = tmp_path / "debug.yaml"
        argv = [str(repo), "--head", "change", "--base", BASE, "--check", "guideline.correctness", "--no-verify"]
        cli.main([*argv, "--debug-log", str(log)])
        written = log.read_text(encoding="utf-8")
        assert "find_user" in written and key not in written

    def test_a_class_listing_a_parent_many_times_reads_each_class_once(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review, and reproduced: `class A(A, A, ...)` multiplied the classes read at every level, 177 s of
        gathering at forty, before any model call."""
        from adk_review.collect import change
        from adk_review.collect.git import collect_evidence

        bases = ", ".join(["A"] * 8)
        looped = f"class A({bases}):\n    def run(self):\n        return self.missing()\n"
        calls: list[int] = []
        real = change._resolved
        monkeypatch.setattr(change, "_resolved", lambda *args: calls.append(1) or real(*args))
        collect_evidence(str(self.shop(tmp_path, {"shop/looped.py": looped})), BASE, "change")
        assert len(calls) < 100, len(calls)

    def test_a_definition_whose_signature_the_diff_does_not_show_is_shown(self, tmp_path: Path) -> None:
        """Read by review, and reproduced: one method edited far below its class's first line put the class's span in
        the diff, so a new call building the class was shown neither `class Account` nor its docstring."""
        from adk_review.collect.git import collect_evidence

        methods = "".join(f"    def m{i}(self):\n        return {i}\n\n" for i in range(12))
        account = (
            'class Account:\n    """An account; its balance in cents, an int."""\n\n'
            f"{methods}    def total(self):\n        return self.balance\n"
        )
        repo = tmp_path / "account"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "shop/__init__.py", "")
        _committed(repo, "shop/account.py", account)
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "shop/account.py", account.replace("return self.balance", "return self.balance * 1"))
        _committed(
            repo, "shop/open.py", "from shop.account import Account\n\n\ndef open_account():\n    return Account()\n"
        )
        callees = collect_evidence(str(repo), BASE, "change")["callees"]
        assert "calls `Account()`, defined in `shop/account.py`" in callees, callees

    def test_a_definition_the_diff_shows_whole_is_not_shown_again(self, tmp_path: Path) -> None:
        """Read by review, and reproduced: a function added right below `find_user` and calling it showed
        `find_user` whole as the diff's context, and the section took one of its places for it again."""
        from adk_review.collect.git import collect_evidence

        below = self.USERS + "\n\ndef greet(uid):\n    return find_user(uid)['name']\n"
        repo = self.shop(tmp_path, {"shop/users.py": below})
        assert collect_evidence(str(repo), BASE, "change")["callees"] == ""

    def test_a_name_the_function_binds_itself_is_not_read_as_the_modules(self, tmp_path: Path) -> None:
        """Read by review, and reproduced: a parameter named as a module-level function was shown that function's
        contract, which the call never reaches."""
        from adk_review.collect.git import collect_evidence

        shadowed = (
            "from shop.users import find_user\n\n\ndef greet(uid, find_user):\n    return find_user(uid)['name']\n"
        )
        repo = self.shop(tmp_path, {"shop/greet.py": shadowed})
        assert collect_evidence(str(repo), BASE, "change")["callees"] == ""

    def test_only_the_definitions_shown_are_read_for_a_private_key(self, tmp_path: Path, monkeypatch) -> None:
        """Read by review, and measured: every distinct definition was rendered, its file read for a key, before the
        list was cut to its eight: 13 s for a change calling 3,000 functions."""
        from adk_review.collect import change
        from adk_review.collect.git import collect_evidence

        helpers = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(10))
        calls = "from shop.helpers import " + ", ".join(f"f{i}" for i in range(10)) + "\n\n\ndef run():\n"
        calls += "".join(f"    f{i}()\n" for i in range(10))
        repo = tmp_path / "many"
        repo.mkdir()
        _git_in(repo, "init", "-q", "-b", BASE)
        _committed(repo, "shop/__init__.py", "")
        _committed(repo, "shop/helpers.py", helpers)
        _git_in(repo, "checkout", "-q", "-b", "change")
        _committed(repo, "shop/run.py", calls)
        read: list[str] = []
        real = change.holds_private_key
        monkeypatch.setattr(change, "holds_private_key", lambda text: read.append(text) or real(text))
        monkeypatch.setattr(change, "CALLEES_SHOWN", 2)
        callees = collect_evidence(str(repo), BASE, "change")["callees"]
        assert callees.endswith("- 8 more definitions the change calls or overrides, not shown"), callees
        assert sum(text.startswith("def f0():") for text in read) <= 2, "the helpers' file, once a definition shown"

    def test_nothing_is_gathered_where_no_guideline_reads_it(self, tmp_path: Path) -> None:
        from adk_review.collect.git import collect_evidence

        repo = self.shop(tmp_path, {"shop/greet.py": self.GREET})
        assert collect_evidence(str(repo), BASE, "change", guidelines=False).get("callees", "") == ""

    def test_a_guideline_and_a_verifier_are_sent_what_the_change_calls(self, tmp_path: Path, cli, asked) -> None:
        from adk_review.judge import verify

        repo = self.shop(tmp_path, {"shop/greet.py": self.GREET})
        cli.main([str(repo), "--head", "change", "--base", BASE, "--check", "guideline.correctness", "--no-verify"])
        instructions = [instruction for instruction, _turns in asked]
        assert instructions and all("## What the change calls" in text for text in instructions), instructions
        assert all("   6 |     return USERS.get(uid)" in text for text in instructions)
        assert "## What the change calls" in verify.calls("", self.SHOWN)


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
        # Read by review: under `--all --path-exclude`, Health said it measured every file, the excluded ones too.
        said = "\n".join(_health(many, Outcome(), None, ("src",), ("src/legacy",)))
        assert "every file at the head under `src`, except `src/legacy`" in said, said

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
        assert grade(profile(75, 25, 0, 0, python=(9, 9))) == (None, "9 Python source file(s) parsed. A grade needs 10")
        assert grade(profile(75, 25, 0, 0, python=(100, 89))) == (
            None,
            "89 of 100 Python source files parsed. A grade needs 90%",
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
        assert health["complexity"]["grade"] is None and "A grade needs 10" in health["complexity"]["why"]
        assert "- maintainability  not graded: 5 Python source file(s) parsed. A grade needs 10" in out
        assert health["found"]["rule.secrets"]["source"]["blocker"] >= 1
        assert cli.main([*whole, "--previous", str(first), "--json", str(second)]) in (1, 3)
        assert "(was " in capsys.readouterr().out
        changed = tmp_path / "change.json"
        assert run_review(cli, demo_repo, "--profile", "rules-only", "--json", str(changed)) == 1
        assert "## Health" not in capsys.readouterr().out
        assert json.loads(changed.read_text(encoding="utf-8"))["health"] is None

"""The rules' helpers, through the one door a rule imports: each reader on what it must find and what it must leave
alone, the contract's sentence for a check that returns the wrong shape, and the catalog holding every rule to its own
examples. Offline and model-free, as the rules are."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from conftest import catalog

from adk_review.core.rules import run_rule
from adk_review.judge.rule_helpers import (
    ChangedFile,
    FileRule,
    Hit,
    Rule,
    Tree,
    TreeRule,
    argument,
    calls,
    calls_in,
    comment_of,
    functions_in,
    imports_in,
    matches,
    matching,
    name_of,
    nodes_in,
    try_rule,
    words,
)
from adk_review.judge.rule_helpers.testing import as_change


class TestTheLineReaders:
    @pytest.mark.parametrize(
        ("line", "names", "called"),
        [
            ("print(x)", ("print",), True),
            ("    print (total)", ("print",), True),
            ('x = f"{print(y)}"', ("print",), True),  # an f-string's field is code
            ("digest = fingerprint(data)", ("print",), False),
            ("# print(x)", ("print",), False),
            ('msg = "print(x)"', ("print",), False),
            ("def print(self, text):", ("print",), False),
            ("out.print(x)", ("print",), False),  # a method is `*.print`
            ("out.print(x)", ("*.print",), True),
            ("pdb.set_trace()", ("pdb.set_trace",), True),
            ("cursor.execute(sql)", ("*.execute",), True),
            ("client().get(url)", ("*.get",), True),  # a method of something with no name
            ("if (ready):", ("if",), False),  # a keyword before a bracket is no call
        ],
    )
    def test_calls_names_a_call_never_a_definition_a_comment_or_a_string(
        self, line: str, names: tuple[str, ...], called: bool
    ) -> None:
        """Measured before the helper: three of eight new line rules failed their first run on exactly these, and the
        shipped `no-print` flagged `def print(self, x):`."""
        assert calls(line, *names) is called

    def test_words_and_comment_of_read_no_string_as_code(self) -> None:
        assert words("except :  # all") == ["except", ":"]
        assert words('x = "a + b"') == ["x", "="]
        assert comment_of('url = "http://a#b"  # the real one') == "# the real one"
        assert comment_of('url = "http://a#b"') == "" and comment_of("x = 1") == ""


class TestThePathReaders:
    def test_a_glob_is_read_as_gitignore_reads_one(self) -> None:
        assert matches("src/a/b.py", "*.py") and not matches("src/a/b.PY", "*.py"), "a name alone, anywhere"
        assert matches("deploy/web.yaml", "deploy/*.yaml") and not matches("x/deploy/web.yaml", "deploy/*.yaml")
        assert matches("src/a/b/c.py", "src/**/*.py") and matches("src/c.py", "src/**/*.py")
        assert matches("src/payments/notes.txt", "src/payments/") and not matches("src/paymentsx.py", "src/payments/")
        assert matching(["b.py", "a.py", "c.txt"], "*.py", "*.md") == ["a.py", "b.py"]

    def test_a_glob_written_with_backslashes_reads_as_one_written_with_slashes(self) -> None:
        """Windows writes a path with `\\`, and `--path` takes one. Read by review, the outside audit's W1: `--skip
        tests\\test_*.py` and a folder `vendor\\` matched nothing, and the files they named were read."""
        assert matches("tests/test_billing.py", "tests\\test_*.py")
        assert matches("vendor/lib/x.py", "vendor\\") and not matches("vendorx/x.py", "vendor\\")


class TestTheSyntaxReaders:
    CODE = (
        "import os.path\n"
        "from requests import Session\n"
        "from os import *\n"
        "from .charge import Charge\n"
        "r = requests.post(\n"
        "    url,\n"
        "    json=payload,\n"
        ")\n"
        "s = requests.get(url, timeout=5)\n"
        "t = requests.get(url, **options)\n"
        "class Client:\n"
        "    def get(self):\n"
        "        def inner():\n"
        "            pass\n"
        "        try:\n"
        "            pass\n"
        "        except ValueError:\n"
        "            pass\n"
    )

    def test_calls_in_finds_a_call_wrapped_over_lines_and_argument_what_it_passes(self) -> None:
        module = ast.parse(self.CODE)
        found = calls_in(module, "requests.*")
        assert [call.lineno for call in found] == [5, 9, 10], "in the order written, at the line each starts"
        assert [argument(call, "timeout") is None for call in found] == [True, False, False], "`**options` may hold it"
        assert isinstance(argument(found[0], "url", 0), ast.Name) and argument(found[0], "data", 1) is None
        assert calls_in("does not parse", "requests.get") == [], "why a file has no module holds no call"

    def test_names_functions_imports_and_nodes_are_read_as_written(self) -> None:
        module = ast.parse(self.CODE)
        method = ast.parse("self.session.get(x)\n'{}'.format(y)\n")
        assert [name_of(node.value.func) for node in method.body] == ["self.session.get", ".format"]
        assert [function.name for function in functions_in(module)] == ["get", "inner"], "methods and nested ones"
        assert imports_in(module) == [(1, "os.path"), (2, "requests.Session"), (3, "os.*"), (4, ".charge.Charge")]
        assert [handler.lineno for handler in nodes_in(module, ast.ExceptHandler)] == [17]


def _file_rule(returned: object) -> FileRule:
    """A file rule whose check returns `returned`, whatever it is."""

    class Shape(FileRule):
        id, severity, title, fix = "shape", "minor", "a shape", "Fix it."

        def check(self, file: ChangedFile) -> object:  # noqa: ARG002 -- returns the shape under test
            return returned

    return Shape()


class TestTheContractSaysWhatAKindTakes:
    @pytest.mark.parametrize(
        ("returned", "said"),
        [
            (None, "check(file) returned None: it returns a list of line numbers, (line, what is wrong) pairs or Hits"),
            (
                ["a message"],
                "check(file) returned 'a message' among its hits. Each hit is a line number, a (line, what",
            ),
            ([True], "check(file) returned True among its hits"),  # a bool is no line number
            ([99], "check(file) names line 99 of a.py, which has 2 line(s)"),
        ],
    )
    def test_a_file_rule_that_returns_another_shape_is_a_sentence(self, returned: object, said: str) -> None:
        """Measured before: a `check` that forgot its `return` ended as "'NoneType' object is not iterable", a list
        of messages as a `Hit` taking 25 arguments, and line 10,000 of an 18-line file was a finding."""
        checked = run_rule(_file_rule(returned), *as_change({"a.py": "x = 1\ny = 2\n"}))
        assert checked.findings == [] and checked.could_not_look.startswith("rule shape raised "), checked
        assert said in checked.could_not_look, checked.could_not_look

    def test_a_bare_line_number_is_quoted_as_the_diff_shows_that_line(self) -> None:
        entry = {"path": "a.py", "text": "x = 1\ny = 2\n", "unread_text": None, "added": [(2, "y = 2")]}
        checked = run_rule(_file_rule([1, 2, (None, "the file as a whole")]), [entry], None)
        assert [(f.line, f.evidence) for f in checked.findings] == [
            (1, " x = 1"),
            (2, "+y = 2"),
            (None, "the file as a whole"),
        ]

    def test_file_changed_is_every_path_the_change_touched(self) -> None:
        """For a rule about the change as a whole, which once had to write its own `hits`."""

        class LockWithManifest(FileRule):
            id, severity, title, fix = "lock-with-manifest", "major", "a manifest changed alone", "Lock it."

            def check(self, file: ChangedFile) -> list[tuple[int | None, str]]:
                alone = file.path == "pyproject.toml" and "uv.lock" not in file.changed
                return [(None, "uv.lock did not change with it")] if alone else []

        rule = LockWithManifest()
        assert [hit.path for hit in try_rule(rule, {"pyproject.toml": "[project]\n"})] == ["pyproject.toml"]
        assert try_rule(rule, {"pyproject.toml": "[project]\n", "uv.lock": "version = 1\n"}) == []

    @pytest.mark.parametrize("reads", [False, True])
    def test_a_rule_of_any_kind_reads_a_test_only_where_it_says_it_reads_tests(self, reads: bool) -> None:
        """A rule about the product's code is spared the tests' deliberate fakes, unless it says it reads tests:
        measured, demo-derives-from flagged `TestInspectRepository` and `FakeModel` in this repository's tests. A test's
        text is all it is spared: the change's paths and the head's still list every test, so a rule asking whether
        a test came with a change, or whether `tests/` exists, is answered."""
        files = {"src/a.py": "x = 1\n", "tests/test_a.py": "x = 1\n"}

        class Line(Rule):
            id, severity, title, fix = "line", "minor", "a line", "Fix it."
            reads_tests = reads

            def check(self, path: str, line: str) -> bool:  # noqa: ARG002 -- every line, to see which are read
                return True

        class File(FileRule):
            id, severity, title, fix = "file", "minor", "a file", "Fix it."
            reads_tests = reads

            def check(self, file: ChangedFile) -> list[tuple[int | None, str]]:
                return [(None, " ".join(sorted(file.changed)))]

        class Whole(TreeRule):
            id, severity, title, fix = "whole", "minor", "a module", "Fix it."
            reads_tests = reads

            def check(self, tree: Tree) -> list[tuple[str, str]]:
                return [(path, "tests/ listed" if tree.has("tests/") else "no tests/") for path, _ in tree.modules()]

        read = {"src/a.py", "tests/test_a.py"} if reads else {"src/a.py"}
        for rule in (Line(), File(), Whole()):
            assert {hit.path for hit in try_rule(rule, files)} == read, rule.kind
        assert {hit.text for hit in try_rule(File(), files)} == {"src/a.py tests/test_a.py"}
        assert {hit.text for hit in try_rule(Whole(), files)} == {"tests/ listed"}

    def test_a_tree_rule_that_returns_bare_paths_is_a_sentence(self) -> None:
        """Measured before: a list of bare paths was read a character at a time, and `README.md` became a finding at
        the file `R` whose evidence was `E`."""

        class Paths(TreeRule):
            id, severity, title, fix = "paths", "minor", "paths", "Fix them."

            def check(self, tree: Tree) -> list[str]:  # noqa: ARG002 -- returns the shape under test
                return ["README.md"]

        checked = run_rule(Paths(), [], Tree(["README.md"], {}))
        assert "check(tree) returned 'README.md' among its hits. Each hit is a (path, what) pair" in (
            checked.could_not_look or ""
        )
        assert Paths._hit(Hit("a", None, "b"), Tree([], {})) == Hit("a", None, "b")


@pytest.fixture
def rules_folder(tmp_path: Path, monkeypatch):
    """A rules folder of the test's own: each file written into it is what the catalog finds, and its module is
    forgotten afterwards, so no other test reads it."""
    import adk_review.judge.rules as rules

    folder = tmp_path / "judge" / "rules"  # where a rule's file lies, as the catalog names its lines
    folder.mkdir(parents=True)
    monkeypatch.setattr(rules, "__path__", [str(folder)])
    written: list[str] = []

    def write(name: str, text: str) -> None:
        (folder / f"{name}.py").write_text(text, encoding="utf-8")
        written.append(f"adk_review.judge.rules.{name}")

    yield write
    for module in written:
        sys.modules.pop(module, None)


RULE = '''"""A rule written for this test."""

from __future__ import annotations

from ..rule_helpers import Rule, comment_of


class Marker(Rule):
    """A marker left in a comment."""

    id = "{id}"
    severity = "minor"
    title = "a marker"
    fix = "Remove it."
    bad = ({bad},)
    good = ({good},)

    def check(self, path: str, line: str) -> bool:
{body}
'''


class TestTheCatalogHoldsARuleToItsExamples:
    def _refused(self, write, name: str, text: str) -> str:
        write(name, text)
        with pytest.raises(ValueError, match=r"^judge/rules/") as refused:
            catalog()
        return str(refused.value)

    def test_a_rule_that_flags_nothing_in_its_bad_example_is_refused(self, rules_folder) -> None:
        """Measured before: a line rule that forgot its `return` ran, found nothing, and the review approved."""
        body = '        "MARK" in comment_of(line)'
        text = RULE.format(id="forgot-return", bad='"x = 1  # MARK"', good='"x = 1"', body=body)
        said = self._refused(rules_folder, "forgot_return", text)
        assert said == "judge/rules/forgot_return.py: 'forgot-return' flags nothing in its bad example 'x = 1  # MARK'"

    def test_a_rule_that_flags_its_good_example_or_its_own_source_is_refused(self, rules_folder) -> None:
        body = '        return "MARK" in line  # the word, wherever it stands: in a string too'
        text = RULE.format(id="reads-words", bad='"x = 1  # MARK"', good="\"label = 'MARK'\"", body=body)
        said = self._refused(rules_folder, "reads_words", text)
        assert "'reads-words' flags example.py:1 in its good example" in said, said
        assert said.endswith("(and 1 more)"), "its own source too: the check's own line holds the word"

    def test_a_rule_that_writes_its_own_hits_is_refused(self, rules_folder) -> None:
        body = '        return "MARK" in comment_of(line)\n\n    def hits(self, files, tree):\n        return []'
        text = RULE.format(id="own-hits", bad='"x = 1  # MARK"', good='"x = 1"', body=body)
        said = self._refused(rules_folder, "own_hits", text)
        assert said.endswith("'own-hits' writes its own `hits`. A rule writes `check`, and its kind finds the hits")

    def test_a_helper_in_the_rules_folder_points_to_the_helpers_folder(self, rules_folder) -> None:
        said = self._refused(rules_folder, "shared", "def is_money(name: str) -> bool:\n    return 'cents' in name\n")
        assert said == (
            "judge/rules/shared.py: a check's file holds one check, and this one holds 0: a rule is one class "
            "deriving from Rule, FileRule or TreeRule, and a helper rules share lives in judge/rule_helpers/"
        )

    def test_a_rule_that_raises_as_it_loads_is_named_with_its_line(self, rules_folder) -> None:
        said = self._refused(rules_folder, "raises", '"""A rule file that raises."""\n\nLIMIT = 1 / 0\n')
        assert said == "judge/rules/raises.py: ZeroDivisionError: division by zero (line 3)"

    def test_a_rule_missing_a_part_is_told_which(self, rules_folder) -> None:
        text = RULE.format(id="no-fix", bad='"x = 1  # MARK"', good='"x = 1"', body='        return "MARK" in line')
        said = self._refused(rules_folder, "no_fix", text.replace('    fix = "Remove it."\n', ""))
        assert said.endswith("Marker needs an id, a title and a fix, and has no fix")

    def test_every_shipped_rule_but_those_reading_ruff_carries_its_examples(self) -> None:
        """Examples are optional to the catalog, since a rule that reads ruff cannot run one without it; every other
        shipped rule carries a bad one and a good one, the pattern a project's rule copies."""
        rules = catalog().rules
        bare = [rule.id for rule in rules if not (rule.bad and rule.good)]
        named = [rule.id for rule in rules if getattr(rule, "tool", None)]
        assert bare == named == ["lint", "pitfalls", "radius-types", "simplify", "types"]


def test_a_rule_names_a_tool_an_adapter_runs_or_is_refused(rules_folder) -> None:
    """A rule names its outside tool; `collect/tools.py` holds one adapter a tool, ruff's and pyrefly's, and the core
    names them, so the catalog refuses a name no adapter runs without loading what runs one."""
    from adk_review.collect.tools import ADAPTERS
    from adk_review.core.rules import TOOLS

    assert set(ADAPTERS) == TOOLS == {"ruff", "pyrefly", "pyrefly-radius"}
    rule = (
        '"""A rule naming a tool no adapter runs."""\n\nfrom __future__ import annotations\n\n'
        "from ..rule_helpers import ChangedFile, FileRule\n\n\nclass NoTool(FileRule):\n"
        '    """Reads gitleaks."""\n\n    id = "no-tool"\n    severity = "minor"\n    title = "a leak"\n'
        '    fix = "Remove it."\n    tool = "gitleaks"\n\n'
        "    def check(self, file: ChangedFile) -> list[int]:\n        return []\n"
    )
    rules_folder("no_tool", rule)
    with pytest.raises(
        ValueError, match=r"names the tool 'gitleaks', which no adapter runs\. There are pyrefly, pyrefly-radius, ruff"
    ):
        catalog()


def test_a_rule_says_whether_it_reads_tests_with_true_or_false_or_is_refused(rules_folder) -> None:
    """`reads_tests = "no"` would read as true, and the rule would read the tests its author meant it to skip."""
    rule = (
        '"""A rule saying whether it reads tests in words."""\n\nfrom __future__ import annotations\n\n'
        "from ..rule_helpers import Rule\n\n\nclass Worded(Rule):\n"
        '    """Reads no test, it says."""\n\n    id = "worded"\n    severity = "minor"\n    title = "a word"\n'
        '    fix = "Remove it."\n    reads_tests = "no"\n\n'
        "    def check(self, path: str, line: str) -> bool:\n        return False\n"
    )
    rules_folder("worded", rule)
    with pytest.raises(ValueError, match="'worded' has reads_tests 'no', not True or False"):
        catalog()


def test_a_check_says_whether_it_is_a_cleanup_with_true_or_false_or_is_refused(rules_folder) -> None:
    """`cleanup = "no"` would read as true, and a hand-off would list a defect as a tidy and hold it back."""
    from adk_review.core.guidelines import Guideline
    from adk_review.judge.catalog import _guideline_problem

    rule = (
        '"""A rule saying whether it is a cleanup in words."""\n\nfrom __future__ import annotations\n\n'
        "from ..rule_helpers import Rule\n\n\nclass Worded(Rule):\n"
        '    """A defect, it says."""\n\n    id = "worded"\n    severity = "minor"\n    title = "a word"\n'
        '    fix = "Remove it."\n    cleanup = "no"\n\n'
        "    def check(self, path: str, line: str) -> bool:\n        return False\n"
    )
    rules_folder("worded", rule)
    with pytest.raises(ValueError, match="'worded' has cleanup 'no', not True or False"):
        catalog()

    class Worded(Guideline):
        id = "worded"
        title = "a word"
        role = "You judge the words."
        thinking_budget = 0
        cleanup = "no"

    assert _guideline_problem(Worded()) == "'worded' has cleanup 'no', not True or False"


def test_a_guidelines_models_name_an_arm_and_a_model_or_it_is_refused() -> None:
    """A guideline's own model is named per arm, in a `Models` with one field for each arm. Anything else, a plain
    map or a model that is no name, would run on nothing it says: refused at the start, as a check that cannot run."""
    from dataclasses import fields

    from adk_review.core.guidelines import Guideline, Models
    from adk_review.judge.catalog import _guideline_problem
    from adk_review.judge.settings import DEFAULTS

    assert {field.name for field in fields(Models)} == set(DEFAULTS), "one field for each arm the reviewer has"

    class Picked(Guideline):
        id = "picked"
        title = "a pick"
        role = "You judge the pick."
        thinking_budget = 0

    assert _guideline_problem(Picked()) is None, "no model of its own is fine"
    Picked.models = Models(copilot="claude-sonnet-4", gemini="gemini-2.5-pro")
    assert _guideline_problem(Picked()) is None
    for wrong in ({"copilot": "claude-sonnet-4"}, "gpt-4.1", Models(copilot=4.1)):  # type: ignore[arg-type]
        Picked.models = wrong
        assert "'picked' has models" in (_guideline_problem(Picked()) or ""), wrong


def test_a_guideline_takes_no_id_a_role_of_the_reviewer_has() -> None:
    """Read by the sixth review: a guideline's id names its model's variable, `REVIEW_<ID>_MODEL`. A guideline named
    `verifier` or `fallback` would read the variable of `--verify`'s model or of every role's fallback: refused."""
    from adk_review.core.guidelines import RESERVED_IDS, Guideline
    from adk_review.judge.catalog import _guideline_problem
    from adk_review.judge.verify import VERIFIER

    assert {VERIFIER, "fallback"} <= RESERVED_IDS

    class Named(Guideline):
        title = "a name"
        role = "You judge the name."
        thinking_budget = 0

    for reserved in sorted(RESERVED_IDS):
        Named.id = reserved
        assert f"{reserved!r} is a role of the reviewer's own" in (_guideline_problem(Named()) or ""), reserved

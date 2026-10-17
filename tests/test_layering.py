"""The package's grouping IS its architecture: every import points one way, and the domain runs nothing.

Each group may import itself and the groups before it — `core`, `collect`, `judge`, `deliver`, the steps of a
review — and the root modules, the loader's entry, the driver and the pre-push hook, may import anything. The
checks themselves, `judge/rules/` and `judge/guidelines/`, run nothing and hold one check a file and nothing else: a
guideline imports `core` alone, and a rule one door, `judge/rule_helpers/`, which imports `core` alone.
Read off the syntax tree, so a new module is placed by where it sits, not by a list kept here — and once at runtime,
because the tree cannot see what importing a module loads.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from conftest import BASE, HEAD, ROOT

PACKAGE = ROOT / "adk_review"
GROUPS = ("core", "collect", "judge", "deliver")
#: The two folders of checks: one class a file, and nothing else.
CHECKS = (PACKAGE / "judge" / "rules", PACKAGE / "judge" / "guidelines")
#: The one door a rule imports: the contract, passed on from `core`, and the helpers a rule reads with.
HELPERS = PACKAGE / "judge" / "rule_helpers"
#: What the pure group may not touch: anything that calls a model, runs a process, or talks to a network.
#: Measured by review: `import socket` or `urllib.request` in a rule passed the test when this named no network.
FORBIDDEN_IN_CORE = ("google", "litellm", "subprocess", "httpx", "asyncio", "os", "socket", "urllib", "http", "ssl")


def _group(path: Path) -> str:
    parts = path.relative_to(PACKAGE).parts
    return parts[0] if len(parts) > 1 else "root"


def _rank(group: str) -> int:
    return GROUPS.index(group) if group in GROUPS else len(GROUPS)


def _relative_imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level:
            target = path.parents[node.level - 1] / (node.module or "").replace(".", "/")
            yield node, target


def _absolute_imports(path: Path) -> list[str]:
    """Every module name `path` imports by its full name: `import x.y`, `from x.y import z`."""
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names.append(node.module or "")
    return names


def test_no_group_imports_a_group_after_it() -> None:
    upward = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for node, target in _relative_imports(path):
            source_group, target_group = _group(path), _group(target)
            if node.module is None:  # `from . import x`: x names the module or package
                target_group = _group(target / node.names[0].name)
            if _rank(target_group) > _rank(source_group):
                upward.append(f"{path.relative_to(PACKAGE)} imports {target_group} ({source_group} may not)")
    assert upward == [], "\n".join(upward)


def test_the_report_needs_only_the_domain() -> None:
    """The report renders what the driver hands it: the sources that ran, in order, and the spend as text. Measured
    by review: it imported the lane table, the ledger and the lint gate's name, and with them 155 ADK modules, for
    text it could simply be given."""
    reached = {
        _group(target) for path in (PACKAGE / "deliver").glob("*.py") for _node, target in _relative_imports(path)
    }
    assert reached <= {"core", "deliver"}, reached


def test_the_package_imports_its_own_modules_only_relatively() -> None:
    """The direction test reads relative imports, the form every module here uses; an import spelled from the top,
    `adk_review.…`, would pass it whatever it pointed at. Measured by review: `from adk_review.judge
    .guidelines import state_key`, planted in `collect/ruff.py`, passed every test in this file."""
    absolute = [
        f"{path.relative_to(PACKAGE)}: {name}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for name in _absolute_imports(path)
        if name.split(".")[0] == PACKAGE.name
    ]
    assert absolute == [], absolute


def test_the_core_runs_nothing_and_knows_no_model() -> None:
    # A rule, a guideline and the helpers a rule reads with run nothing too.
    checks = [path for folder in (*CHECKS, HELPERS) for path in folder.glob("*.py")]
    offenders = [
        f"{path.name}: {name}"
        for path in sorted([*(PACKAGE / "core").glob("*.py"), *checks])
        for name in _absolute_imports(path)
        if name.split(".")[0] in FORBIDDEN_IN_CORE
    ]
    assert offenders == [], offenders


def test_a_rule_imports_the_door_alone_and_a_guideline_and_the_door_the_core_alone() -> None:
    """A rule and a guideline are definitions, needing nothing that gathers, calls a model or reports. A guideline
    reads what a check is from `core`; a rule reads it, and its helpers, through one door, which reads `core` alone.
    One way in: a rule reaching past the door, into `core` or one helper's module, is a second way to write one."""
    rules, guidelines = CHECKS
    allowed = {
        rules: lambda target: target == HELPERS,
        guidelines: lambda target: _group(target) == "core",
        HELPERS: lambda target: _group(target) == "core" or HELPERS in target.parents,
    }
    reached = [
        f"{path.relative_to(PACKAGE)} imports {target.relative_to(PACKAGE)}"
        for folder, fits in allowed.items()
        for path in sorted(folder.glob("*.py"))
        for _node, target in _relative_imports(path)
        if not fits(target)
    ]
    assert reached == [], reached


def test_the_door_lists_exactly_what_it_passes_on() -> None:
    """The door's `__all__` is its word to every rule: a name bound and not listed, or listed and not bound, is a door
    that says one thing and does another."""
    import adk_review.judge.rule_helpers as door

    module = ast.parse((HELPERS / "__init__.py").read_text(encoding="utf-8"))
    bound = {
        alias.asname or alias.name
        for node in module.body
        if isinstance(node, ast.ImportFrom) and node.module != "__future__"
        for alias in node.names
    }
    assert sorted(door.__all__) == sorted(bound) and all(hasattr(door, name) for name in door.__all__)


def test_the_helpers_are_no_check_folder_and_no_agent() -> None:
    """The catalog reads `rules/` and `guidelines/` by name, never the helpers, so a class among them is never a
    check; and no `agent.py` sits among them, which `adk web`'s nested loader would take for an agent."""
    from adk_review.judge.catalog import catalog

    assert not (HELPERS / "agent.py").exists()
    assert all(type(rule).__module__.startswith("adk_review.judge.rules.") for rule in catalog().rules)


def test_a_check_folder_holds_its_checks_and_nothing_else() -> None:
    """One way to write a check: a file in its folder, named after the check it holds, and nothing beside them — no
    `__init__.py`, no guide, no template, which live in the README. The catalog refuses a file that holds no
    check, two, or one not named after it; this holds the folders to the same, and to holding nothing else."""
    from adk_review.judge.catalog import catalog

    found = catalog()
    for folder, checks in zip(CHECKS, (found.rules, found.guidelines), strict=True):
        entries = sorted(entry.name for entry in folder.iterdir() if entry.name != "__pycache__")
        assert entries and all(name.endswith(".py") and name != "__init__.py" for name in entries), entries
        assert [check.id for check in checks] == [name.removesuffix(".py").replace("_", "-") for name in entries]


def test_every_group_says_what_it_may_import() -> None:
    """The rule lives in the code as well: each package docstring states its own boundary."""
    for group in GROUPS:
        text = (PACKAGE / group / "__init__.py").read_text(encoding="utf-8")
        assert "import" in text.lower(), f"{group}/__init__.py does not say what it may import"


def test_the_core_imports_with_adk_absent_and_loads_none_of_what_it_may_not() -> None:
    """The claim at runtime: `core/`, the rules, their helpers and the guidelines can be read and tested without ADK
    installed. A fresh interpreter makes `google` unimportable, imports every core module and, through the catalog,
    every check, and reports what came with them — measured before the package root stopped importing the agent
    module: 187 ADK modules, then a `ModuleNotFoundError` with ADK absent."""
    modules = sorted(f"adk_review.core.{p.stem}" for p in (PACKAGE / "core").glob("*.py") if p.stem != "__init__")
    forbidden = [m for m in FORBIDDEN_IN_CORE if m != "os"]  # the interpreter itself imports os
    probe = (
        "import sys\n"
        "sys.modules['google'] = None  # an import of it raises, as it does when ADK is not installed\n"
        f"for name in {modules!r}:\n    __import__(name)\n"
        "from adk_review.judge.catalog import catalog\n"
        "found = catalog()\n"
        "assert found.rules and found.guidelines, 'each folder holds one check at least'\n"
        f"loaded = [m for m in sys.modules if sys.modules[m] and m.split('.')[0] in {forbidden!r}]\n"
        "inside = [m.split('.', 1)[1] for m in sys.modules if m.startswith('adk_review.')]\n"
        "loaded += [m for m in inside if m not in ('judge', 'judge.catalog', 'judge.rules', 'judge.guidelines')\n"
        "           and not m.startswith(('core', 'judge.rules.', 'judge.guidelines.', 'judge.rule_helpers'))]\n"
        "print(sorted(loaded))\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]", done.stdout


def test_the_modes_that_read_the_checks_alone_start_without_the_engine(tmp_path: Path) -> None:
    """`--list-checks`, `--explain`, `--try`, `--version` and a usage error read the checks and the config alone, and
    the pre-push hook imports the driver: measured, best of seven, each mode started in about 0.7 s with ADK imported
    first, and in 0.11 to 0.15 s without. A fresh interpreter makes `google` unimportable and runs each; a review, and
    so the engine, is imported where one starts."""
    sample = tmp_path / "sample.py"
    sample.write_text("total = 1\n", encoding="utf-8", newline="\n")
    asked = [["--list-checks"], ["--explain", "rule.secrets"], ["--try", str(sample)], ["--version"], ["--no-such"]]
    probe = (
        "import contextlib, io, sys\n"
        "sys.modules['google'] = None  # an import of it raises, as it does when ADK is not installed\n"
        "from adk_review import push\n"
        "from adk_review.review import main\n"
        "codes = []\n"
        f"for argv in {asked!r}:\n"
        "    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):\n"
        "        try:\n"
        "            codes.append(main(argv))\n"
        "        except SystemExit as stopped:  # what argparse ends --version and a usage error with\n"
        "            codes.append(stopped.code)\n"
        "print(codes, [m for m in sys.modules if m.startswith('google.')])\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[0, 0, 0, 0, 2] []", done.stdout


def test_a_command_loads_no_litellm_until_the_copilot_arm_is_built() -> None:
    """Measured before: `import litellm` at the top of each command cost about a second of every start, `--help`
    included, and on its own import LiteLLM read `.env` and fetched a price list over the network. It is the
    Copilot arm's alone; a fresh interpreter proves the command's import does not load it."""
    probe = (
        "import sys; import adk_review.review\nprint(sorted(m for m in sys.modules if m.split('.')[0] == 'litellm'))\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]", done.stdout[:200]


def test_a_review_with_no_guideline_runs_with_the_engine_absent(demo_repo: Path, tmp_path: Path) -> None:
    """A profile that names no guideline calls no model, and imports none of the engine either: the rules run as
    `--try` runs them, and the report reads the same. Measured, best of three by the installed command: the demo's
    rules-only review took 0.87 to 0.94 s, of which 0.41 s imported the judge's engine to run a graph holding no
    agent, and the pre-push hook, whose profile is `default-rules`, paid it on every push. A fresh interpreter makes
    `google` unimportable and reviews the demo branch, `--cache` on, whose key once cost 0.24 s of engine import for
    the fallback model's name; `--trace`, `--debug-log` and `--record` are the engine's own and still bring it."""
    out, cache = tmp_path / "review.json", tmp_path / "cache"
    argv = [str(demo_repo), "--head", HEAD, "--base", BASE, "--profile", "rules-only", "--json", str(out)]
    argv += ["--cache", str(cache)]  # measured: the cache's key once imported the engine for one variable's name
    probe = (
        "import contextlib, io, sys\n"
        "sys.modules['google'] = None  # an import of it raises, as it does when ADK is not installed\n"
        "import adk_review.review as review\n"
        "review.load_dotenv = lambda *args, **kwargs: False  # the central .env left unread, as every test leaves it\n"
        "with contextlib.redirect_stdout(io.StringIO()):\n"
        f"    code = review.main({argv!r})\n"
        "print(code, [m for m in sys.modules if m.startswith('google.')])\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "1 []", done.stdout + done.stderr
    import json

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["usage"] == {"calls": 0, "attempted": 0, "tokens": 0, "ceiling": None, "by_agent": {}}
    assert data["seconds"] == {} and data["secrets_redacted"] == 0, data


def test_every_key_the_evidence_carries_is_spelled_once_in_core(demo_repo: Path) -> None:
    """The evidence is one dict, as ADK's state and a kept review hold it, read by `collect`, the checks node, the
    adapters, the report and the driver: `core/evidence.py` spells each key once, and the collector and the rules
    fill no other. Measured before it was spelled: a key the checks node blanked after the rules was read by the JSON
    report, and `added_at` came in to outlive it."""
    from adk_review.collect.git import collect_evidence
    from adk_review.core.evidence import Entry, Evidence
    from adk_review.judge.catalog import catalog
    from adk_review.judge.checks import run_rules

    evidence = collect_evidence(str(demo_repo), BASE, HEAD)
    assert evidence["status"] == "success", evidence
    assert set(evidence) == set(Evidence.__annotations__) - {"error_message"}
    run_rules(evidence, catalog().rules)  # the lint rule sets `diagnostics` on the copies it reads; none on these
    spelled, required = set(Entry.__annotations__), Entry.__required_keys__
    for entry in evidence["files"]:
        assert required <= set(entry) <= spelled, (entry["path"], set(entry) ^ spelled)


def test_the_login_starts_without_the_engine() -> None:
    """`adk-review-login` is a one-time command that reads two variables and talks to GitHub: measured, importing the
    judge's config for the token's path cost it 0.27 s of ADK and google-genai, a start of 0.32 s against 0.05 s. The
    environment's readers live apart from what is built from them, and the login imports those alone."""
    probe = (
        "import sys\n"
        "sys.modules['google'] = None  # an import of it raises, as it does when ADK is not installed\n"
        "import adk_review.login\n"
        "print([m for m in sys.modules if m.startswith('google.')])\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]", done.stdout

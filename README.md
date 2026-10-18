# adk-review

A code reviewer for a git branch, built on Google ADK. It reads what a branch changes and judges it two ways. Rules,
decided by code, read every line the change added. Guidelines, judged by a model, read the diff. Code then decides the
verdict and the exit code: a model never decides whether a build passes. The repository under review is only read,
through read-only git: none of its code runs, no setting of the review comes from it, and nothing is written into it.

`GUIDE.md`, beside this file, is the user's guide: every flag, exit code, check, setting, JSON field and limit, held
to the code by a test. This file starts with a first review, then says how a review runs, how its code is laid
out and configured, and how to write a rule or a guideline of your own.

## Quick start

The reviewer needs Python 3.13, uv, and git 2.41 or later on `PATH`. Ruff, which the lint rule runs, is installed
with it. Three commands from the root of this checkout give a first review, with no model and no key:

```bash
uv sync --locked                             # once: the reviewer and its locked dependencies
uv run python scripts/make_demo_repo.py      # a throwaway repository with planted defects; prints the review to run
uv run adk-review <that path> --head feature/payments --base main --profile demo
```

The rules find the demo's hard-coded key pair, its shell call and a float division of cents: exit 1, a blocker.
`make demo` runs the last two. On a repository of your own, `uv run adk-review <repo> --base main --profile
default-rules` runs the default profile's rules alone, still with no model.

The guidelines need a model, on one of two arms. Set it in the shell, or in `.env` at the root of this checkout
(copy `.env.example`), which the shell wins over:

- **Copilot**, the arm when nothing is set: no key. Run `uv run adk-review-login` once, GitHub's device flow, and
  LiteLLM reads the token it caches; installed, the command is `adk-review-login`.
- **Gemini**: `REVIEW_PROVIDER=gemini` and `GOOGLE_API_KEY`, or through Vertex AI a project, as `.env.example` says.

Then drop `--profile`: `uv run adk-review <repo> --base main` runs the rules, then the guidelines. Both arms are
first-class; "Configuration" in `GUIDE.md` lists every variable.

Every mode is the same command:

```bash
uv run adk-review <repo> --base main                            # the default profile: the rules, then the guidelines
uv run adk-review <repo> --base main --profile default-rules    # the default's rules: no model, no key
uv run adk-review <repo> --head <branch> --base main --verify   # a second model judges each guideline finding
uv run adk-review <repo> --base main --max-tokens 20000         # the call that would pass the ceiling is refused
uv run adk-review <repo> --base main --profile rules-only       # every rule but the demo's: no model, no key
uv run adk-review <repo> --base main --path <folder>            # one folder of the change, the rest counted
uv run adk-review <repo> --all --profile rules-only             # every file at the head, not a change
uv run adk-review <repo> --staged --profile default-rules       # what is staged, against HEAD
uv run adk-review <repo> --base main --check rule.secrets       # one check alone, this once
uv run adk-review <repo> --base main --json review.json         # the report, and the same review as JSON
uv run adk-review <repo> --base main --dry-run                  # what a review would cover and run; nothing runs
uv run adk-review --list-checks                                 # every check: its kind, severity, profiles and file
uv run adk-review --explain rule.lint                           # one check whole, a rule with its own examples
```

`<repo>` is the path of the repository to review. A command that names none asks for it at a terminal, and a pipeline
that names none is refused, exit 2. `--help` prints every flag, and `uv run python -m adk_review.review` is the same
command spelled long.

To run it from any folder, install it as a command: `uv tool install <path or git URL of this repository>` puts
`adk-review`, its pre-push hook, `adk-review-push`, and the Copilot login, `adk-review-login`, on `PATH`: the reviewer
alone, with ruff beside it. An installed reviewer reads the model's settings from the shell alone, no `.env` being read outside the checkout, and its
config from beside itself: a team that wants another policy changes `config.toml` in its checkout and installs from
there, `--editable` so a change applies at once, or passes flags.

Chat runs the same checks: `uv run adk web .` at the root of the checkout, pick `adk_review`, and say what to
review, the repository's absolute path and its branches: "review /home/me/shop, branch feature/payments against
main". An intake agent confirms the repository and the commits through read-only tools and asks for what is
missing, told the repository its conversation already confirmed; the checks node gathers the evidence the command would for those
commits, runs the rules and writes the change the guidelines read, the command's own block under the same cap and
skip list; the guidelines judge; and a verdict agent writes the verdict for a person to read. A turn reviews only
the commits its own intake confirmed: a greeting, a question or a thanks after a review ends at the intake's answer,
one model call. Chat runs the `default` profile under `[review]`'s `fail_on`, `max_tokens`, `diff_cap`
and `skip`. Its verdict is a model's prose: the exit code and the second opinion are the command's. Chat asks
whether its arm can run once a turn, as the command asks before it starts, and answers the same sentence where it
cannot: on Copilot with no token cached, no login is begun on the server's console.

The tests run offline, on a fake model registered with ADK: `uv run pytest -q`.

## How a review runs

The order is the architecture: evidence and rules first, the guidelines' judgement second, code's decision last.
`review.py`, the command, is the driver that fixes it, and chat's graph keeps the same order.

1. **Settle what to review, before anything costs.** `config.toml` is read whole, every check is found and held to
   its own examples, and the profile's checks are chosen. The repository is the one named first, or asked for at a
   terminal; it is never assumed. The head is `--head`, or the branch checked out. The base is `--base`; with neither
   it nor `--all` or `--staged`, a person at a terminal is asked which, and a pipeline gets the change against `main`,
   or `master`, or else the branch `origin/HEAD` names. Where a guideline runs, the model's arm must be ready.
   Anything wrong here is one sentence on stderr and exit 2, before any model call.
2. **Gather the evidence, in plain Python.** Read-only git gives each changed file's whole diff and the lines it
   added, its text at the head for the file rules and, where a tree rule or a guideline runs, the head's tree and
   each changed file's blast radius. The block of diffs the guidelines read is built here, each diff capped and the
   block capped, every cut named.
3. **Run every rule** before any model call: in one graph node where a guideline follows, straight from the driver
   where none does. Each rule answers with what it found, or why it could not look.
4. **Let the guidelines judge the change.** Each is an ADK agent with a role of its own, and they run at once under a
   bound on the calls in flight. Every request is scrubbed of credentials before it is sent, and the branch's text
   sits between two marker lines it cannot forge.
5. **Ground what the guidelines found.** A finding stays only where its quote is whole lines of its own file's diff;
   the rest are dropped and counted. With `--verify`, a second model judges each guideline finding no rule already
   made, and one it refutes is dropped and named.
6. **Fold and decide.** Findings on the same lines fold into one, the most severe reading kept whole. The verdict is
   code's: a kept finding at or above `--fail-on` is `REQUEST_CHANGES`, exit 1. A hole — a check that failed or could
   not look, a changed file no check read whole, a dropped finding at the bar — is `DEGRADED`, exit 3. A hole never
   approves.
7. **Deliver.** The report goes to stdout, and the JSON (`--json`) and a hand-off for a coding agent (`--handoff`) to
   their files, whenever a review is decided.

Exit 0 approves; 1 is a finding at the bar; 2 a usage error before any model call; 3 a hole; 130 and 143 a run
stopped by Ctrl-C or by a job. "Exit codes" in `GUIDE.md` says what each covers.

## Layout

The code is grouped by the steps of a review, and each package may import only the packages before it: `core` →
`collect` → `judge` → `deliver`. `tests/test_layering.py` reads every import off the syntax tree and fails on one that
points the wrong way.

| Package | May import | Holds |
|---|---|---|
| `core/` | nothing else | `findings.py` (`Finding`, `Review`, `Checked`, the severity scale), `rules.py` (what a rule is: the `Rule`, the `FileRule` and the `TreeRule`, the `ChangedFile` and the `Tree` they read, the `Diagnostic` an outside tool hands a file rule, `Hit`, `UnreadError`, and `run_rule`, which runs any of them the same way), `guidelines.py` (what a guideline is), `health.py` (a whole project's measures under `--all`: what was read, size, McCabe complexity bands, each rule's findings in source and tests), `profiles.py` (a check's id and its state key, and the checks a profile turns on), `blast.py` (what at the head depends on each changed file, read from the syntax tree), `evidence.py` (what a review reads, each key spelled once: the `Evidence`, the `Change` of it the rules read, the `Failure` in its place, and each changed file in it, an `Entry`), `paths.py` (whether a glob names a path, as `.gitignore` reads one: the skip list's question, and a rule's), `secrets.py` (the credential shapes: what redaction hides and what the secrets rules ask), `text.py` (flat whitespace, a cut that names itself, lines as git numbers them), `verdict.py` (grounding, folding, deciding, `Outcome`, the exit codes) |
| `collect/` | `core` | `git.py` (read-only git: what a review covers, each file's diff and its text at the head, the head's tree, `collect_evidence`), `change.py` (the change as the evidence holds it, decided from the diffs alone: what the guidelines are shown of each file and the block they read, what no one could read, the blast radius), `history.py` (what a push sends, commit by commit, for the pre-push hook), `diff.py` (a unified diff read without git: the lines it adds, the cap, the fence each guideline reads it in), `ruff.py` (ruff over the changed Python files' texts, in a folder of its own), `tools.py` (each outside tool a rule may name, and the adapter that runs it: ruff's) |
| `judge/` | `core`, `collect` | `rules/` (every rule, one file each, and nothing else), `guidelines/` (every guideline, one file each, and nothing else), `rule_helpers/` (the one door a rule imports: the contract, passed on from `core`, and helpers named after what they read), `catalog.py` (finds every check, reads `config.toml`, chooses what a profile runs, for the command and chat alike), `checks.py` (`run_rules`, every rule over a `Change`, and the node that runs it before the guidelines, in both shapes), `agents.py` (a guideline as an agent: the instruction around its role, the fences, the state keys spelled once, the read-back), `settings.py` (what the environment says: the arm, each role's model name, the fallback, Copilot's token path; read, nothing built, so the login and a rules-only review's cache key import no engine), `config.py` (the models built from it, and the request every call carries: temperature zero, a deadline, retries; readiness), `plugins.py` (redaction, the ledger and its ceiling, provider errors, progress, the recorder, and in chat the readiness check and the judging agents' one turn), `graph.py` (the graph and the `App` in both shapes; not `agent.py`, since `adk web` lists any folder holding one as an agent), `run.py` (one seeded run through the engine, guarded, and what it left in state read back), `tools.py` (chat's model-facing tools and the guardrail in front of them), `verify.py` (the second opinion) |
| `deliver/` | `core` alone: the driver hands it the rest | `report.py` (the text report, the JSON, the hand-off, a previous report read back for `--previous`, what `--dry-run`, `--list-checks`, `--explain` and `--try` print, and the rules that keep the branch's text from forging any of them) |
| the root | everything | `review.py` (the command: the driver that fixes the order of the steps; a profile with no guideline runs the rules as `--try` does, the engine never imported), `push.py` (the pre-push hook: what a push sends, reviewed with `default-rules`, and each of its commits read with `push-history`, the credential rules, for git's hook and pre-commit's alike), `login.py` (the Copilot arm's one-time login, `adk-review-login`: GitHub's device flow, the token cached where LiteLLM reads it), `agent.py` (the chat `App` for ADK's loader, `adk web` and `adk run`, the one place that wants it), `__init__.py` (answers `agent` without loading it, so `core/` imports alone), `config.toml` (the one config file) |

`core/`, the checks and `judge/rule_helpers/` import nothing that calls a model or runs a process — no `google`,
`litellm`, `subprocess`, `httpx`, `asyncio`, `os`, `socket`, `urllib`, `http` or `ssl` — and a test imports them with
ADK absent. A guideline imports `core` alone, and a rule imports the door, `judge/rule_helpers/`, alone. Each check
folder holds one check a file, named after it, and nothing else: no `__init__.py`, no guide, no template. The
templates are in this file, under "Write a rule" and "Write a guideline".

## Configuration

`config.toml`, beside `review.py`, is the one config file. `[review]` holds the review policy, each setting a flag's
default that the flag overrides for one run: `fail_on`, `max_tokens`, `verify`, `diff_cap` and `skip`. `[profiles]`
holds named lists of check ids, or the groups `rules` and `guidelines`. The file is read whole: a section or a setting
it does not know, one missing, or one of the wrong kind is a sentence and exit 2, never a setting quietly left at
another value. It is the one place the review policy lives: nothing of it is read from the repository under review,
and `--dry-run` names the file read.

The model comes from the shell, or from the `.env` at the root of the checkout, which never wins over the shell:
`REVIEW_PROVIDER` picks the arm, `copilot` when unset or `gemini`, and `REVIEW_MODEL` the model, blank for `gpt-4.1`
or `gemini-2.5-flash`; `REVIEW_VERIFIER_MODEL` and `REVIEW_FALLBACK_MODEL` give `--verify` and every role's fallback a
model of their own. Both arms are first-class; `copilot` is only the default. Readiness is asked before any call:
Copilot's cached token, from the one-time `uv run adk-review-login`, or Gemini's `GOOGLE_API_KEY` or
Vertex AI project, which google-genai itself answers, never a copy of its rules; on the Gemini arm a model ADK would
send to another provider, `gpt-*` or `claude-*`, is refused. Every certificate is verified through the operating
system's own store, so a company proxy's root the machine trusts, the reviewer trusts too. "Configuration" in
`GUIDE.md` lists each variable with its default, and `.env.example` shows each arm's block.

## Checks and profiles

Every check is one file. A rule, decided by code, is a class in `judge/rules/`; a guideline, judged by a model, is a
class in `judge/guidelines/`. Each is named after its file — `judge/rules/no_print.py` holds the rule `no-print` —
and has one id everywhere, `rule.<name>` or `guideline.<name>`: in `config.toml`, on the command line and as its own
section of the report. Inside ADK, whose names must be identifiers, the same check is `rule_<name>`. The catalog,
`judge/catalog.py`, finds every check in those two folders and nowhere else, for the command and chat alike. A file
there that will not load, holds no check or two, names its check after another file, or fails its own examples is a
sentence and exit 2, never a check quietly left out.

A rule is one of three kinds, one mental model — a line, a file, the tree. A `Rule`'s `check(path, line)` reads each
line the change added and answers yes or no. A `FileRule`'s `check(file)` reads each file the change touched, whole
at the head — its lines, the numbers of the lines the change added, every path the change touched and, for Python,
its module — for what one line cannot show, as `reviewer_instructions.py` reads a directive wrapped over lines. A
`TreeRule`'s `check(tree)` reads the head instead: every path, and each Python module parsed once with the blast
radius, for what must exist, define a name, derive from a parent or take a parameter. A guideline names its role and
a thinking budget; the instruction around the role is every guideline's.

The rules shipped with the reviewer are written as a project writes its own:

- for any project: `secrets` (a vendor's credential shape in an added line), `named-secrets` (a value assigned to a
  name that says secret, where no vendor's shape is), `lint` (what ruff reports, at a severity and with a fix for
  each code), `reviewer-instructions` (text that tries to instruct the reviewing model, read as a model reads it:
  across wrapped lines, through hidden and full-width characters, and in a file's name), `encoding-declaration` (a
  source encoding that turns plain text into other code: a hole, since no rule read the code Python would run),
  `lockfile-sources` (a lockfile fetching a package from outside its package manager's public registry, the one
  check of a lockfile's sources, since the shipped skip list leaves lockfiles to the rules), `pinned-actions` (a
  workflow or an action running another action at a tag, a branch or a short SHA, which can be moved to other code
  after review, not its full commit SHA), `hidden-text` (tag
  characters and bidirectional controls, in any file) and `steering-files` (a change to a file another tool or agent
  obeys: a coding agent's instructions or settings, Copilot's and Gemini CLI's among them, a CI workflow, a hook, a
  `.pth` file);
- a project's own conventions, as examples: `no-print`, `private-patch` and `tests-offline`;
- one small payments project's own rules, written for the demo repository and named `demo-` so none is taken for a rule
  a project needs: `demo-money-in-cents`, and the tree rules `demo-required-paths`, `demo-module-defines`,
  `demo-parameters` and `demo-derives-from`. A project replaces them with its own.

The guidelines are `security`, `tests`, `complexity` and `correctness`.

A profile is a list in `config.toml`, and a check runs only where a profile names it, or alone with `--check ID`:

- `default` runs when no `--profile` names another, and the file must hold it: the basics, `rule.secrets`,
  `rule.named-secrets`, `rule.lint`, `rule.reviewer-instructions`, `rule.encoding-declaration`,
  `rule.lockfile-sources` and `rule.pinned-actions`, and the guidelines `security`, `tests` and `complexity`;
- `default-rules` is the default's rules and no model, so it needs no key and fits any project's every pull request;
  a test keeps it to the default's rules;
- `push-history` is what the pre-push hook reads each commit a push sends with, line by line: the credential rules;
- `rules-only` is every rule but the payments demo's, each by its id, and no model;
- `full` is `rules-only`'s rules and every guideline, through the group `guidelines`;
- `demo` is the payments demo's: every rule, through the group `rules`, its own among them, and no model.

A new rule is added to `rules-only` and `full` by its id, which a test holds to every rule whose id does not start
with `demo-`, and joins `demo` through the group. `--list-checks` prints every check with its
kind, its severity, the profiles that run it and its file; `--explain ID` prints one whole, a rule with its own
examples. The report's header names the profile and every check it left off: a guideline left off was never asked,
so it never reads as a failed one. Redaction is not a check: every model call is scrubbed of known credential shapes,
whatever the profile, the secrets rule off included.

## Write a rule

A rule is one Python class in one file of `judge/rules/`, named after the file: `no_todo.py` holds the rule
`no-todo`. Drop the file in and the catalog finds it; there is nothing to register, and no other folder is read for
rules. The folder holds rules and nothing else, so the templates are here, one for each kind, smallest first. Every
rule imports one line from one door, `judge/rule_helpers/`: the kind it is, and the helpers its `check` reads with.

A line rule reads each line the change added, one at a time:

```python
"""A marker left in the code: a TODO a change adds is work no one owns."""

from __future__ import annotations

from ..rule_helpers import Rule, comment_of

#: What the rule looks for, named once here: the reader of a finding reads the title and the fix, the reader of the
#: rule reads this and the check.
MARKER = "TODO"


class NoTodo(Rule):
    """One sentence on why a project wants this rule: what goes wrong when the shape it names is merged."""

    id = "no-todo"  # the file's name with `-` for `_`: a profile names it `rule.no-todo`
    severity = "minor"  # blocker: must not merge; major: fix before merge; minor: worth fixing
    title = "a marker left in the code"  # one line naming the problem, as the report prints it
    fix = "Resolve it before merging, or move it to the tracker."  # what to change, concretely
    bad = ("x = 1  # TODO: handle refunds",)  # what it flags: held to at every start
    good = ('label = "TODO list"', ("notes.md", "TODO: write the guide"))  # what it leaves alone

    def check(self, path: str, line: str) -> bool:
        """True when `line`, added to `path`, breaks the rule: here, a Python comment that holds the marker."""
        return path.endswith(".py") and MARKER in comment_of(line)
```

A file rule reads each file the change touched, whole at the head, for what one line cannot show: here, a call
wrapped over several lines.

```python
"""A call that can wait forever: requests' functions have no timeout unless one is passed."""

from __future__ import annotations

from ..rule_helpers import ChangedFile, FileRule, argument, calls_in

#: The calls that wait on the network until a timeout says stop, as the code writes them.
CALLS = ("requests.get", "requests.post", "requests.put", "requests.patch", "requests.delete", "requests.request")


class HttpTimeout(FileRule):
    """A request with no timeout holds a worker for as long as the other side keeps the connection open."""

    id = "http-timeout"
    severity = "major"
    title = "a network call with no timeout"
    fix = "Pass timeout=, in seconds, to every call."
    bad = ("r = requests.get(url)\n", "r = requests.post(\n    url,\n    json=payload,\n)\n")
    good = ("r = requests.get(url, timeout=5)\n", "r = session.get(url)\n")

    def check(self, file: ChangedFile) -> list[int]:
        """Each line where a call the change added starts and passes no timeout: the report quotes that line."""
        return [
            call.lineno
            for call in calls_in(file.module, *CALLS)
            if call.lineno in file.added and argument(call, "timeout") is None
        ]
```

A tree rule reads the head, every path and each Python module, for what must exist; `demo_required_paths.py` is the whole
of one:

```python
"""The payments project's own shape: the files and folders every branch must keep."""

from __future__ import annotations

from ..rule_helpers import Tree, TreeRule

#: What must exist at the head: a file by its path, a folder by its path ending in `/`.
REQUIRED = ("README.md", "src/payments/__init__.py", "tests/")


class RequiredPaths(TreeRule):
    """A branch may add what it likes; what it must not do is lose the project's shape: its README, its package and
    its tests. The list is the rule's own: a project edits it, as it would the words of `demo-money-in-cents`."""

    id = "demo-required-paths"
    severity = "major"
    title = "a file or folder the project needs is missing"
    fix = "Restore it, or change the list in this rule if the shape has changed on purpose."
    bad = ({"README.md": ""},)
    good = ({"README.md": "", "src/payments/__init__.py": "", "tests/test_charge.py": ""},)

    def check(self, tree: Tree) -> list[tuple[str, str]]:
        return [(path, "not at the head") for path in REQUIRED if not tree.has(path)]
```

1. Copy a template to `judge/rules/<name>.py` and name the rule after the file: its `id` is the file's name with `-`
   for `_`.
2. Write `check`, picking the smallest thing that answers the question — a line, a file, the tree — and return what
   its kind takes:

   | Kind | `check` reads | and returns |
   |---|---|---|
   | `Rule` | `path`, `line` | `True` where the line breaks the rule |
   | `FileRule` | `file.path`, `file.lines`, `file.line(n)`, `file.near(n)`, `file.added`, `file.changed`, `file.module`, and `file.diagnostics` for a rule that names a `tool` | line numbers, the report quoting each line; `(line, what is wrong)` pairs, `None` for the file as a whole; or `Hit`s |
   | `TreeRule` | `tree.paths`, `tree.has(path)`, a folder as `"tests/"`, `tree.module(path)`, `tree.modules()` | `(path, what is wrong)` pairs, `(path, line, what)` triples, or `Hit`s |

   `file.added` holds the numbers of the lines the change added, and `file.changed` every path the change touched,
   for a rule about the change as a whole: a manifest changed and its lock file not. A file past the size limits is
   not read whole, and a rule that asks for its `lines` could not look, a hole; `file.near(n)` gives line `n` of any
   file, as far as its diff shows it, or None, for a rule that reads only what a change added and the lines around
   it, as `reviewer-instructions` does. `file.module` and
   `tree.module(path)` are the file as Python reads it, or a sentence saying why there is none. A `Hit` names its own
   title, severity or fix, where one rule covers several problems, as `lint` does for ruff's codes. Anything else
   returned is a sentence naming what the kind takes, and a rule that raises is named with the line of its own file
   that raised.
3. Give it examples: `bad`, each of which it must flag, and `good`, each of which it must leave alone — a text, read
   at `example.py`; a `(path, text)` pair, for a rule that chooses its files; or `{path: text}`, several files at
   once. At every start the catalog holds every rule to its own examples, and a line or a file rule to firing on
   nothing in its own source, its examples aside: a rule that fails either is a sentence and exit 2, never a rule
   that quietly finds nothing.
4. See it fire, with no repository, no model and no key:
   `uv run python -m adk_review.review --try src/ --check rule.no-todo` reads files on disk, each as wholly
   added — a file at the path given, a folder's files at their paths inside it — and `--explain rule.no-todo` prints
   the rule whole, its examples among it. Then try it on a branch:
   `uv run python -m adk_review.review <repo> --base main --check rule.no-todo`.
5. Test what the examples do not say, in `tests/`, with the door's `try_rule(rule, {path: text})`,
   `lines_hit(rule, path, text)` and `self_check(rule)`: each reads the rule as a review does.
6. Turn it on: add `rule.no-todo` to `rules-only` and `full`, which a test holds to every rule not named `demo-`;
   `demo` runs it through the group. To run it in every review that names no profile, add it to `default` too, and
   to `default-rules`, which a test keeps to the default's rules.

The door passes on the contract — `Rule`, `FileRule`, `TreeRule`, `ChangedFile`, `Diagnostic`, `Tree`, `Hit`,
`UnreadError` and the severity scale, `Severity` and `RANK` — and the helpers: each in a module named after what it
reads (`lines.py`, `syntax.py`) or tries a rule with (`testing.py`), or passed on from `core/` where a review reads it
too — `matches` and `matching` from `paths.py`, `is_test` from `blast.py`, `is_python` from `rules.py`, and a
credential's shape from `secrets.py`:

| Helper | What it answers |
|---|---|
| `is_test(path)`, `is_python(path, first_line)` | Whether a path is a test; whether Python reads it as source |
| `matches(path, *globs)`, `matching(paths, *globs)` | Whether a `.gitignore`-style glob names a path; the paths the globs name, sorted |
| `calls(line, *names)` | Whether the line calls a named function, `*` for anything: never a definition, a comment or a string |
| `comment_of(line)`, `words(line)`, `tokens(line)` | The comment a line ends with; its names, numbers and operators; its tokens |
| `shape_of(text)`, `vendor_shape(text)`, `named_at(text)`, `named_values(text)`, `SECRET_WORDS`, `is_placeholder(value)`, `without_examples(text)` | The credential shape a text holds, and a vendor's alone, as `secrets` reads it; the name a generic one is given, whole on a `.env` line and from its secret word on in code, its value and whether the value is quoted, the first or each in turn; the words a secret goes by, as a pattern; whether a value only stands for a secret, `password` or `****`; the text with each example credential blanked, a URL's to `example.com`, or to `localhost` with a placeholder password, never a vendor's key, and a private key's header before an ellipsis, so a rule weighs what is left |
| `calls_in(code, *names)`, `argument(call, name, position)` | Every call to a named function in a module, in written order; what a call passes for one parameter |
| `functions_in(code)`, `imports_in(code)`, `nodes_in(code, *kinds)`, `name_of(node)` | Every function; every imported name with its line; every node of a kind; a name as written, dotted |
| `defined(module)`, `classes(module)`, `parameters(module, function)` | What a module defines; its classes and their parents; a function's parameters |
| `try_rule`, `lines_hit`, `self_check`, `self_test` | A rule tried on files, on a text, on its own source, on its own examples |

Three limits stay. A line helper reads one line alone, so a line inside a docstring reads as code; a rule that must
tell them apart reads the file. Names are read as written: after `from requests import get`, `get(url)` is not
`requests.get`. A Python file that does not parse gives the syntax readers nothing, and lint names it.

What a rule may do, and may not:

- It reads what it is given and runs nothing — no process, no network, no environment, no file but the ones handed
  to it — and imports the door, `judge/rule_helpers/`, alone; `tests/test_layering.py` keeps it so.
- A rule that needs an outside tool's output names the tool and is handed it: `lint` names `ruff`, with its own codes
  as its `tool_settings`, and `collect/ruff.py` runs ruff for it. Another tool is one adapter more in
  `collect/tools.py`, and the contract stays as it is.
- It never reads the repository under review's configuration: a branch must not be able to switch off the rule that
  judges it.
- It reads no test unless it says so: a rule about the product's code is handed no file `is_test` names, so a
  test's fakes are no findings, while `file.changed` and `tree.paths` still list every test. A rule about what runs,
  leaks or steers sets `reads_tests = True`, as `secrets` and `steering-files` do: a test runs in CI with its keys,
  and a workflow named `test_*.yml` is a test by its name. `--explain` says which a rule does.
- Its expectations are constants at the top of its file, beside the check, and its examples follow them: a project
  that edits a rule's constants edits its examples with them.
- A rule that cannot look — a file not read whole, a tree not listed — is a hole in the review, never "nothing
  found". The kinds raise `UnreadError` for you, and a rule may raise it itself, as `encoding-declaration` does for
  code no rule can read; a rule that raises anything is named in the report, exit 3.
- A rule's own examples are what it flags, so a review of a change to the rule's file may flag them too.
- A rule is for what a pattern decides; what needs judgement is a guideline.

## Write a guideline

A guideline is one Python class in one file of `judge/guidelines/`, named after the file: `naming.py` holds the
guideline `naming`. It names four things: an id, a title, the role a model takes, and a thinking budget. The rest —
the severity scale, the rule that every finding quotes the diff, the fenced change and the reminder after it, the
schema and the reading back — is every guideline's, in `judge/agents.py`, so a guideline cannot weaken it.

```python
"""Naming: whether the names a change adds say what each thing is for."""

from __future__ import annotations

from ...core.guidelines import Guideline


class Naming(Guideline):
    """One sentence on why a project asks a model this: what no pattern can decide."""

    id = "naming"  # the file's name with `-` for `_`: a profile names it `guideline.naming`
    title = "names that say what a thing is for"  # a few words, as --list-checks prints them
    role = "You judge whether the names the change adds say what each thing is for."  # opens the instruction
    thinking_budget = 2048  # tokens of thinking before the answer, on Gemini; Copilot has no such lever
```

1. Copy it to `judge/guidelines/<name>.py` and name the guideline after the file.
2. Write the role: who the model is and what it judges the change for, in a sentence or two. The instruction asks
   for findings "only in your dimension" right after it, so a role that names one concern keeps the guideline to it.
3. See it with `--list-checks` and `--explain guideline.naming`, and try it alone, with a model, with
   `--check guideline.naming`.
4. Turn it on: `full` runs it at once, through the group; add `guideline.naming` to `default` to run it in every
   review that names no profile.

Like a rule, a guideline runs nothing; it imports `core` alone. Its findings are a model's, so they are held to the
diff: one whose quote the diff does not bear out is dropped and counted, and `--verify` asks a second model about each
one no rule already made.

## Reports

The text report goes to stdout: the verdict under the title, what was reviewed, under `--all` the project's health,
the blast radius, a section for each check that found something or failed, one line for those that found nothing, and then what was dropped, refuted, folded, redacted and spent. "The report" in `GUIDE.md` lists it
line by line. Beside it:

- Under `--all`, where the files are the project, a Health section measures it by code alone: the files read whole,
  the source and the tests in files and lines, the share of the source's function lines in each McCabe complexity
  band, complexity counted as ruff's C901 counts it, so the report and the lint rule name the same number, but for a
  nested function, counted on its own — up to 10, 11 to 20, 21 to 50, over 50, the bands of the Software Improvement
  Group's maintainability model —
  the most complex functions, and each rule's findings in source and in tests apart, by severity. Maintainability is
  graded A to E, the Software Improvement Group's published rating of that complexity profile, where at least ten
  source files parsed and nine in ten of those at the head; security and correctness are counted, never graded: on
  twelve real projects a grade from the worst finding read C or worse for nearly all of them, where the profile
  told them apart, A to D. The verdict never reads any of it. With `--previous`, the grade, each share and each
  rule's count say what they were.
- `--json FILE` writes the same review as data for a pipeline: the verdict and its exit code, every finding with its
  check, its line, the lines the diff places it on and whether one of them is a line the change added, what could
  not look, what was not read, and the spend. `schema` rises when a field changes its meaning or goes, as it did to 2
  when a finding's `source` became its `check`. The file is written whenever a review is decided, so never on exit 2;
  an earlier file of that name is removed before the review, so a pipeline never reads a stale one, and the new one
  is written beside the path and moved into place, the operator's alone.
- `--handoff FILE` writes each kept finding as a short instruction for a coding agent: where, the lines quoted, what
  to change, and the fingerprint that must be gone afterwards, with each finding folded into it named under it,
  since another reading of the same lines can be a second defect. It ends in the command that checks the fixes: the
  review again on the same scope with only the rules that found something, so an agent checks its own work offline,
  no model called. The scope is the preflight's, which the cache's key holds, and it is the whole of what the rules
  read: `--skip` and `--diff-cap` bound the guidelines alone. The guidelines that found something are named apart,
  with their `--check` and no command, since checking a model's reading spends its quota and their review took flags
  the scope does not hold. A branch's name is its author's: where no quoting keeps the command one command — a line
  break, a name the report's safety rules would rewrite, or on Windows a character cmd or PowerShell still reads
  inside double quotes, PowerShell's typographic quotes among them — none is printed, only the checks to name.
  Nothing is applied.
- `--previous FILE` reads an earlier `--json` report and marks each finding new or seen before, by its fingerprint:
  its check, its file and its evidence — for a guideline's finding, the lines its quote stands on — never its line,
  so a finding keeps it when lines move. It lists what is no longer found; a review with a hole resolves nothing.
- `--cache DIR` keeps each decided review under a hash of everything its result depends on — the reviewer's own code
  and config, the commits, what is covered, the checks, every setting and each model — and serves the same review
  again with no model call. A review with a hole is never kept, so the next run looks again.
- `--debug-log FILE`, `--trace FILE` and `--record DIR` keep what a run did: every model request and answer, secrets
  redacted; ADK's spans, in a local SQLite file; and each model answer under a hash of its request, which a fake
  replays offline.

## In a pipeline

The command is built for a pipeline: without a terminal it asks nothing, its exit code is the verdict, and `--json`
leaves the review as data for the steps after it. A pull request's checkout needs four things, each tried on a
simulated one:

- the whole history, since a change against a base is refused in a shallow clone, exit 2;
- the head as a commit, `--head <sha>`, since the checkout is detached;
- the base as the remote's branch, `--base origin/main`, since no local `main` exists;
- the reviewer from its own repository at a pinned commit, never from the change under review, which must not choose
  its judge.

`--profile default-rules` needs no key, so it fits every pull request. `rules-only` adds every other rule but the
payments demo's, examples written for one project that `demo` alone runs. The
guidelines need a credential the pipeline can hold, `REVIEW_PROVIDER=gemini` with `GOOGLE_API_KEY` from its secrets:
the Copilot login is a device flow no pipeline can complete, and a pull request from a fork gets no secrets. Run the
guidelines on a fork's change only after a maintainer has read it, in a job that holds the provider's key and nothing
else, and never under `pull_request_target`, which hands the base repository's secrets to a run over code the fork
chose. The command runs from the reviewer's own folder, as below: from inside the checkout under review, `python -m`
imports that folder's modules before its own, so run it there with `python -P`, or `PYTHONSAFEPATH=1`.

A GitHub Actions workflow in the repository to review:

```yaml
name: review
on: pull_request
permissions:
  contents: read
jobs:
  review:
    runs-on: ubuntu-latest
    timeout-minutes: 20 # a run past it is stopped, never left to hold a runner
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          fetch-depth: 0 # the whole history: a shallow clone is refused
          path: subject # the repository under review
          persist-credentials: false # the job's token stays out of the reviewed repository's config
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          repository: emrecdr/adk-review
          ref: <a commit of the reviewer> # pinned: the change under review never chooses it
          path: reviewer
          persist-credentials: false
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          version: "0.12.23" # pinned, as the reviewer's own lock file is
      - name: Review
        shell: bash # -o pipefail, so the review's exit code survives the tee
        working-directory: reviewer
        env:
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          BASE: origin/${{ github.base_ref }}
        run: >-
          uv run --locked python -m adk_review.review "$GITHUB_WORKSPACE/subject"
          --head "$HEAD_SHA" --base "$BASE" --profile default-rules
          --json "$RUNNER_TEMP/review.json" | tee "$RUNNER_TEMP/review.md"
      - name: Summary and annotations
        if: always()
        shell: bash
        run: |
          if [ -f "$RUNNER_TEMP/review.md" ]; then cat "$RUNNER_TEMP/review.md" >> "$GITHUB_STEP_SUMMARY"; fi
          if [ -f "$RUNNER_TEMP/review.json" ]; then
            jq -r '
              def data: tostring | gsub("%"; "%25") | gsub("\r"; "%0D") | gsub("\n"; "%0A");
              def prop: data | gsub(":"; "%3A") | gsub(","; "%2C");
              def level: if .severity == "minor" then "warning" else "error" end;
              def rank: {"blocker": 0, "major": 1, "minor": 2}[.severity];
              .findings | sort_by(rank) | group_by(level)[]
              | (.[:10][] | "::\(level) file=\(.file | prop),line=\(.line // 1 | prop),title=\(.check | prop)::\(.title | data)"),
                (if length > 10 then "::notice::\(length - 10) more \(.[0] | level) finding(s): the summary lists every one" else empty end)
            ' "$RUNNER_TEMP/review.json"
          fi
```

The review step fails the check on exit 1, a finding at `--fail-on`, and on exit 3, a check that could not look;
`continue-on-error: true` reports without blocking. The second step runs either way: the report goes to the run's
summary, and each finding becomes an annotation on its line, escaped as GitHub's own toolkit escapes one, blockers
first. GitHub shows ten annotations of each level a step and drops the rest unsaid, so past ten a notice counts them,
and the summary lists every one. Exit 2 is a usage error before anything ran, so it leaves no report and no JSON.

On Azure Pipelines the same holds, for a pull request under a branch policy: check the repository out with
`fetchDepth: 0`, since new pipelines may fetch one commit deep, and the reviewer at a pinned commit beside it; pass
`--head "$(System.PullRequest.SourceCommitId)"` and
`--base "refs/remotes/origin/$(System.PullRequest.targetBranchName)"`, never
`origin/$(System.PullRequest.TargetBranch)`, which on Azure Repos holds `refs/heads/main` and names no branch; run
the command under `set -o pipefail` where it pipes into `tee`, since a script step's bash does not set it; and attach
the report with `##vso[task.uploadsummary]<path of review.md>`. On GitLab CI, in a merge request pipeline: set
`GIT_DEPTH: "0"`, since new projects clone 20 commits deep and a shallow clone is refused; clone the reviewer at a
pinned commit; pass `--head "$CI_COMMIT_SHA"` and `--base "$CI_MERGE_REQUEST_DIFF_BASE_SHA"`, the merge request's
own diff base; and keep `review.md` and `review.json` as artifacts `when: always`. Each command was tried on a
simulated checkout, the base as a full remote ref and as a commit; neither service was, so no file is given for them.

To review each push before it leaves a developer's machine, see "Before a push" in `GUIDE.md`.

## SonarQube in the IDE

SonarQube is optional and runs on your own machine: a second reader beside the gate, never part of it. CI holds the code
to ruff, pyrefly and the suite, and nothing here changes what a review does. The checkout carries what it needs:
`docker-compose.yml` runs a SonarQube server on the H2 database it embeds, no database server beside it, and the scanner
under the `scanner` profile, each image pinned by its digest as the workflows pin an action by its commit;
`sonar-project.properties` names the project, `adk-review`, its sources, the package and the scripts, and its tests; and
`.vscode/` recommends the SonarQube for IDE extension (SonarLint, as it was called) beside Ruff and Pyrefly, the
editor's lint and type check the same as CI's, and binds this folder to that project on the local server.

1. **Start the server**, with Docker running: `docker compose up -d` starts SonarQube alone and leaves the scanner out.
   The first start takes a while; the server is ready when `http://localhost:9000` shows its login page. It listens on
   this machine alone. Sign in as `admin`, password `admin`, and SonarQube asks for a new password at once.
2. **Make a token** under *My Account → Security*: a user token, since the extension takes only a user's, and the same
   token runs the scanner. SonarQube shows it once. Keep it in your shell or a password manager, never in a file the
   repository holds; `.env` is the reviewer's, and the scanner never reads it.
3. **Run the first analysis**, which creates the project on the server: the extension binds only to a project that
   exists.

   ```bash
   export SONAR_TOKEN=<your token>                   # PowerShell: $env:SONAR_TOKEN = "<your token>"
   docker compose --profile scanner run --rm sonar-scanner
   ```

   The compose file hands the shell's `SONAR_TOKEN` to the scanner, which reads `sonar-project.properties` from the
   checkout mounted at `/usr/src` and reaches the server by its name inside the compose network. The scanner is
   published for amd64 alone, so an arm64 machine, an Apple silicon Mac among them, runs it emulated and slower. The
   result is the project's page on the server, `http://localhost:9000/dashboard?id=adk-review`; run the scanner again to
   see it after a change.
4. **Connect the IDE.** Open this folder in VS Code and install the extensions it recommends. The SonarQube extension
   lists the connection `local-sonarqube`, which `.vscode/settings.json` declares at `http://localhost:9000`; edit it
   and give it the token, which the extension keeps in VS Code's secret storage rather than in the settings file. The
   folder is already bound to `adk-review`, so each open Python file is read as you type under the server's quality
   profile, a rule turned off on the server turned off in the editor too, and what it finds is in the Problems panel.
   In another editor the same extension, for JetBrains IDEs or Visual Studio, asks for the same three things by hand:
   the server `http://localhost:9000`, the token, and the project key `adk-review`.

`docker compose stop` stops the server and keeps its data; `docker compose down -v` removes the server and every
analysis with it. H2 is the database SonarQube keeps for development and trials, which this is: should the server not
start after the image moves to a newer SonarQube, `docker compose down -v` and a new scan rebuild it all from the
checkout. The scanner writes `.scannerwork/` into the checkout, which git ignores.

## Claude Code and Copilot

Both work on this repository by the same rules. `CLAUDE.md` is the long form, which Claude Code loads and Copilot in VS
Code and Copilot's cloud agent read as well; `.github/copilot-instructions.md` is its short form, for Copilot wherever
it runs, and `.github/instructions/` holds the rules that load with the files they name: the package, the tests, the
docs. The recurring tasks are skills in `.github/skills/`, each a slash command in VS Code's chat — `/new-rule`,
`/new-guideline`, `/review-a-branch` and `/update-docs` — which `CLAUDE.md` points Claude Code at too, and
`.github/agents/reviewer-author.agent.md` is a custom agent that implements a change the way the tests demand. On
GitHub, Copilot's cloud agent starts from `.github/workflows/copilot-setup-steps.yml`, the project installed from its
lock, so it runs the lint, the type check and the suite as a person would.

In VS Code, `.vscode/` recommends Copilot Chat beside Ruff, Pyrefly and SonarQube for IDE, and lets an agent run the
offline gate without asking each time: `uv run ruff check`, `ruff format --check`, `pyrefly check` and `pytest`, and
`make lint`, `typecheck`, `test` and `ci`. Any other command asks first, and one that names `.env` always does, since
neither assistant may read it. Nothing here calls a model by itself; a review whose profile names a guideline does.

## Design notes

- **The grouping is the architecture.** What each file may know is what a test says. `core/` can be read, and
  tested, without ADK installed, and a test does that too, because the syntax tree cannot see what an import loads:
  with an eager `from . import agent` in the package root, importing `core/` measured 187 ADK modules loaded. The
  root answers `agent` on first use instead (PEP 562), and ADK's loader still finds it.
- **Every check has one home.** A check is a file in its kind's folder, written the one way its kind is written, and
  the checks shipped with the reviewer are no exception: ESLint writes its own rules with the API a plugin uses, and
  pylint loads its own checkers through the hook a plugin's goes through. A check named after its file is held to it
  by the catalog and by a test, as ESLint's build holds each rule to its doc and its test. A rule held in a config
  file — a pattern and a list of paths — is a second language to learn; a class that answers yes or no is the Python
  a reader already knows, and the one choice left to the command line is which list to run.
- **Nothing of the review comes from the repository under review.** A branch that could choose its own profile could
  switch its own secrets rule off, and the reviewer would depend on what it reviews. So no rule, profile, setting or
  ruff configuration is read from it: ruff runs `--isolated`, with the rule's own codes, and `--ignore-noqa`, so no
  comment in the branch silences it. No review is filtered by the repository's `.gitignore` either: git keeps an
  ignored file out of every commit, so it never reaches a review, and a filter by it would skip only what was
  committed anyway, a force-added `.env` holding a key. Under `--staged`, git's own ignore rules decide only which
  untracked files the report names.
- **Git is read, never trusted.** Every call is a read-only verb, an argv list with a timeout, run by git's full path
  from `PATH`'s absolute entries, never from the checkout under review. Its environment drops every inherited `GIT_*`
  variable and reads no global or system config; attributes come from the merge base, never from the branch; no
  replace ref or graft is followed and nothing is fetched; and `core.fsmonitor` is off, so no program the
  repository's own config names ever starts. Measured, each before it was fixed: `GIT_DIR` reviewed another
  repository, a replace ref swapped the key's file for a harmless one, and the branch's own `-diff` attribute hid a
  planted key.
- **Secrets are found by code.** Every model call is redacted, so no guideline ever sees a key: measured on Copilot
  with the redaction on, no guideline reported the planted one. A key must be found by code, from the diff itself,
  before any model runs: `secrets` reads the added lines of each file's whole diff, past any cap, and
  `named-secrets` weighs a value given a name that says secret. No cut falls inside a credential, since half of one
  matches no shape and would be sent unredacted.
- **The rules run as one node.** Every rule runs the same way, through `run_rule`, in one `FunctionNode`, ADK 2.x's
  node for a deterministic step, before the guidelines: in the command's graph over the evidence it gathered, and in
  chat's, which gathers the same evidence for the commits its intake confirmed. Each rule's answer is left in state
  under its check's key, as each guideline's review is, and the node writes the blast radius the guidelines read.
  It never raises: measured, a node that raises ends the whole run, every guideline with it. Once the rules have read
  them, it lets go of the tree and each file's text, diff and added lines, and the lines shown of one not read whole,
  before the model calls. A profile with no
  guideline runs the same `run_rules` straight from the driver, the engine never imported: measured by the installed
  command, the demo's rules-only review took 0.87 to 0.94 s through a graph holding no agent, 0.41 s of it the
  engine's import, and 0.16 to 0.17 s without it, the pre-push hook paying the difference on every push; `--trace`,
  `--debug-log` and `--record` are the engine's own and still bring its run.
- **Lint reads a copy, never the checkout.** The changed Python files, as the collector read them at the head, are
  written into a temporary folder, each in a folder of its own under a name of the rule's own — `module.py`,
  `module.pyi` for a stub, or `__init__.py` where the branch's is one — and named to ruff one by one: a `.venv/` the
  branch changed is linted, `x.py` beside `X.py/y.py` collides on no disk, and a `nul.py` or `a:b.py`, which Windows
  cannot hold, is linted like any other. Ruff lints what the collector read and no more: a file not read whole is
  lint's hole. What a code means, its severity and its fix, is the rule's: an `assert` is major in library code,
  where `python -O` strips it, and minor in a test, where it is how pytest checks. Without ruff, or with one that
  fails, the rule did not look and says so, exit 3.
- **Grounding.** A guideline's finding must quote, verbatim, whole lines of its own file's diff, within one hunk, as
  the guideline saw them: secrets redacted, diff markers gone, whitespace not counted. Where a quote's line breaks
  were lost, its lines begin and end where the file's do, a diff marker or a fence run into them or not: measured live
  on Gemini, a guideline quoted each run of lines as one, its `-` markers and its fence left in, and every finding it
  made dropped, the review a hole. A redaction's placeholder
  stands for any other, its label the scrubber's and never the code's: measured live on Gemini, a guideline quoted
  the planted key's lines with the label renamed, and the finding dropped. Each hunk is read as git prints it, as the
  new file reads and as the old file read. A finding's line is read off the diff, never taken from
  the model: measured on Copilot, the guidelines named a wrong line for 4 of 5 findings. A finding at the bar whose
  quote the diff does not bear out is a hole, since a real blocker dropped would approve. A quote carrying the framing
  its file was shown in — the heading, the fence, git's header lines and each hunk's `@@`, git's
  `\ No newline at end of file`, the cap's cut — grounds as the lines inside it: measured live on Gemini, the
  injection on the labelled set's `sql-from-input` came quoted inside its fence, and the blocker was dropped, the
  review a hole; with the framing looked past, two runs more, three of five blocker quotes carried a fence or git's
  header and both runs kept the blocker. The collector records each file's framing beside its lines as it renders
  them, so only that file's own is looked past: measured by review with a list of such lines kept by hand instead, a
  file with no final newline and a file cut at the cap, each quoted whole, were dropped. Measured live with the
  framing recorded, on both arms, no quote was dropped: Gemini quoted `sql-from-input`'s blocker under git's whole
  header, down to the hunk's `@@` and the function git named there, and four quotes on the planted branch inside
  fences, and each was kept. A kept finding's evidence is
  then the lines its quote stands on, as the file reads them: the report, the JSON, the hand-off, the verifier and the
  fingerprint read the diff's own text, never a model's framing of it; measured by review, the same two lines quoted
  plain and inside their fence had two fingerprints, so `--previous` named one finding resolved and new at once. What
  was measured not to help: told to quote whole lines and, for a missing test, the lines that lack one, Copilot's
  guidelines quoted whole files under their `@@` headers, and 14 of 27 findings were dropped where 5 of 21 had been;
  that sentence was taken out.
- **One finding per span of lines.** Rules and guidelines can all find one line; the report carries it once: the most
  severe reading first, whole, the first check on a tie — the rules, then the guidelines, each in the order the
  profile names them, which is the report's order too — and any finding one kept already holds folds into it. A
  folded finding is still named, under its own check: "folded into `rule.secrets`'s finding on the same lines: lint:
  S105 …". Measured before: compared only with the first finding it overlapped, a guideline's blocker quoting a whole
  function replaced ruff's line 27 and left S602 on line 28 beside it; and with every ruff hit folded into a
  guideline's blocker, lint's section read "no findings" under the rule that had found `shell=True`.
- **Retries live in the clients.** They are set once on the request, which both arms read, as the deadline is: genai
  retries the status codes it deems transient, with backoff, and ADK's LiteLLM wrapper forwards the same field as
  `num_retries`. Gemini asks for three attempts; Copilot asks for one, because the OpenAI SDK under LiteLLM retries
  twice on its own: asked for three, the two made seven. ADK's node `retry_config` would re-run a whole guideline,
  and with the error plugin turning errors into answers it would never see one. A quota window outlasts any retry:
  `REVIEW_FALLBACK_MODEL` puts a second model behind every role's first, through ADK's `FallbackModel`, each model
  tried once on a 429 or a 5xx, since Gemini's free tier counts its quota per model and Copilot's rate limits differ
  by model. Seen live on Gemini's free tier: five
  requests a minute per model, and `--verify` made ten, so the last verifiers met a 429 asking for 39 seconds, and
  their findings were kept and named with it; and twenty a day, so a key on that tier reviews about six changes a
  day under `default`, and a live script's sixteen reviews need a paid tier or `--pause` over several days.
- **A ceiling reserves first.** The guidelines call at once, so a ceiling that counted only tokens already spent would
  let them all through and be passed by all of them. Each call reserves its cost before it is sent — its request's
  characters over four, room for an answer and the thinking its budget allows — and the call that would pass the ceiling
  is refused, through the same plugin hook the redaction uses. The refused guideline is named, everything found is
  reported, exit 3: the ceiling ends the spending, never the report.
- **The second opinion never removes silently.** `--verify` is the one place a model can remove a finding, so the
  reason is in the report, and a verifier that fails leaves the finding in place, named with why. It runs before the
  fold, on the guidelines' findings alone: a rule's findings are facts and are never asked about, nor is a
  guideline's finding that only says again what a rule found, no more severely, which the fold folds into the rule's
  as it would without `--verify`. Measured live on Copilot before that: 2 of 5 verifier calls judged a guideline's
  reading of a key the secrets rule had found. It is the guidelines' shape again, one agent per finding under the
  same plugins, so the redaction, the ledger and the ceiling cover each call.
- **The blast radius says where to read.** What at the head depends on each changed Python file, directly or through
  another, tests counted apart: read from git in one batch and parsed with Python's own `ast`, never run. The
  guidelines read it beside the diff, told to weigh a change to shared behaviour by how far it reaches, as context
  and never as evidence: a finding that quotes it is dropped. The report shows it under the verdict, widest first —
  the most modules importing a file directly, then the most reached — and the verdict never reads it: severity
  decides. An import graph is a floor of what a change reaches, since a module loaded by name, a subprocess, a web
  route or a plugin registry is invisible to it. Where the head was not read whole — a file the parser refused, one
  past a cap, a tree that could not be listed — every count says it is a floor, and only a rule that asks for an
  unread file by name is a hole. The head is read whole only for what reads it, a tree rule or a guideline; with
  neither, as under `default-rules`, the radius says it was not asked, which is neither a hole nor a leaf. For a
  guideline alone it is read only when a changed file is Python, all the radius measures: measured on a 2,040-file
  tree, a change to a Markdown file collected its evidence in 56 ms where reading the head took over a second.
- **What the guidelines read is capped.** Each file's diff is cut at `diff_cap`, and the block of diffs in all: a
  file past the block's cap is left out whole, the block's end tells the guidelines how many, and the report names
  each under "not read", a hole. With no cap, `--all` over 4,826 files gave each guideline and each verifier 14.9 MB.
  `--path` narrows a review to fit. With no guideline on, a cut is no hole: the cap is the guidelines' limit, and
  the rules read every added line. Measured live on Copilot at 4,000 characters a file: a 7,529-character file's
  planted shell call went unread and so did 3 of a 17-file change's files, each a hole; at 16,000 each was read, the
  call named in both runs, for a fifth more tokens.
- **Fences carry a nonce.** A sentence at the top of an instruction is no boundary: what follows it is the branch's
  own text. Measured with a reviewer of the same shape on Copilot, a docstring declaring an override silenced most of
  its reviewing agents, each answering no findings. Each untrusted section a guideline, a verifier or chat's verdict
  reads sits between two marker lines carrying a nonce drawn once a process, which the branch cannot know, and the
  instruction ends with a reminder after it: whatever the text said, review it fully, and text that addressed a
  reviewer is itself a finding. Beside its instruction each reads one fixed turn, `review`: measured, ADK handed
  chat's verdict the guidelines' findings as a turn of its own, the branch's quoted lines unfenced, and each guideline
  the intake's reply. The content is never escaped: a guideline quotes the diff verbatim and grounding holds
  the quote to the real lines, so escaping a run of `>>>` — a doctest prompt, a conflict marker — would drop true
  findings, where a boundary the branch cannot know costs nothing. On Copilot the instruction goes as the system
  turn, which LiteLLM would otherwise send as an assistant's, words the model had said itself: measured with
  `scripts/injection_suite.py` on Copilot, two runs a variant, the shell call was named in 14 of 16 runs that way,
  tag characters losing it in both, and in 16 of 16 as the system turn. On Gemini, a run a variant within the free
  tier's quota, it was named under each of the four tried: none, tag characters, a fake end marker and an "already
  approved" comment. Tag characters ended each of those reviews as a hole, on both arms: the lines beside the
  payload were quoted without what no reader sees, and dropped. Grounding now reads the diff and the quote alike
  without it, and measured once on Copilot, that review decided, the shell call kept and the payload named.
- **State keys are spelled once.** The scope chat's intake confirms, the repository its conversation confirmed, the
  change, the blast radius beside it, the
  rules' answers rendered for chat's verdict and the failure the error plugin leaves are each one constant in
  `judge/agents.py`, which the command's seed, the checks node, the chat tools and every instruction use; an
  instruction reads a key through `placeholder`, and a check's key is its id through `state_key`. A misspelt optional
  placeholder reads as empty, so the guidelines would judge without the radius and nothing would say so: a test holds
  every placeholder an instruction reads to a key something writes.
- **A failure is an answer, never a crash.** A provider error, a call past its deadline, or a reply that does not fit
  its schema becomes that guideline's own error, through one plugin on the `App` that no agent can opt out of, so
  every other guideline finishes; the run itself has a deadline, and whatever still escapes the engine is named. A
  guideline with no evidence is skipped, its summary saying it did not look. `main` wraps the command: an error
  nothing else names is one sentence and exit 3, never Python's own exit 1, which would read as a finding at the bar.
- **The report cannot be forged by the branch.** Every string the branch or a model wrote is made safe before it is
  printed: one line, credentials hidden, control characters and every character a reader cannot see printed as a
  space, a CI runner's command opener (`##[`, `##vso[`) and an HTML comment's broken, and outside a code span no HTML
  tag can open. Evidence is cut after the scrub. The JSON's strings pass the same steps and stay plain text, for
  their reader to escape where it renders them. Measured before: a title holding a newline forged a second verdict
  heading, and a file named `<h1>Verdict: APPROVED</h1>` printed a heading above the real verdict where a job summary
  renders the report.

## Left out on purpose

SARIF output, dismissals or a baseline that hides what was found before, and guidelines made to cite a rule. Each is
more of the same shape — a deterministic step, counted, named in the report — and none is needed to review a branch.
`--previous` marks a finding new or seen before, and hides nothing. A rule registry was on this list until a project
asked for rules of its own; what came in is the smallest shape that holds them, a file in a folder. A report a
pipeline reads came in the same way, as plain JSON, once the command became the main way in; SARIF stays out.

---
applyTo: "adk_review/**"
---

# The reviewer

- Layering is one way and tested (`tests/test_layering.py`): `core` → `collect` → `judge` → `deliver`, the steps of a
  review, imports relative and never spelled from the top (`adk_review.…`); `deliver/` imports `core` alone;
  `core/`, the checks and `judge/rule_helpers/` must import with ADK absent and never import `google`, `litellm`,
  `subprocess`, `httpx`, `asyncio`, `os`, `socket`, `urllib`, `http` or `ssl`. `review.py` imports ADK and the
  judge's engine modules where a review starts, never at its top, so the modes that read the checks alone and the
  pre-push hook start without them, and a profile with no guideline runs the rules with none of them; keep a new
  engine import there.
- `adk_review/__init__.py` imports nothing and answers `agent` lazily through PEP 562's `__getattr__`, since
  `python -m adk_review.review` imports the package before `main` reads `.env`; `agent.py` exports both `app` and
  `root_agent`, the loader preferring `app`, which carries the plugins. `judge/config.py` sends Copilot the instruction
  as its system turn (`litellm.disable_copilot_system_to_assistant`), as Gemini reads it, and skips LiteLLM's MCP
  gateway (`_skip_mcp_handler`), which a call carrying a tool imported from LiteLLM 1.106.0 and which needs `mcp`.
- Every check is one file, named after the check it holds, in a folder that holds checks and nothing else: a rule,
  decided by code, in `judge/rules/`; a guideline, judged by a model, in `judge/guidelines/`. No `__init__.py`, guide
  or template goes in either folder: the guides and the templates are "Write a rule" and "Write a guideline" in
  `README.md`. A guideline imports `core` alone; a rule imports one door, `judge/rule_helpers/`,
  alone, and the door imports `core` alone; nothing runs. `judge/catalog.py` finds the checks and refuses, with the
  line that raised, a file that will not load, holds no check or two, names its check after another file, writes its
  own `hits`, or fails its own examples. No other folder is read for checks: one place for each kind, one way.
- A rule names `id` (its file's name with `-` for `_`), `severity` (`blocker`, `major` or `minor`), `title` and `fix` as
  class constants, each sentence one command of at most 20 words (`tests/test_command.py`), its expectations as
  constants in its file, `bad` and `good` examples (a text, read at `example.py`; a `(path, text)` pair, at a path the
  shipped `scan` reads, as `tests/test_catalog.py` holds; or `{path: text}`), `reads_tests = True` where what it finds
  matters in a test too (a credential, hidden text, a workflow named `test_*.yml`; no test's file reaches any other
  rule, though `file.changed` and `tree.paths` list them all), `reads_every_kind = True` where it finds a credential (it
  then reads the project's own files `scan` leaves out, never another project's code), `cleanup = True` where its
  findings change how code reads and never what it does (the hand-off lists them as tidies), and one `check` of three
  kinds — a line, a file, the tree: a `Rule` with `check(path, line) -> bool` over each added line; a `FileRule` with
  `check(file)` returning line numbers, which the report quotes, or `(line, what is wrong)` pairs over each changed file
  whole at the head (`file.lines`, `file.added`, `file.changed`, `file.module`, `file.arrived(*globs)` where a file
  renamed or copied into a rule's paths brings every line of it there, `file.new_lines(*globs)` the lines a rule over
  those paths reads, `file.line(n)`, `file.near(n)` for a file past the size limits, as far as its diff shows it, and
  `file.diagnostics`, an outside tool's, for a rule that names its `tool`, whose adapter is in `collect/tools.py`); a
  `TreeRule` with `check(tree)` returning `(path, what)` or `(path, line, what)` over the head's paths and parsed
  modules (`tree.paths`, `tree.has`, `tree.module`, `tree.modules`, `tree.added` the lines the change added by file,
  `tree.imports(path)` what each import binds, `tree.removed` the paths the change moved or removed,
  `tree.imports_before(path)` what each import bound while they were there, `tree.diagnostics` an outside tool's by path
  for a tree rule that names its `tool`), and `needs_head(files)` False where the changed files alone show it has
  nothing to read at the head, where it then answers nothing. Any other shape is a sentence. The helpers come through
  the door: `is_test`, `matches`, `calls`, `comment_of`, `words`, `tokens`, `calls_in`, `argument`, `functions_in`,
  `imports_in`, `defined`, `classes`, `parameters` and the rest. A hit may be a `Hit` naming its own title, severity or
  fix, as `lint` does for ruff's codes. `secrets` and `lint` are written like any other rule; redaction is not a check,
  and `core/secrets.py` keeps the shapes it scrubs with. A file's lines are numbered as git numbers them
  (`core/text.py`'s `lines_of`), and a changed file the collector did not read whole is the hole of the file rule that
  asks for its text — its lines, its module, ruff's diagnostics — and of no other, but a symbolic link, a hole for each
  line rule too until its file is read as text, since its lines are that file's, unless it names no file, a binary file
  or a Git LFS pointer, which the report names not read as that file is, or `scan` leaves it out, when the credential
  rules read its own path; what that rule found on the files it read stands beside the hole. No rule reads the head for
  itself. A rule's tests use `try_rule`, `lines_hit` and `self_check` from `judge/rule_helpers/`.
- A guideline names `id`, `title`, `role` (the instruction's opening: who the model is and what it judges) and
  `thinking_budget`, and `cleanup = True` where its findings change how code reads and never what it does, as
  `complexity`'s do (the hand-off lists them as tidies); optionally `models = Models(copilot=..., gemini=...)`, a model
  of its own per arm; `judge/agents.py` builds every guideline's agent the same way around its role.
- Each check has one id, `rule.<name>` or `guideline.<name>` (`core/profiles.py`), in `config.toml`, on the command
  line (`--check ID`, `--list-checks`) and as its own report section; inside ADK, where names must be identifiers, it
  is `state_key(id)`, `rule_<name>`. Never spell a check's id or key by hand: a check's own `check_id`, built by
  `rule_id` or `guideline_id`, and `state_key`.
- `config.toml` beside `review.py` is the one config file, nothing naming another: `[review]` the flags' defaults,
  `[profiles]` named lists of check ids or the groups `rules` and `guidelines`. It is read whole, so a misspelt key is
  exit 2. No setting of the review is read from the repository under review, and no review is filtered by its
  `.gitignore`. `default` holds the basics and a check runs only where a profile names it: a new rule joins `demo`
  through the group, `rules-only` and `full` when its id is added to both, which a test holds to every rule whose id
  does not start with `demo-`, and `default`, `default-rules` and `code-review` when its id is added to all three, since
  tests keep `default-rules` to the default's rules and `code-review` opening with them; a file with no `default` is
  refused, exit 2.
- `main` wraps the command: an error nothing else names is a sentence and exit 3, never Python's own exit 1, which
  would read as a blocker.
- `--dry-run`, `--list-checks`, `--try` and `--explain` (`MODES` in `review.py`) each stop before a review would
  start: they exclude one another, and each refuses a review's own flags (`REVIEW_ONLY`: `--json`, `--handoff`,
  `--comment`, `--cache`, `--previous`, `--record`, `--trace`, `--debug-log`) with exit 2. A new flag only a review
  reads joins `REVIEW_ONLY`.
- `--staged` reads the index through plumbing alone (`diff-index --cached`, `ls-files -s`, `cat-file`), so nothing is
  written, no index refreshed and no lock taken. An untracked file is named, never reviewed and never a hole; a file
  changed but not staged is not listed, since `diff-files` ran a clean filter the repository's own config named.
- The blast radius (`core/blast.py`) is parsed once from the head commit and shared with the tree rules; the guidelines
  see it, and beside it each call into a function the change touched from a line it did not add (`callers_of`, fenced,
  capped at `CALLERS_CAP_CHARS`), the report shows it under the verdict, the verdict never reads it. Where the head was
  not read whole, every count the report prints is a floor and says so; the tree is dropped from the evidence before the
  model calls. The head is read whole only when a tree rule needs it for the change (`needs_head`) or a guideline runs;
  otherwise the radius says "not asked", neither a hole nor a leaf.
- Every untrusted section a guideline, a verifier or the chat's verdict reads goes through `fenced(label, inside)` in
  `judge/agents.py`: two marker lines carrying `BOUNDARY`, a nonce drawn once a process, or `JUDGED_BOUNDARY`, a second
  no guideline is shown, where a guideline's own words are read, and the instruction ends with a reminder, `REMINDER`
  for the guidelines and the verifiers. Never escape or rewrite the content itself: a guideline quotes the diff
  verbatim, and grounding holds the quote to the real lines.
- A state key more than one module uses is a constant in `judge/agents.py` (`SCOPE`, `REPOSITORY`, `DIFF`, `BLAST`,
  `QUOTABLE`, `FINDINGS`, `FAILURE`), and an instruction reads a key through `placeholder(key)`; `tests/test_engine.py`
  holds every placeholder an instruction reads to a key something writes.
- Every rule runs the same way, through `run_rule`, in one `FunctionNode` (`judge/checks.py`) before the guidelines, in
  both shapes, or straight from the driver through the same `run_rules` where no guideline runs; each rule's answer, a
  `Checked`, is left in state under its check's key. The node also writes the blast radius the guidelines read; in chat
  it keeps each file as grounding reads it under `QUOTABLE`, for the grounding node that holds the guidelines' findings
  to the diff and decides the verdict before the verdict agent writes it up under `FINDINGS`, and gathers the evidence
  for the commits the intake confirmed, never their names again; and once the rules have read them it lets go of the
  tree, the texts, the diffs and the added lines. The node never raises: a node that raises ends the whole run, every
  guideline with it.
- `--json FILE` writes the review as data (`as_data` in `deliver/report.py`): every string through the text report's
  own safety steps, `one_line` then `inert`, evidence cut where the report cuts it. Raise `SCHEMA` when a field
  changes its meaning or goes; add a field freely. A finding's `fingerprint` is built from its check, file and evidence,
  never its line — a guideline's evidence is the lines its quote stands on, which grounding sets — and `--previous FILE`
  compares by it; a review with a hole resolves nothing.
- `GUIDE.md` is held to the code by `tests/test_docs.py`: a new flag, check, JSON field or limit
  fails the suite until the guide says it. Regenerate its check table from `--list-checks`.
- The plugins are `StopAtRateLimit`, which after a 429 no retry or fallback got past answers each later call of the
  review `RATE_LIMITED`, unsent, then `RedactSecretsPlugin`, then the optional `DebugLoggingPlugin` (`--debug-log`,
  never the session's state) and `Recorder` (`--record`), then `UsageLedger`, `ReportProviderErrors` and `Progress`,
  which times each agent and, at a terminal or with `--progress`, tells each guideline's start and finish on stderr, the
  command telling every `HEARTBEAT_SECONDS` which it still waits on, and `--quiet` silencing both; the first plugin to
  answer short-circuits the rest. Chat puts `RefuseUnready` before it and `OneTurn` after it: each judging agent reads
  its instruction and one fixed turn, never a turn ADK would make of another agent's output.
- In chat the scope is a `temp:` key, which ADK keeps for one turn, and a gate after the intake goes on only for a
  scope that turn confirmed; the intake is told the repository its conversation confirmed (`{review_repo?}`).
- Under `--all` the report adds a Health section, `core/health.py`: what was read, the source and the tests apart,
  McCabe complexity as ruff's C901 counts it (a nested function on its own) in the SIG bands, maintainability graded
  A to E by SIG's published risk-profile rating only where ten or more source files parsed and nine in ten of those
  at the head, each rule's findings counted by severity and never graded. The verdict never reads any of it.

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
- Every check is one file, named after the check it holds, in a folder that holds checks and nothing else: a rule,
  decided by code, in `judge/rules/`; a guideline, judged by a model, in `judge/guidelines/`. No `__init__.py`, guide
  or template goes in either folder: the guides and the templates are "Write a rule" and "Write a guideline" in
  `README.md`. A guideline imports `core` alone; a rule imports one door, `judge/rule_helpers/`,
  alone, and the door imports `core` alone; nothing runs. `judge/catalog.py` finds the checks and refuses, with the
  line that raised, a file that will not load, holds no check or two, names its check after another file, writes its
  own `hits`, or fails its own examples. No other folder is read for checks: one place for each kind, one way.
- A rule names `id` (its file's name with `-` for `_`), `severity` (`blocker`, `major` or `minor`), `title` and `fix`
  as class constants, its expectations as constants in its file, `bad` and `good` examples (a text, read at
  `example.py`; a `(path, text)` pair; or `{path: text}`), and one `check` of three kinds — a line, a file, the tree:
  a `Rule` with `check(path, line) -> bool` over each added line; a `FileRule` with `check(file)` returning line
  numbers, which the report quotes, or `(line, what is wrong)` pairs over each changed file whole at the head
  (`file.lines`, `file.added`, `file.changed`, `file.module`, `file.line(n)`, `file.near(n)` for a file past the size
  limits, as far as its diff shows it, and `file.diagnostics`, an outside tool's,
  for a rule that names its `tool`, whose adapter is in `collect/tools.py`); a `TreeRule` with `check(tree)` returning `(path, what)` or `(path, line, what)`
  over the head's paths and parsed modules (`tree.paths`, `tree.has`, `tree.module`, `tree.modules`). Any other
  shape is a sentence. The helpers come through the door: `is_test`, `matches`, `calls`, `comment_of`, `words`,
  `tokens`, `calls_in`, `argument`, `functions_in`, `imports_in`, `defined`, `classes`, `parameters` and the rest.
  A hit may be a `Hit` naming its own title, severity or fix, as `lint` does for ruff's codes. `secrets` and `lint`
  are written like any other rule; redaction is not a check, and `core/secrets.py` keeps the shapes it scrubs with.
  A file's lines are numbered as git numbers them (`core/text.py`'s `lines_of`), and a changed file the collector did
  not read whole is the hole of the file rule that asks for its text — its lines, its module, ruff's diagnostics —
  and of no other; what that rule found on the files it read stands beside the hole. No rule reads the head for
  itself. A rule's tests use `try_rule`, `lines_hit` and `self_check` from `judge/rule_helpers/`.
- A guideline names `id`, `title`, `role` (the instruction's opening: who the model is and what it judges) and
  `thinking_budget`; `judge/agents.py` builds every guideline's agent the same way around its role.
- Each check has one id, `rule.<name>` or `guideline.<name>` (`core/profiles.py`), in `config.toml`, on the command
  line (`--check ID`, `--list-checks`) and as its own report section; inside ADK, where names must be identifiers, it
  is `state_key(id)`, `rule_<name>`. Never spell a check's id or key by hand: a check's own `check_id`, built by
  `rule_id` or `guideline_id`, and `state_key`.
- `config.toml` beside `review.py` is the one config file, nothing naming another: `[review]` the flags' defaults, `[profiles]` named lists of check ids or the groups `rules` and
  `guidelines`. It is read whole, so a misspelt key is exit 2. No setting of the review is read from the repository
  under review, and no review is filtered by its `.gitignore`; a config inside it is refused. `default` holds the
  basics and a check runs only where a profile names it: a new rule joins `rules-only` and `full` through the group,
  and `default` and `default-rules` when its id is added to both, since a test keeps `default-rules` to the default's
  rules; a file with no `default` is refused, exit 2.
- `main` wraps the command: an error nothing else names is a sentence and exit 3, never Python's own exit 1, which
  would read as a blocker.
- `--dry-run`, `--list-checks`, `--try` and `--explain` (`MODES` in `review.py`) each stop before a review would
  start: they exclude one another, and each refuses a review's own flags (`REVIEW_ONLY`: `--json`, `--handoff`,
  `--cache`, `--previous`, `--record`, `--trace`, `--debug-log`) with exit 2. A new flag only a review reads joins
  `REVIEW_ONLY`.
- `--staged` reads the index through plumbing alone (`diff-index --cached`, `ls-files -s`, `cat-file`), so nothing is
  written, no index refreshed and no lock taken. An untracked file is named, never reviewed and never a hole; a file
  changed but not staged is not listed, since `diff-files` ran a clean filter the repository's own config named.
- The blast radius (`core/blast.py`) is parsed once from the head commit and shared with the tree rules; the
  guidelines see it, the report shows it under the verdict, the verdict never reads it. Where the head was not read
  whole, every count the report prints is a floor and says so; the tree is dropped from the evidence before the model
  calls. The head is read whole only when a tree rule or a guideline runs; otherwise the radius says "not asked",
  neither a hole nor a leaf.
- Every untrusted section a guideline, a verifier or the chat's verdict reads goes through `fenced(label, inside)` in
  `judge/agents.py`: two marker lines carrying `BOUNDARY`, a nonce drawn once a process, and the instruction ends with
  a reminder, `REMINDER` for the guidelines and the verifiers. Never escape or rewrite the content itself: a guideline
  quotes the diff verbatim, and grounding holds the quote to the real lines.
- A state key more than one module uses is a constant in `judge/agents.py` (`SCOPE`, `REPOSITORY`, `DIFF`, `BLAST`,
  `RULES`, `FAILURE`), and an instruction reads a key through `placeholder(key)`; `tests/test_engine.py` holds every
  placeholder an instruction reads to a key something writes.
- Every rule runs the same way, through `run_rule`, in one `FunctionNode` (`judge/checks.py`) before the guidelines,
  in both shapes, or straight from the driver through the same `run_rules` where no guideline runs; each rule's answer, a `Checked`, is left in state under its check's key. The node also writes the
  blast radius the guidelines read and, under `RULES`, every answer rendered and capped for the chat's verdict; in
  chat it gathers the evidence for the commits the intake confirmed, never their names again; and once the rules have
  read them it lets go of the tree, the texts, the diffs and the added lines. The node never raises: a node that
  raises ends the whole run, every guideline with it.
- `--json FILE` writes the review as data (`as_data` in `deliver/report.py`): every string through the text report's
  own safety steps, `one_line` then `inert`, evidence cut where the report cuts it. Raise `SCHEMA` when a field
  changes its meaning or goes; add a field freely. A finding's `fingerprint` is built from its check, file and evidence,
  never its line — a guideline's evidence is the lines its quote stands on, which grounding sets — and `--previous FILE`
  compares by it; a review with a hole resolves nothing.
- `GUIDE.md` is held to the code by `tests/test_docs.py`: a new flag, check, JSON field or limit
  fails the suite until the guide says it. Regenerate its check table from `--list-checks`.
- The plugins are `RedactSecretsPlugin`, then the optional `DebugLoggingPlugin` (`--debug-log`, never the session's
  state) and `Recorder` (`--record`), then `UsageLedger`, `ReportProviderErrors` and `Progress`, which times each
  agent and, at a terminal or with `--progress`, tells each guideline's start and finish on stderr; the first plugin
  to answer short-circuits the rest. Chat puts `RefuseUnready` and `OneTurn` first: each judging agent reads its
  instruction and one fixed turn, never a turn ADK would make of another agent's output.
- In chat the scope is a `temp:` key, which ADK keeps for one turn, and a gate after the intake goes on only for a
  scope that turn confirmed; the intake is told the repository its conversation confirmed (`{review_repo?}`).
- Under `--all` the report adds a Health section, `core/health.py`: what was read, the source and the tests apart,
  McCabe complexity as ruff's C901 counts it (a nested function on its own) in the SIG bands, maintainability graded
  A to E by SIG's published risk-profile rating only where ten or more source files parsed and nine in ten of those
  at the head, each rule's findings counted by severity and never graded. The verdict never reads any of it.

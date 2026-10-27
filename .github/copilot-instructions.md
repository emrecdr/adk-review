# Copilot instructions for this repository

This repository is `adk-review`, a git-branch code reviewer for Python projects, on Google ADK (`google-adk==2.13.0`,
Python 3.13, `uv`). It is grouped by the steps of a review — `core`, `collect`, `judge`, `deliver` — and every check is
one file: a rule, decided by code, in `adk_review/judge/rules/`, importing one door, `adk_review/judge/rule_helpers/`,
and held to its own examples; a guideline, judged by a model, in `adk_review/judge/guidelines/`; each named after its
file and chosen by profile in `adk_review/config.toml`. Around them: the rules as one node before the guidelines,
retries, a token ceiling, `--verify`, each file's blast radius and the calls into what the change touched, fences with a
nonce around all the guidelines read, what a review covers (`--path`, `--all`, `--staged`) and reads (`[review] scan`:
Python's files and the configs beside them, never another project's code under `third_party`, the credential rules alone
reading the project's own files of every kind), a JSON report for pipelines (`--json`), `--quiet` for a pipeline's
silent log, progress with a heartbeat at a terminal, and a pre-push hook and a commit hook. `CLAUDE.md` at the root is
the long form of these instructions, which Copilot in VS Code and the cloud agent read as well, so the two agree;
`README.md` is the developer's guide and `GUIDE.md` the user's. Path-specific rules sit in `.github/instructions/`, and
the recurring tasks in `.github/skills/`, each a slash command in VS Code's chat: `/new-rule`, `/new-guideline`,
`/review-a-branch` and `/update-docs`, which keeps the docs true after a change.

## Hard rules for every change

- Double-check and validate before starting to implement: reproduce each finding first — a probe on a scratch
  repository, a measurement, or a test that fails on the committed code — and confirm its cause in the code. A finding
  that does not reproduce is reported, never fixed.
- Size a change to its benefit: never implement a big or complex change for a very small, minor benefit. Prefer the
  smallest fix, a sentence in the guide that names the limit, or no change, and say which was chosen and why.
- Support Windows and macOS alike, and Linux: every change works, and is tested, on each. A test is skipped on one
  only where what it tests cannot exist there — a signal Windows cannot catch, a file mode it does not hold — and its
  reason says so; a feature a system runs another way is tested that way there, as Git for Windows runs a hook through
  its own `sh`. Write each file a shell or git reads with `newline="\n"`, and each path given to a shell as POSIX.
- Make the user's life easier, always, as the owner asks again and again: a sensible default wherever one fits, so a
  common task needs no extra flag or file; an example of each common task in the guides, ready to copy; and every
  message, error or report naming what to do next. Where a choice is the user's, offer it with a recommendation.

## Secrets and configuration

One `.env` at the root (gitignored, copied from `.env.example`) holds `REVIEW_PROVIDER`, `REVIEW_MODEL` and the keys;
the shell wins over it at every entry point. A guideline may name its own model per arm in its file, `models =
Models(copilot=…, gemini=…)`, which `REVIEW_<ID>_MODEL` overrides; `REVIEW_VERIFIER_MODEL` gives `--verify` its own.
Never read, print or quote `.env`, a key, or the Copilot token or its folder (`~/.config/litellm/github_copilot`, or
`GITHUB_COPILOT_TOKEN_DIR`), and never run `adk-review-login`: it needs the person at a browser, and `--force` replaces
their token. `adk_review/judge/config.py` injects `truststore` before any client is built, so certificates come from the
operating system's store; keep that line where it is.

## When reviewing a pull request

Flag a change that breaks a rule below or in `.github/instructions/`, naming the rule and the test that would catch
it — `tests/test_docs.py` for the guides, `tests/test_layering.py` for the imports, `tests/test_portability.py` for
`encoding=` and `/tmp/`, `tests/test_copilot.py` for the files under `.github/` — and look first for the commonest
miss: a new flag, check, JSON field or limit the guide does not say.

## Commands

- `uv sync --locked` installs what the lock pins. `uv run ruff check . && uv run ruff format --check .` lints
  (`make lint`); `uv run pyrefly check` checks the types of the package and the scripts, held to no error
  (`make typecheck`); `uv run pytest -q` runs the whole offline suite, no key needed (`make test`; `make ci` is all
  three). Run all three before calling anything done, and report what they printed.
- `uv run python scripts/make_demo_repo.py` builds the demo repository with its planted defects (`--fix` commits its
  two fixes); `uv run adk-review <demo repo> --head feature/payments --base main --profile demo` reviews it with
  no model call, and `--profile default-rules`, `--check rule.<name>`, `--dry-run`, `--list-checks`, `--explain` and
  `--try` make none either. A review whose profile names a guideline calls a provider, and so do
  `--check guideline.<name>`, `adk web`, `adk run`, `scripts/measure.py`, `scripts/injection_suite.py` and
  `scripts/labelled_set.py`, so run one only when the task says so.
- A review under `default` makes three calls, `simplify` three, `code-review` five and `full` six, at most three in
  flight (`CONCURRENCY`). On Copilot each call is billed to the seat that logged in, and GitHub rate-limits each user,
  unpublished; Gemini's free tier allows five a minute and twenty a day: a run that met a 429 names it under
  `could_not_look` and measures nothing there, and a live script stops at it. The cloud agent's setup logs in to
  nothing, so there a review naming a guideline stops at readiness, exit 2: run the model-free profiles. Run each side
  of a before-and-after comparison from its own copy of the tree, since a review imports the checkout.
- `[tool.uv]` in `pyproject.toml` takes a release only three days after it is published (`exclude-newer`), so a
  dependency released since does not resolve yet; the comment there says why the span stays. Dependabot
  (`.github/dependabot.yml`) proposes each week's updates after the same three days, and no package skips the wait.
  Its `ignore` holds LiteLLM below the releases made after a compromised one, and the comment there says when to lift
  it: bump LiteLLM by hand no further either.

## Rules the tests enforce

- Imports point one way, `core` → `collect` → `judge` → `deliver`, relative and never spelled from the top
  (`tests/test_layering.py`); `deliver/` imports `core` alone, a guideline `core` alone, a rule the door alone. `core/`,
  the checks and the helpers import no network, process or model module and must import with ADK absent.
- The docs are tested (`tests/test_docs.py`): every guide names only paths the working tree has, `README.md` names each
  module at the package's root, and `GUIDE.md` holds every flag, check, JSON field and limit as the code has them.
- `root_agent.name` equals the folder name, `adk_review`. Never name a sub-package module `agent.py`: ADK's nested
  loader would take it for an agent. `adk_review/judge/config.py` is the only file that builds a provider's model
  (Gemini a plain model string, Copilot `LiteLlm("github_copilot/…")`); `adk_review/judge/settings.py` only reads the
  environment. Both arms are first-class, Copilot the one this reviewer targets and the default: every change works,
  is tested and is measured live on each, on Copilot first; and judging agents run at temperature zero,
  a model that cannot being refused before any call.
- A provider error is the agent's own answer, and so is a reply that does not fit its schema (`SCHEMA_MISMATCH`); "did
  not look" must never read as "found nothing". An after-model hook passes a streamed `partial` piece by and reads
  the whole answer that follows it.
- Tools return a `{status: …}` dict and never raise at the model. Git runs by its full path from `PATH`'s absolute
  entries, read-only verbs only, as an argv list with a timeout, inside `_environment(repo)`, in the C locale so git's
  messages read as written, which also starts no file-system monitor the repository names; nothing ever writes into the
  repository under review, not even a fetch: a branch it does not hold is named with the `git fetch` for the person to
  run. A failed git call raises `GitError`, never an empty answer. `_sha` alone reads git's exit 128 as no commit, a
  revision git cannot follow, and only where git says nothing: an `error:` or `fatal:` line at exit 1 or 128 is a
  `GitError`, a corrupt object never a missing name.
- The reviewer posts nothing to a pull request: `--comment [FILE]` writes a comment for the branch's author, by default
  `comments-<repo>__<base>__<head>__<minute>.md`, which the person pastes by hand, and no command that would post it is
  printed; `--handoff [FILE]` writes the findings for a coding agent, by default
  `handoff-<repo>__<base>__<head>__<minute>.md`, and applies none of them itself.
- Every string a model reads is capped in characters with the cut named in the text, and so is each title and change a
  model writes into a finding. A dropped finding's file is a model's text too, cut in `drop_ungrounded` at
  `REASON_MAX_CHARS`. Every `subprocess.run` that decodes passes `encoding=`; no source spells `/tmp/`
  (`tempfile.gettempdir()`); a temporary folder the package makes passes `ignore_cleanup_errors=True`, since Windows can
  hold a file in it open; a file a script or a fixture commits is written with `newline="\n"`.
- Every sentence the package prints itself — a stderr line, a flag's `--help`, an error it raises or answers, a rule's
  title and fix — is ASD-STE100: one command, at most 20 words, no `;` joining two (`tests/test_command.py`). The
  guidelines, chat's intake and its verdict are told the same for their own words, and to quote the diff unchanged. An
  error a person can act on names what to do instead, a flag to drop or give, a path or a command, as the owner asked of
  a tool that guides its user; argparse's own end by naming `--help`. An error's text ends bare, with no full stop,
  since a caller may add a sentence after it; and the flags that rescope a change with nothing to compare are added by
  the command, from a mark on the error envelope (`rescope`). A rule's `(path, text)` example names a file the shipped
  `scan` reads (`tests/test_catalog.py`).

## Tests

The suite is offline and lives in `tests/` alone; no test may call a provider or need a key. How a test is written —
the fixtures, the naming, async — is in `.github/instructions/tests.instructions.md`, which loads with any test.

## Style

Ruff, line length 120, Python 3.13. Comments and docstrings are plain prose that says why, and what the package prints
follows ASD-STE100; commit subjects are plain-prose sentences stating what changed and what was learned. A pull request
comes with lint, the type check and the suite green on the three platforms CI runs (`.github/workflows/ci.yml`,
`azure-pipelines.yml`), and a workflow action is pinned by its commit SHA, the same in every workflow and the README's
sample, and uv by one exact version in every pipeline.

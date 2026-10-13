# Copilot instructions for this repository

This repository is `adk-review`, a git-branch code reviewer on Google ADK (`google-adk[extensions]==2.9.2`, Python
3.13, `uv`). It is grouped by the steps of a review — `core`, `collect`, `judge`, `deliver` — and every check is one
file: a rule, decided by code, in `adk_review/judge/rules/`, importing one door, `adk_review/judge/rule_helpers/`, and
held to its own examples; a guideline, judged by a model, in `adk_review/judge/guidelines/`; each named after its file
and chosen by profile in `adk_review/config.toml`. Around them: the rules as one node before the guidelines, retries, a
token ceiling, `--verify`, each file's blast radius, fences with a nonce around all the guidelines read, what a review
covers (`--path`, `--all`, `--staged`), a JSON report for pipelines (`--json`) and a pre-push hook. `CLAUDE.md` at the
root is the long form of these instructions; `README.md` is the developer's guide and `GUIDE.md` the user's.
Path-specific rules sit in `.github/instructions/`, the recurring tasks in `.github/skills/`.

## Commands

- `uv sync` installs. `uv run ruff check . && uv run ruff format --check .` lints (`make lint`); `uv run pytest -q`
  runs the whole offline suite, no key needed (`make test`; `make ci` is both). Run both before calling anything done,
  and report what they printed.
- `uv run python scripts/make_demo_repo.py` builds the demo repository with its planted defects (`--fix` commits its
  two fixes); `uv run adk-review <demo repo> --head feature/payments --base main --profile rules-only` reviews it with
  no model call, and `--check rule.<name>`, `--dry-run` and `--list-checks` make none either. Every other review or
  chat command calls a provider — `--check guideline.<name>`, `adk web`, `adk run`, `scripts/measure.py`,
  `scripts/injection_suite.py` and `scripts/labelled_set.py` — so run one only when the task says so.
- `[tool.uv]` in `pyproject.toml` takes a release only three days after it is published (`exclude-newer`), so a
  dependency released since does not resolve yet; the comment there says why the span stays.

## Rules the tests enforce

- Imports point one way, `core` → `collect` → `judge` → `deliver`, relative and never spelled from the top
  (`tests/test_layering.py`); `deliver/` imports `core` alone, a guideline `core` alone, a rule the door alone. `core/`,
  the checks and the helpers import no network, process or model module and must import with ADK absent.
- The docs are tested (`tests/test_docs.py`): every guide names only paths the working tree has, `README.md` names each
  module at the package's root, and `GUIDE.md` holds every flag, check, JSON field and limit as the code has them.
- `root_agent.name` equals the folder name, `adk_review`. Never name a sub-package module `agent.py`: ADK's nested
  loader would take it for an agent. `adk_review/judge/config.py` is the only file that names a provider (Gemini a
  plain model string, Copilot `LiteLlm("github_copilot/…")`). Both arms are first-class: every change works, is
  tested and is measured live on each, `copilot` being only the default; and judging agents run at temperature zero,
  a model that cannot being refused before any call.
- A provider error is the agent's own answer, and so is a reply that does not fit its schema (`SCHEMA_MISMATCH`); "did
  not look" must never read as "found nothing".
- Tools return a `{status: …}` dict and never raise at the model. Git runs by its full path from `PATH`'s absolute
  entries, read-only verbs only, as an argv list with a timeout, inside `_environment(repo)`, which also starts no
  file-system monitor the repository names; nothing ever writes into the repository under review, and a failed git
  call raises `GitError`, never an empty answer.
- Every string a model reads is capped in characters with the cut named in the text. Every `subprocess.run` that
  decodes passes `encoding=`; no source spells `/tmp/` (`tempfile.gettempdir()`); a file a script or a fixture commits
  is written with `newline="\n"`.

## Tests

The suite is offline and lives in `tests/` alone; no test may call a provider or need a key. How a test is written —
the fixtures, the naming, async — is in `.github/instructions/tests.instructions.md`, which loads with any test.

## Secrets and configuration

One `.env` at the root (gitignored, copied from `.env.example`) holds `REVIEW_PROVIDER`, `REVIEW_MODEL` and the keys;
the shell wins over it at every entry point. Never read, print or quote `.env`, a key or the Copilot token.
`adk_review/judge/config.py` injects `truststore` before any client is built, so certificates come from the operating
system's store; keep that line where it is.

## Style

Ruff, line length 120, Python 3.13. Comments and docstrings are plain prose that says why; commit subjects are
plain-prose sentences stating what changed and what was learned. A pull request comes with lint and the suite green
on the three platforms CI runs (`.github/workflows/ci.yml`, `azure-pipelines.yml`), and a workflow action is pinned
by its commit SHA.

## When reviewing a pull request

Flag a change that breaks a rule above or in `.github/instructions/`, naming the rule and the test that would catch
it — `tests/test_docs.py` for the guides, `tests/test_layering.py` for the imports, `tests/test_portability.py` for
`encoding=` and `/tmp/`, `tests/test_copilot.py` for the files under `.github/` — and look first for the commonest
miss: a new flag, check, JSON field or limit the guide does not say.

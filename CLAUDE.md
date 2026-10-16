# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

`adk-review`: a git-branch code reviewer on Google ADK (pinned `google-adk[extensions]==2.9.2`, Python 3.13, `uv`).
Every check is one file: a rule, decided by code, in `adk_review/judge/rules/`, or a guideline, judged by a model, in
`adk_review/judge/guidelines/`. The command line is the main entry point; `adk web` serves the same graph as a chat.
`README.md` is the developer's guide — the architecture, and how to write a rule or a guideline — and `GUIDE.md` the
user's: every flag, check, JSON field and limit, held to the code by tests.

## Commands

```bash
uv sync --locked                           # install what the lock pins (make sync)
uv run pytest -q                           # full offline suite, no key needed (make test)
uv run pytest -q tests/test_rules.py       # one file
uv run ruff check . && uv run ruff format --check .   # lint (make lint); `make format` fixes; `make ci` = lint + test

uv run python scripts/make_demo_repo.py          # build <tmp>/adk-demo-repo: main + feature/payments with 4 planted defects
uv run python scripts/make_demo_repo.py --fix    # commit the two fixes on feature/payments
uv run adk-review [repo] [--head B] [--base main | --all | --staged] [--path DIR] [--fail-on …] [--verify | --no-verify] [--max-tokens N] [--diff-cap N] [--skip GLOB] [--profile NAME | --check ID] [--json FILE] [--handoff FILE] [--cache DIR] [--previous FILE] [--progress] [--debug-log FILE] [--trace FILE] [--record DIR] [--dry-run] [--list-checks] [--explain ID] [--try PATH…] [--version]
uv run adk-review <demo repo> --head feature/payments --base main --profile rules-only   # every rule, no model call, no key
uv run adk-review <demo repo> --head feature/payments --base main --dry-run   # what a review would cover and run, nothing run
uv run python scripts/copilot_login.py           # one-time GitHub device-flow login for the Copilot arm
uv run adk web .                                 # the chat in a browser, over the same graph
hook="$(git -C <repo> rev-parse --path-format=absolute --git-path hooks)/pre-push" && mkdir -p "${hook%/*}" && printf '#!/bin/sh\nexec adk-review-push "$@"\n' > "$hook" && chmod +x "$hook"   # after `uv tool install .`: default-rules on what a push sends, push-history's credential rules on each commit and message; pre-commit's adk-review hook runs the same; --no-verify skips
uv run python scripts/injection_suite.py --runs 3 [--build-only]   # live: does a payload make a guideline lose the planted shell call
uv run python scripts/labelled_set.py --runs 3 [--build-only]      # live: one known defect a branch, or none: found, missed, invented
uv run python scripts/measure.py --runs 5 [--all] # live: per planted defect, how often a guideline names it (--all: as a whole project)
```

`uv tool install .` puts `adk-review` and `adk-review-push` on `PATH` anywhere. With no `repo`, the command assumes
none: a terminal is asked for the path, a pipeline is refused with exit 2. Exit codes: 0 approved, 1 a blocker, 2 a
usage error before any model call (an arm that is not ready, a branch that adds nothing: "no change to review", a
`config.toml`, profile or rule file that cannot run, nothing under `--path`, or no choice made at the question asked
when neither `--base` nor `--all` is given), 3 a hole: a rule or a guideline failed, a rule could not look (the head's
tree not listed, or a file it holds not read whole), a changed file no guideline read whole — binary, a submodule, cut
at the diff cap or past the guidelines' block cap — named under "not read", a finding at the `--fail-on` bar whose quote
the diff does not bear out, any error nothing else names (`main` wraps the command: a sentence, never Python's own exit
1, which would read as a blocker), a reader that closes the pipe early, and a reviewer that cannot load (a broken
install). Ctrl-C is one sentence and 130, the shell's own code for it.

A model call needs a configured arm and spends its quota: `adk web`, `adk run`, a review whose profile names a
guideline, `--check guideline.<name>` and the three `live:` scripts make one, so run them only when the task says so.
A review under `default` makes three calls at once: Gemini's free tier allows five a minute and twenty a day, so a
live script's sixteen reviews on it need `--pause` and more than a day, and Copilot rate-limits a model after some
hundreds of calls in an afternoon; a run that met a 429 names it under `could_not_look` and measures nothing there.
Run each side of a before-and-after comparison from its own copy of the tree, since a review imports the checkout.
The suite, `--profile rules-only` or `default-rules`, `--check rule.<name>`, `--dry-run`, `--list-checks`, `--explain`
and `--try` make none. `[tool.uv]` in `pyproject.toml` takes a release only three days after it is published
(`exclude-newer`), so a dependency released since does not resolve yet; the comment there says why the span stays.

## Configuration

One `.env` at the root (copy `.env.example`). `REVIEW_PROVIDER=copilot|gemini` picks the arm, `copilot` when unset;
`REVIEW_MODEL` blank means `gpt-4.1` / `gemini-2.5-flash`; `REVIEW_VERIFIER_MODEL` and `REVIEW_FALLBACK_MODEL` are read
too. Copilot needs a one-time `uv run python scripts/copilot_login.py`; Gemini needs `GOOGLE_API_KEY`, or through
Vertex AI a project, `GOOGLE_CLOUD_PROJECT` or the one Application Default Credentials name — `require_ready` asks
google-genai itself, never a copy of its rules, and on the Gemini arm refuses a model ADK sends to another provider
(`gpt-*`, `claude-*`). Both arms are first-class: a change works, is tested and is measured live on each, and
`copilot` is only the default. `adk_review/judge/config.py` injects `truststore` before any client
is built, so certificates are verified through the operating system's store (a company proxy's root the machine
trusts, Python trusts too), with `SSL_CERT_FILE` still read where set; `scripts/copilot_login.py` does the same and
reads the `GITHUB_COPILOT_*` endpoints from `.env`. The shell wins over `.env` at every entry point: ADK 2.9.2 restores
the variables set before it loads the file, and the command loads it without overriding. The report names the model,
the arm that ran, and Vertex AI or a GitHub Enterprise tenant's host where the arm goes through one. Never read,
print or quote `.env`, a key or the Copilot token.

## Architecture

The package is grouped by the steps of a review — `core`, `collect`, `judge`, `deliver` — and every check is one file.
A rule is one class of three kinds: a `Rule`, `check(path, line)` over each added line; a `FileRule`, `check(file)`
over each changed file whole at the head, its lines, the lines the change added, its module, and an outside tool's
diagnostics where it names the tool (`lint` names ruff, whose adapter `collect/tools.py` holds, run by
`collect/ruff.py`); a `TreeRule`, `check(tree)` over the head's paths and parsed modules, naming a line where it knows
one. A guideline is a class naming its role and a thinking budget (`core/guidelines.py`), built into an agent by
`judge/agents.py`. `judge/catalog.py` finds every check, reads `config.toml` and resolves what runs, for the command and
the chat alike. A rule imports one door, `judge/rule_helpers/`: the contract, passed on from `core/rules.py`, and
helpers named after what they read (`paths.py`, `lines.py`, `syntax.py`) and try a rule with (`testing.py`). A rule
carries `bad` and `good` examples, which the catalog holds it to at every start with its own source, a rule failing
either being exit 2; `try_rule`, `lines_hit` and `self_check` try a rule with no repository, `--list-checks` lists every
check with its kind, its profiles and its file, `--check ID` runs one check alone. Each check has one id,
`rule.<name>` or `guideline.<name>` (`core/profiles.py`), in the config, on the command line and as its own report
section; inside ADK it is `state_key`'s identifier, `rule_<name>`.

The one config file, `config.toml` beside `review.py`, holds the profiles under `[profiles]` — named lists of check ids
or the groups `rules` and `guidelines`, `default` the basics — and the review policy (`[review]` `fail_on`,
`max_tokens`, `verify`, `diff_cap`, `skip`, each a flag's default), read whole so a misspelt key is exit 2. With no
`--profile`, `default` runs, and a check runs only where a profile names it, or alone with `--check ID`. `--profile` is
the only choice, and nothing is read from the repository under review, `.gitignore` included: git keeps ignored files
out of commits (under `--staged` git reads it only to name the untracked files the report lists). What a review covers:
`--path DIR` a folder of the change, the rest counted in the report; `--all` every file at the head, diffed against
git's empty tree; `--head` defaults to the branch checked out; with neither `--base` nor `--all` a terminal asks, and a
pipeline reviews the change against `main`, or `master`, or else the branch `origin/HEAD` names.

Evidence is gathered in Python first, the rules run as one `FunctionNode` before the guidelines in both shapes
(`judge/checks.py`, every rule through `run_rule`), and code decides the verdict. Around them: retries, a token
ceiling, a `--verify` second opinion, a JSON report a pipeline reads (`--json FILE`, schema 2, written for every
decided review, under the text report's safety rules), under `--all` a Health section (`core/health.py`: what was read,
source and tests apart, McCabe complexity counted as ruff's C901 counts it, in bands graded A to E by SIG's published
risk-profile rating where most of the source parsed, each rule's findings by severity, counted and never graded; never
read by the verdict), each changed file's blast radius (`core/blast.py`: an import graph parsed from the head commit,
shown to the guidelines as `{blast?}` and heading the report, never read by the verdict), and fences around every
untrusted section the guidelines, the verifiers and the chat's verdict read (`judge/agents.py`: two marker lines
carrying a nonce drawn once a process, a reminder after them, the content never escaped so a quote still grounds).

Load-bearing rules the tests enforce:

- **Loader.** `root_agent.name` must equal the folder name, `adk_review`. `adk web` uses ADK's nested loader, which
  treats any folder holding an `agent.py` (up to five levels deep) as an agent, so never name a sub-package module
  `agent.py` (the graph lives in `judge/graph.py` for this reason). `tests/test_wiring.py` asserts `adk web` lists the
  package alone.
- **Package root.** `__init__.py` imports nothing and answers `agent` lazily via PEP 562 `__getattr__`, because
  `python -m adk_review.review` imports the package before `main` reads `.env`, and `agent.py` builds its agents at
  import. `review.py` imports ADK and the judge's engine modules (agents, config, graph, plugins, run, verify) where a
  review starts, never at its top, so `--list-checks`, `--explain`, `--try`, a usage error and the pre-push hook start
  without them; a test runs those modes with `google` unimportable.
- **Providers.** `judge/config.py` is the only file that names a provider: Gemini is a plain model string, Copilot is
  `LiteLlm("github_copilot/…")`. Every change must work on both arms. Judging agents run at temperature zero; a model
  that cannot, Copilot's `gpt-5` or o-series as LiteLLM says offline, is refused by `require_ready`, exit 2. Copilot
  is sent the instruction as its system turn (`litellm.disable_copilot_system_to_assistant`), as Gemini reads it.
- **Shape.** `agent.py` exports both `app` and `root_agent` (the loader prefers `app`, and a bare `root_agent` would
  lose the plugins). `build_app(chat=True)` adds an intake agent before the same middle and a verdict after it; the
  command uses `chat=False` over seeded state. In chat a gate after the intake goes on only for a scope this turn's
  intake confirmed — a `temp:` key, which ADK keeps for one turn — so a turn that confirms none ends at the intake's
  answer; the checks node gathers its own evidence from that scope and writes the change the guidelines read, where
  the command seeds the change at the session's start, in no event a debug log could carry. Plugins:
  `RedactSecretsPlugin`, `UsageLedger`, `ReportProviderErrors`, and `Progress`, which times each agent and, at a
  terminal or with `--progress`, tells each guideline's start and finish on stderr; in chat, first, `RefuseUnready`,
  which asks `require_ready` once a turn, as the command asks before it builds anything, and answers its sentence in
  each model call's place while the arm cannot run, then `OneTurn`, which gives each judging agent one fixed turn,
  `review`, as the command's read: ADK would hand the verdict the guidelines' findings unfenced as a turn.
- **Layering** (`tests/test_layering.py`): `core` → `collect` → `judge` → `deliver`, with imports pointing one way
  only, never spelled from the top (`adk_review.…`), `deliver/` importing `core` alone, a guideline importing `core`
  alone, and a rule importing one door, `judge/rule_helpers/`, alone, which imports `core` alone and lists in `__all__`
  exactly what it passes on. `core/`, the checks and the helpers may not import `google`, `litellm`, `subprocess`,
  `httpx`, `asyncio`, `os`, `socket`, `urllib`, `http` or `ssl`, and must import with ADK absent; each check folder
  holds one check a file, named after it, and nothing else — no `__init__.py`, no guide, no template.
- **Errors.** `on_model_error_callback` turns a provider error into the agent's answer and leaves `temp:failure` in
  state, and `ReportProviderErrors` also answers a reply that does not fit its schema, a cut one included, as that
  agent's own error (`SCHEMA_MISMATCH`), where ADK would raise and end the run; guidelines carry a
  `before_agent_callback` that skips them when there is no evidence. "Did not look" must never read as "found nothing".

Conventions:

- Tools return a `{status: …}` dict and never raise at the model.
- Git runs by its full path from `PATH`'s absolute entries, never from the current directory (Windows searches it
  first, an empty or relative entry names it, and it can be the checkout under review), as an argv list with a timeout
  that stops the whole command (Git for Windows' `git.exe` starts the real git as its child), read-only verbs only, and
  nothing ever writes into the repository under review, not even the lazy fetch a partial clone would make. Every call
  runs in `_environment(repo)`: no inherited `GIT_*` variable, no global or system config and no attributes file but
  the repository's, `safe.directory` for the one repository named, no lazy fetch, no replace refs or grafts,
  `core.precomposeUnicode`, `diff.suppressBlankEmpty` and `core.fsmonitor` fixed through `GIT_CONFIG_COUNT`. A file's
  diff is read as git prints it for no one's settings, through plumbing (`diff-tree -r -p`): each path as
  `:(top,literal)` pathspec magic, the merge base's attributes (`--attr-source`, git 2.41; under `--all`, the empty
  tree's), `--no-color --no-ext-diff --no-textconv`. A call that fails, cannot start or runs past its timeout raises
  `GitError`, turned into an error envelope only where envelopes are built; never answer it as an empty result.
  `_git(..., ok=(0, 1))` is only for calls whose exit code 1 is an answer.
- Every string a model reads is capped in characters, with the cut named in the text, and the cut never falls inside a
  credential. The report cuts each finding's evidence the same way, after the scrub, and breaks CI runners' command
  openers (`##[`, `##vso[`) and HTML comments' (`<!--`) in all it prints, every character a reader cannot see printed
  as a space.
- State moves between agents via `output_key` and `{placeholder}` (`{key?}` where the key may be missing). Each key
  more than one module uses is spelled once, in `judge/agents.py`; a check's key is its id through `state_key`.
- Every `subprocess.run` that decodes passes `encoding=`; no source under `adk_review/` or `scripts/` spells `/tmp/`
  (use `tempfile.gettempdir()`). Every file a script or a fixture commits is written with `newline="\n"`, a `.env`
  that is not UTF-8 is a sentence and exit 2, and `.github/workflows/ci.yml` and `azure-pipelines.yml` run lint and
  the suite on Windows, Linux and macOS. Every action a workflow uses is pinned by its full commit SHA, the same
  commit in every workflow (`tests/test_copilot.py`).

## Tests

The suite is fully offline. `tests/conftest.py` registers a `FakeModel` in ADK's `LLMRegistry` for `fake.*`.
`load_agent(monkeypatch)` sets `REVIEW_MODEL=fake` and imports `adk_review.agent` fresh, so the real `build_model()`,
Runner, `Workflow`, callbacks and plugins all run; the `cli` fixture is the command's module loaded that way. Both
run on the Gemini arm, where the fake registers, unless a test names another: `load_agent(monkeypatch, "copilot",
"gpt-4.1")`, or `load_command` for the command, with LiteLLM's network client replaced, runs the Copilot arm's own
path offline (`test_the_planted_review_runs_end_to_end_on_the_copilot_arm`); a change to that path needs a test
there. The fake
answers whatever canned payload the request's response schema accepts, or a sentence when there is no schema. The
`fake_provider` fixture's `raises_when(text, exc)` / `answers_when(text, payload)` / `calls_when(text, call)` key on
text in the system instruction, to simulate a provider failing, a verifier refuting or an agent calling a tool;
`confirms(fake_provider, repo)` makes chat's intake confirm a scope with its tool, as a model would, `Chat(module)`
holds one conversation over several turns, and the `asked` fixture records each request as it was sent. `demo_repo` is a session-scoped real repository
from `scripts/make_demo_repo.py`, and `hostile_repo` a branch per way a change once hid from the review.
`asyncio_mode = "strict"`, so async tests need `@pytest.mark.asyncio`. A test still running after
`faulthandler_timeout` prints every thread's stack and ends the run. Tests live only in `tests/`. A test is named as
the sentence it proves (`test_the_gate_runs_before_any_model`), and a helper several tests need goes into
`tests/conftest.py`, never into the package.

## The docs are tested, so update them with the code

`tests/test_docs.py` reads the docs the way a reader does: every guide (this file, `GUIDE.md` and the Markdown under
`.github/`; not `README.md`, which names other repositories' paths) names only backticked paths with a folder in them
that the working tree holds; `README.md` names each module at the package's root; and
`GUIDE.md` holds every flag the parser takes and no other, the checks as `--list-checks` prints them, every JSON field
in order, and each limit the number its constant holds. A new flag, check, field or limit fails the suite until the
guide says it. The docs state no static counts of the repository's current state and carry no dates.

## Copilot's files

`.github/copilot-instructions.md` is the short form of this file for GitHub Copilot, with path-specific rules in
`.github/instructions/`, skills for the recurring tasks in `.github/skills/`, a custom agent in `.github/agents/` and
the cloud agent's setup in `.github/workflows/copilot-setup-steps.yml`. A rule that changes here changes there too;
`tests/test_copilot.py` holds those files to their format.

Claude Code loads none of them by itself, and they hold procedures this file does not repeat. Before adding a rule,
read `.github/skills/new-rule/SKILL.md` and `.github/instructions/reviewer.instructions.md` (a rule's constants,
examples and helpers); before adding a guideline, `.github/skills/new-guideline/SKILL.md` (the tests that count the
guidelines, and the phrases that find every doc naming them); before running a review,
`.github/skills/review-a-branch/SKILL.md`.

## History

Commit subjects are plain-prose sentences stating what changed and what was learned.

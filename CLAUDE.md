# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

`adk-review`: a git-branch code reviewer for Python projects, on Google ADK (pinned `google-adk==2.13.0`,
Python 3.13, `uv`). Every check is one file: a rule, decided by code, in `adk_review/judge/rules/`, or a guideline,
judged by a model, in `adk_review/judge/guidelines/`. The command line is the main entry point; `adk web` serves the
same graph as a chat. `README.md` is the developer's guide — the architecture, and how to write a rule or a guideline —
and `GUIDE.md` the user's: every flag, check, JSON field and limit, held to the code by tests.

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

## Commands

```bash
uv sync --locked                           # install what the lock pins (make sync)
uv run pytest -q                           # full offline suite, no key needed (make test)
uv run pytest -q tests/test_rules.py       # one file
uv run ruff check . && uv run ruff format --check .   # lint (make lint); `make format` fixes
uv run pyrefly check                                  # types: the package and scripts, no error (make typecheck); `make ci` = lint + typecheck + test

uv run python scripts/make_demo_repo.py          # build <tmp>/adk-demo-repo: main + feature/payments with 4 planted defects
uv run python scripts/make_demo_repo.py --fix    # commit the two fixes on feature/payments
uv run adk-review [repo] [--head B] [--base main | --all | --staged] [--path DIR] [--path-exclude DIR] [--fail-on …] [--verify | --no-verify] [--max-tokens N] [--diff-cap N] [--skip GLOB] [--scan GLOB] [--third-party GLOB] [--profile NAME | --check ID] [--json FILE] [--handoff [FILE]] [--comment [FILE]] [--cache DIR] [--previous FILE] [--progress | --quiet] [--debug-log FILE] [--trace FILE] [--record DIR] [--dry-run] [--list-checks] [--explain ID] [--try PATH…] [--version]
uv run adk-review <demo repo> --head feature/payments --base main --profile demo   # every rule, the demo's own among them, no model call, no key
uv run adk-review <demo repo> --head feature/payments --base main --dry-run   # what a review would cover and run, nothing run
uv run adk-review-login                          # one-time GitHub device-flow login for the Copilot arm
uv run adk web .                                 # the chat in a browser, over the same graph
# the commit hook, after `make install`: GUIDE.md's "Before a commit" lines, what a commit holds (`git commit -a` and `<paths>` included) with default-rules; pre-commit's adk-review-commit hook runs the same
# the pre-push hook, after `make install`: GUIDE.md's "Before a push" lines, which keep a pre-push hook already there (Git LFS's) and run it after the review; default-rules on what a push sends, push-history's credential rules on each commit and message; pre-commit's adk-review hook runs the same; --no-verify skips
uv run python scripts/injection_suite.py --runs 3 [--profile NAME | --check ID] [--branch NAME] [--build-only]   # live: does a payload make a guideline lose the planted shell call
uv run python scripts/labelled_set.py --runs 3 [--profile NAME | --check ID] [--branch NAME] [--build-only]      # live: one known defect a branch, or none: found (and by which guideline), missed, invented at every severity, run by run
uv run python scripts/measure.py --runs 5 [--all] [--profile NAME | --check ID] # live: per planted defect, how often a guideline names it (--all: as a whole project)
```

`make install` puts `adk-review`, `adk-review-push`, `adk-review-commit` and `adk-review-login` on `PATH` anywhere,
every dependency at the lock's version, since `uv tool install` reads no lock; with no checkout, `uv tool install
--exclude-newer P3D git+<address>` keeps the cooldown. With no `repo`, the command assumes none: a terminal is asked for
the path, a pipeline is refused with exit 2. Exit codes: 0 approved, 1 a finding at the `--fail-on` bar (`blocker`
unless set), 2 a usage error before any model call (an arm that is not ready, a branch that adds nothing or shares no
history with its base: "no change to review", with `--head`, `--base` and `--all` named, a branch it does not hold,
named with its remote copy and the `git fetch` for the person to run, a name no branch can have, said to be none, a
`config.toml`, profile or rule file that cannot run, nothing under `--path`, no choice made at the question asked when
neither `--base` nor `--all` is given, or a stop at the question a terminal is asked when the guidelines cannot read the
change in one run, its parts planned and printed first), 3 a hole: a rule or a guideline failed, a rule could not look
(the head's tree not listed, a file it holds not read whole, or a link whose file was not read or followed), a changed
file no check can read whole — binary, a submodule, a Git LFS pointer — or, where a guideline runs, one it was not shown
whole — a symbolic link to a file it was not shown whole, cut at the diff cap or past the guidelines' block cap — unless
the skip list names it, named under "not read", a finding at the `--fail-on` bar whose quote the diff does not bear out
or that a verifier refuted, a guideline's call the token ceiling refused (a verifier's leaves its finding unverified), a
`--json`, `--handoff` or `--comment` file that cannot be written, any error nothing else names (`main` wraps the
command: a sentence, never Python's own exit 1, which would read as a blocker), a reader that closes the pipe early, and
a reviewer that cannot load (a broken install). Ctrl-C is one sentence and 130, the shell's own code for it, and a job's
SIGTERM 143.

A model call needs a configured arm and spends its quota: `adk web`, `adk run`, a review whose profile names a
guideline, `--check guideline.<name>` and the three `live:` scripts make one, so run them only when the task says so. A
review under `default` makes three calls, `simplify` three, `code-review` five and `full` six, at most three in flight
(`CONCURRENCY`): Gemini's free tier allows five a minute and twenty a day, so a live script, three reviews of each
branch by default, needs `--pause` on it and more than a day, and on Copilot each call is billed to the seat that logged
in, and GitHub rate-limits each user, unpublished: measured, `gpt-4.1`, which GitHub has deprecated, answered a 429 for
its utility models after about 175 calls; a run that met a 429 names it under `could_not_look` and measures nothing
there, and a live script stops at it. Run each side of a before-and-after comparison from its own copy of the tree,
since a review imports the checkout. The suite, `--profile rules-only`, `demo` or `default-rules`,
`--check rule.<name>`, `--dry-run`, `--list-checks`, `--explain` and `--try` make none. `[tool.uv]` in `pyproject.toml`
takes a release only three days after it is published (`exclude-newer`), so a dependency released since does not resolve
yet; the comment there says why the span stays. Dependabot (`.github/dependabot.yml`) proposes each week's updates after
the same three days, and no package skips the wait; it proposes the packages `pyproject.toml` names, and the lock's
other packages move with `uv lock --upgrade`, by hand. Its `ignore` holds LiteLLM below the releases made after a
compromised one, and the comment there says when to lift it: bump LiteLLM by hand no further either. `pyproject.toml`
pins ruff and pyrefly at the lock's versions, as it pins LiteLLM, so every install runs the two the suite ran with: a
pin and the lock move together.

## Configuration

One `.env` at the root (copy `.env.example`). `REVIEW_PROVIDER=copilot|gemini` picks the arm, `copilot` when unset;
`REVIEW_MODEL` blank means `gpt-4.1` / `gemini-2.5-flash`; `REVIEW_VERIFIER_MODEL` and `REVIEW_FALLBACK_MODEL` are read
too, and `REVIEW_<ID>_MODEL` gives one guideline its own model, over the `models` its file may name per arm. Copilot
needs a one-time `uv run adk-review-login`; Gemini needs `GOOGLE_API_KEY`, or through Vertex AI a project,
`GOOGLE_CLOUD_PROJECT` or the one Application Default Credentials name — `require_ready` asks google-genai itself, never
a copy of its rules, and on the Gemini arm refuses a model that is not Gemini's (`gpt-*`, `claude-*`). Both arms are
first-class, Copilot the one this reviewer targets and the default: a change works, is tested and is measured live on
each, on Copilot first. `adk_review/judge/config.py` injects `truststore` before any client is built, so certificates
are verified through the operating system's store (a company proxy's root the machine trusts, Python trusts too), with
`SSL_CERT_FILE` still read where set; the login, `adk_review/login.py`, does the same and reads the `GITHUB_COPILOT_*`
endpoints from `.env`. `adk_review/judge/settings.py` reads the environment and builds nothing, so a rules-only review's
cache key and the login import no engine. The shell wins over `.env` at every entry point: ADK restores the
variables set before it loads the file, and the command loads it without overriding. The report names the model, the arm
that ran, and Vertex AI or a GitHub Enterprise tenant's host where the arm goes through one. Never read, print or quote
`.env`, a key, or the Copilot token or its folder (`~/.config/litellm/github_copilot`, or `GITHUB_COPILOT_TOKEN_DIR`),
and never run `adk-review-login`: it needs the person at a browser, and `--force` replaces their token.

## Architecture

The package is grouped by the steps of a review — `core`, `collect`, `judge`, `deliver` — and every check is one file. A
rule is one class of three kinds: a `Rule`, `check(path, line)` over each added line; a `FileRule`, `check(file)` over
each changed file whole at the head, its lines, the lines the change added, its module, and an outside tool's
diagnostics where it names the tool (`lint`, `pitfalls` and `simplify` name ruff, each with codes no other names, and
`types` names pyrefly, whose adapters `collect/tools.py` holds, run by `collect/ruff.py` and `collect/pyrefly.py`); a
`TreeRule`, `check(tree)` over the head's paths and parsed modules, naming a line where it knows one, with the lines the
change added (`tree.added`), what each import binds (`tree.imports`), the paths the change moved or removed
(`tree.removed`, `tree.imports_before`) and, where it names a tool, its diagnostics by path (`tree.diagnostics`:
`radius-types` names `pyrefly-radius`, pyrefly over the head's tree and the base's, what is new in a file or on a line
the change left alone, or on an added line another file's change breaks); the head is read whole only where a tree
rule's `needs_head(files)` or a guideline asks, `callers` asking only where a function that was there before changed its
parameters, or the change removed a name a module defined or moved or removed a module, and `radius-types` wherever the
change modified, moved or removed a Python file other than a test. A guideline is a class naming its role, a thinking
budget and, optionally, its own model per arm (`core/guidelines.py`), built into an agent by `judge/agents.py`.
`judge/catalog.py` finds every check, reads `config.toml` and resolves what runs, for the command and the chat alike. A
rule imports one door, `judge/rule_helpers/`: the contract, passed on from `core/` (`rules.py`'s kinds, `Hit` and
`Diagnostic`, `evidence.py`'s `Entry`, `findings.py`'s `Severity` and `RANK`), helpers named after what they read
(`lines.py`, `syntax.py`, `diagnostics.py`) and try a rule with (`testing.py`), and what a review reads too, passed on
from `core/`: `matches`, the skip list's matcher, a test's path, the parser `parse`, how far a name a package passes on
is followed (`PASSED_ON`), and a credential's shape. A rule is handed no test's file unless it sets `reads_tests =
True`, as each rule about what runs, leaks or steers does (`file.changed` and `tree.paths` still list every test). A
rule reads only the files `scan` names unless it sets `reads_every_kind = True`, as the two credential rules do: a key
leaks from a README as from code. A rule or a guideline whose findings change how code reads and never what it does sets
`cleanup = True`, as `simplify` and `complexity` do: the hand-off lists them under `## Tidy`, apart from the fixes, and
holds one back on lines a defect at major or above names. Ruff's findings the base held too, the same code and words on
the same line, as the diff numbers each line the change kept (`base_line`), a count no higher, are old (`Hit.old`,
`Checked.old`, `Outcome.old`): listed last under "Already in the files you touched", fixed too by the Boy Scout rule,
and never read by the verdict; the collector reads each modified Python file's base text for it (`base_texts`), and a
file not set against it keeps `Diagnostic.known` None, its untouched findings counted at the bar, as before. An S105,
S106 or S107 that covers a line the change added (`Diagnostic.last`) is the change's own whatever the base held, which
`lint` says through `listed`'s `own`: ruff's words hold the name alone, and the rule weighs the string. A rule may carry
`bad` and `good` examples (those that read an outside tool carry none), which the catalog holds it to at every start
with its own source, a rule failing either being exit 2, and a `(path, text)` example names a file the shipped `scan`
reads (`tests/test_catalog.py`); `try_rule`, `lines_hit` and `self_check` try a rule with no repository, `--list-checks`
lists every check with its kind, its profiles and its file, `--check ID` runs one check alone. Each check has one id,
`rule.<name>` or `guideline.<name>` (`core/profiles.py`), in the config, on the command line and as its own report
section; inside ADK it is `state_key`'s identifier, `rule_<name>`.

The one config file, `config.toml` beside `review.py`, holds the profiles under `[profiles]` — named lists of check ids
or the groups `rules` and `guidelines`, `default` the basics — and the review policy (`[review]` `fail_on`,
`max_tokens`, `verify`, `diff_cap`, `skip`, `scan`, `third_party`, each a flag's default), read whole so a misspelt key
is exit 2. With no `--profile`, `default` runs, and a check runs only where a profile names it, or alone with `--check
ID`. `--profile` is the only choice, and nothing is read from the repository under review, `.gitignore` included: git
keeps ignored files out of commits (under `--staged` git reads it only to name the untracked files the report lists).
What a review covers: `--path DIR` a folder of the change, the rest counted in the report; `--path-exclude DIR` a folder
or file whose changes no check reads, counted too, outweighing `--path` (a tree rule still names a line there that the
change breaks); `--all` every file at the head, diffed against git's empty tree; `--head` defaults to the branch checked
out; with neither `--base` nor `--all` a terminal asks, and a pipeline reviews the change against `main`, or `master`,
or else the branch `origin/HEAD` names.

Evidence is gathered in Python first, the rules run as one `FunctionNode` before the guidelines in both shapes
(`judge/checks.py`, every rule through `run_rule`), or straight from the driver through the same `run_rules` where no
guideline follows, and code decides the verdict. Around them: retries, a token ceiling, a `--verify` second opinion, a
JSON report a pipeline reads (`--json FILE`, schema 2, written for every decided review, under the text report's safety
rules), a comment for the branch's author (`--comment [FILE]`, by default
`comment-<repo>__<base>__<head>__<YYYYMMDD-HHMMSS>__<commit>.md` where the command runs), which the person pastes into
the pull request by hand: the reviewer posts nothing and prints no command that would, a hand-off for a coding agent
(`--handoff [FILE]`, by default `handoff-<repo>__<base>__<head>__<YYYYMMDD-HHMMSS>__<commit>.md`, named as the comment
is), which applies nothing itself, under `--all` a
Health section (`core/health.py`: what was read, source and tests apart, McCabe complexity counted as ruff's C901 counts
it, in bands graded A to E by SIG's published risk-profile rating where most of the source parsed, each rule's findings
by severity, counted and never graded; never read by the verdict), each changed file's blast radius (`core/blast.py`: an
import graph parsed from the head commit, shown to the guidelines as `{blast?}` with each call into a function the
change touched from a line it did not add (`collect/change.py`'s `callers_of`), and heading the report, never read by
the verdict), and fences around every untrusted section the guidelines, the verifiers and the chat's verdict read
(`judge/agents.py`: two marker lines carrying a nonce drawn once a process, a second one, no guideline shown it, where a
guideline's own words are read, a reminder after them, the content never escaped so a quote still grounds).

Load-bearing rules the tests enforce:

- **Loader.** `root_agent.name` must equal the folder name, `adk_review`. `adk web` uses ADK's nested loader, which
  treats any folder holding an `agent.py` (up to five levels deep) as an agent, so never name a sub-package module
  `agent.py` (the graph lives in `judge/graph.py` for this reason). `tests/test_wiring.py` asserts `adk web` lists the
  package alone.
- **Package root.** `__init__.py` imports nothing and answers `agent` lazily via PEP 562 `__getattr__`, because
  `python -m adk_review.review` imports the package before `main` reads `.env`, and `agent.py` builds its agents at
  import. `review.py` imports ADK and the judge's engine modules (agents, config, graph, plugins, run, verify) where a
  review starts, never at its top, so `--list-checks`, `--explain`, `--try`, a usage error and the pre-push hook start
  without them; a profile with no guideline runs the rules as `--try` does and imports none of them either, unless
  `--trace`, `--debug-log` or `--record`, the engine's own flags, asks for its run; a test runs those modes and such a
  review with `google` unimportable.
- **Providers.** `judge/config.py` is the only file that builds a provider's model: Gemini is a plain model string,
  Copilot is `LiteLlm("github_copilot/…")`; `judge/settings.py` only reads which arm and which names the environment
  sets. Every change must work on both arms. Judging agents run at temperature zero; a model that cannot, as LiteLLM
  says offline, is refused by `require_ready`, exit 2: Copilot's GPT models after `gpt-4.1`, `gpt-6-luna` among them,
  which reason by default and are sent no reasoning setting, and the o-series. Copilot is sent the instruction as its
  system turn (`litellm.disable_copilot_system_to_assistant`), as Gemini reads it, and LiteLLM's MCP gateway is skipped
  (`_skip_mcp_handler`): from LiteLLM 1.106.0 a call carrying a tool, as chat's intake makes, imported it, and it needs
  `mcp`, a package of LiteLLM's proxy the reviewer does not install.
- **Shape.** `agent.py` exports both `app` and `root_agent` (the loader prefers `app`, and a bare `root_agent` would
  lose the plugins). `build_app(chat=True)` adds an intake agent before the same middle and, after it, a grounding node,
  which holds the guidelines' findings to the diff and decides the verdict through the command's own functions, and a
  verdict agent that writes it up; the command uses `chat=False` over seeded state. In chat a gate after the intake goes
  on only for a scope this turn's intake confirmed — a `temp:` key, which ADK keeps for one turn — so a turn that
  confirms none ends at the intake's answer; the checks node gathers its own evidence from that scope and writes the
  change the guidelines read, where the command seeds the change at the session's start, in no event a debug log could
  carry. Plugins: `StopAtRateLimit`, which after a 429 no retry or fallback got past sends no later call of the review,
  the verifiers' included, answering each `RATE_LIMITED` (in chat, until the turn ends), then `RedactSecretsPlugin`,
  ADK's `DebugLoggingPlugin` and `Recorder` where `--debug-log` and `--record` ask, `UsageLedger`,
  `ReportProviderErrors`, and `Progress`, which times each agent and, at a terminal or with `--progress`, tells each
  guideline's start and finish on stderr, the command telling every `HEARTBEAT_SECONDS` which it still waits on, and
  `--quiet` silencing both; in chat, first, `RefuseUnready`, which asks `require_ready` once a turn, as the command asks
  before it builds anything, and while the arm cannot run answers the turn with its sentence before any agent runs, and
  after `StopAtRateLimit`, `OneTurn`, which gives each judging agent one fixed turn, `review`, as the command's read:
  ADK would hand the verdict the guidelines' findings unfenced as a turn.
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
  ADK runs each after-model hook on every streamed piece too, under `adk web`'s Token Streaming, so a hook that reads
  the answer passes a `partial` one by and reads the whole answer that follows it.

Conventions:

- Tools return a `{status: …}` dict and never raise at the model.
- Git runs by its full path from `PATH`'s absolute entries, never from the current directory (Windows searches it first,
  an empty or relative entry names it, and it can be the checkout under review), as an argv list with a timeout that
  stops the whole command (Git for Windows' `git.exe` starts the real git as its child), read-only verbs only, and
  nothing ever writes into the repository under review, not even the lazy fetch a partial clone would make, nor a fetch
  of a branch it does not hold: the message names the `git fetch` for the person to run. Every call runs in
  `_environment(repo)`: the C locale, so git's messages read as written, no inherited `GIT_*` variable but
  `GIT_INDEX_FILE`, made absolute, the index git hands a commit hook, which `--staged` reads, no global or system config
  and no attributes file but the repository's, `safe.directory` for the one repository named, no lazy fetch, no replace
  refs or grafts, `core.precomposeUnicode`, `diff.suppressBlankEmpty`, `core.fsmonitor` and `core.autocrlf` (`input`: a
  checkout Windows wrote with CRLF over blobs of LF is never diffed in place of its blobs) fixed through
  `GIT_CONFIG_COUNT`, and a call's own settings beside them there, never as `-c`; a review of commits diffs under an
  index no file holds (`NO_INDEX`), so git never reads a checked-out file for its blob, one a filter of the person's own
  config rewrites, Git LFS's or git-crypt's, among them; `--staged` reads the person's own index, where git still can. A
  file's diff is read as git prints it for no one's settings, through plumbing (`diff-tree -r -p`, or `diff-index
  --cached -p` under `--staged`): each path as `:(top,literal)` pathspec magic, the merge base's attributes
  (`--attr-source`, git 2.41; under `--all`, the empty tree's), `--no-color --no-ext-diff --no-textconv`; where a
  guideline reads a Python file the change modifies, `--function-context` with Python's own boundary as
  `diff.default.xfuncname`, each hunk within the function or class around it, and git's three lines again for a file
  that widening pushes past the diff cap or past 16,000 characters (`WIDEN_CAP_CHARS`), and for every widened file where
  the block, as it counts, leaves out a file the change keeps. Each hunk header is left bare (`unguessed`): git follows
  one with its guess at the function around the hunk, a line from above it cut at 80 bytes, and a credential there, its
  closing quote past the cut, reached the model. A call that fails, cannot start or runs past its timeout raises
  `GitError`, turned into an error envelope only where envelopes are built; never answer it as an empty result. A user's
  ref reaches git after `--end-of-options`, in `_sha` and `_branch`, or inside a pattern under refs or after
  `--disambiguate=`, so a ref spelled `-x` is never a flag. `_git(..., ok=(0, 1))` is only for calls whose exit code 1
  is an answer. `_sha` alone also reads exit 128 as no commit, git's answer for a revision it cannot follow, such as
  `main@{9}`, and only where git says nothing: an `error:` or `fatal:` line at exit 1 or 128 is a `GitError`, so a
  corrupt object is never called missing, a peel's "expected … type" line alone being an answer.
- Every string a model reads is capped in characters, with the cut named in the text, and the cut never falls inside a
  credential, and so is each title, change, reason and corrected code a model writes into a finding, cut where
  `core/findings.py` makes it. A dropped finding's file is a model's text too, cut in `drop_ungrounded` at
  `REASON_MAX_CHARS`. The report cuts each
  finding's evidence the same way, after the scrub, and breaks CI runners' command openers (`##[`, `##vso[`) and HTML
  comments' (`<!--`) in all it prints, and in prose a markdown image's or link's (`![`, `](`) and a reference a pull
  request's page acts on (`@name`, `#12`, `!12`), every character a reader cannot see printed as a space.
- Every sentence the package prints itself — a stderr line, a flag's `--help`, an error it raises or answers, a rule's
  title, fix and why — is ASD-STE100: one command, at most 20 words, no `;` joining two (`tests/test_command.py`). The
  guidelines, chat's intake and its verdict are told the same for their own words, and to quote the diff unchanged; each
  guideline and verifier is told what a `-` and a `+` line are (`DIFF_MARKS`, `judge/agents.py`), and each guideline
  that a `-` line is never a defect to report nor a line to remove, since a model read deleted lines as the code to fix,
  and a kept finding's evidence keeps the `-` of a deleted line, recorded from the diff's own marks (`Sourced.deleted`),
  which the hand-off explains to its agent (`DELETED_QUOTED`, `core/verdict.py`), the comment's readers reading diffs
  every day, and chat's verdict is shown as `(deleted)`, a finding that asks for what the branch already does then as
  `(possibly already done)`, which its verdict is told of. An error a person can act on names what to do instead, a flag
  to drop or give, a path or a command, as the owner asked of a tool that guides its user; argparse's own end by naming
  `--help`. An error's text ends bare, with no full stop, since a caller may add a sentence after it, as the pre-push
  hook adds its own; and the flags that rescope a change with nothing to compare are added by the command, from a mark
  on the error envelope (`rescope`), and those that narrow one to nothing from another (`narrowed`), since chat shares
  the collector's words and takes no flag.
- State moves between agents via `output_key` and `{placeholder}` (`{key?}` where the key may be missing). Each key
  more than one module uses is spelled once, in `judge/agents.py`; a check's key is its id through `state_key`.
- Every `subprocess.run` that decodes passes `encoding=`; no source under `adk_review/` or `scripts/` spells `/tmp/`
  (use `tempfile.gettempdir()`), and each temporary folder the package makes is removed with
  `ignore_cleanup_errors=True`: on Windows another process, a virus scan, can hold a file in it after it was read, and
  the removal raised after the work was done. Every file a script or a fixture commits is written with `newline="\n"`, a
  `.env` that is not UTF-8 is a sentence and exit 2 (1 from the login), and `.github/workflows/ci.yml` and
  `azure-pipelines.yml` run lint, the type check and the suite on Windows, Linux and macOS. Every action a workflow uses
  is pinned by its full commit SHA, the same commit in every workflow and the README's sample, and uv by one exact
  version in every pipeline and the README's, since `required-version` reads as a floor (`tests/test_copilot.py`), and
  by none newer than the pinned `setup-uv` holds a checksum for (its `known-checksums.json`, which no offline test
  reads): it checks a newer uv only against a checksum the download's own host serves.

## Tests

The suite is fully offline. `tests/conftest.py` registers a `FakeModel` in ADK's `LLMRegistry` for `fake.*`.
`load_agent(monkeypatch)` sets `REVIEW_MODEL=fake` and imports `adk_review.agent` fresh, so the real `build_model()`,
Runner, `Workflow`, callbacks and plugins all run; the `cli` fixture is the command's module loaded that way. Both run
on the Gemini arm, where the fake registers, unless a test names another: `load_agent(monkeypatch, "copilot",
"gpt-4.1")`, or `load_command` for the command, with ADK's LiteLLM client replaced, runs the Copilot arm's own path
offline (`test_the_planted_review_runs_end_to_end_on_the_copilot_arm`); a change to that path needs a test there.
LiteLLM's own routing runs only where the network alone is replaced, below LiteLLM, as
`test_a_copilot_call_carrying_a_tool_reaches_copilot` does: a call carrying a tool, which LiteLLM 1.106.0 failed, passed
every test above it. The fake answers whatever canned payload the request's response schema accepts, or a sentence when
there is no schema. The `fake_provider` fixture's `raises_when(text, exc)` / `answers_when(text, payload)` /
`calls_when(text, call)` key on text in the system instruction, to simulate a provider failing, a verifier refuting or
an agent calling a tool; `confirms(fake_provider, repo)` makes chat's intake confirm a scope with its tool, as a model
would, `Chat(module)` holds one conversation over several turns, under the `run_config` a test names, as `adk web`'s
Token Streaming does, its last turn's events in `chat.events`, and the `asked` fixture records each request as it was
sent. `demo_repo` is a session-scoped real repository from `scripts/make_demo_repo.py`, and `hostile_repo` a branch per
way a change once hid from the review. A hook's script is written with `_script` (LF ends, the execute bit) and run
through `_git_shell()`, git's own `sh`, as Git for Windows runs a hook. `asyncio_mode = "strict"`, so async tests need
`@pytest.mark.asyncio`. A test still running after `faulthandler_timeout` prints every thread's stack and ends the run.
Tests live only in `tests/`. A test is named as the sentence it proves (`test_the_gate_runs_before_any_model`), and a
helper several tests need goes into `tests/conftest.py`, never into the package.

## The docs are tested, so update them with the code

`tests/test_docs.py` reads the docs the way a reader does: every guide (this file, `GUIDE.md` and the Markdown under
`.github/`; not `README.md`, which names other repositories' paths) names only backticked paths with a folder in them
that the working tree holds; `README.md` names each module at the package's root; and
`GUIDE.md` holds every flag the parser takes and no other, the checks as `--list-checks` prints them, every JSON field
in order, and each limit the number its constant holds. A new flag, check, field or limit fails the suite until the
guide says it. The docs state no static counts of the repository's current state and carry no dates.

## Copilot's files

`.github/copilot-instructions.md` is the short form of this file for GitHub Copilot, with path-specific rules in
`.github/instructions/`, skills for the recurring tasks in `.github/skills/`, each a slash command in VS Code's chat, a
custom agent in `.github/agents/` and the cloud agent's setup in `.github/workflows/copilot-setup-steps.yml`. Copilot in
VS Code and the cloud agent read this file too, so a rule that changes here changes there too; `tests/test_copilot.py`
holds those files to their format. `.vscode/` recommends Copilot Chat beside Ruff, Pyrefly and SonarQube for IDE, has
Pylance, where it runs, show Pyrefly's type errors (Pyrefly's own extension turns Pylance off), and lets an agent run
the offline gate (`uv run ruff check`, `ruff format --check`, `pyrefly check`, `pytest`, and the `make` targets `lint`,
`typecheck`, `test` and `ci`) without asking; any command naming `.env` always asks.

Claude Code loads none of them by itself, and they hold procedures this file does not repeat. Before adding a rule,
read `.github/skills/new-rule/SKILL.md` and `.github/instructions/reviewer.instructions.md` (a rule's constants,
examples and helpers); before adding a guideline, `.github/skills/new-guideline/SKILL.md` (the tests that count the
guidelines, and the phrases that find every doc naming them); before running a review,
`.github/skills/review-a-branch/SKILL.md`; and after a change, `.github/skills/update-docs/SKILL.md` (every guide that
names what changed, and what no test of the docs reads).

## History

Commit subjects are plain-prose sentences stating what changed and what was learned.

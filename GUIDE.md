# adk-review: user's guide

The reviewer reads a Python project's git branch and judges it two ways. Its rules read Python, and other languages'
code gets the credential rules alone. Rules, decided by code, read every line the change added. Guidelines, judged by a
model, read the diff. Code decides the verdict and the exit code. Nothing is read from the repository under review but
what git holds of it — its commits, or under `--staged` its index — and nothing is written into it. `README.md`, beside
this file, says how the reviewer works; this guide is the reference, and a test holds its flags, checks, JSON fields and
limits to the code.

## First run

`README.md`'s Quick start walks these from a clone, step by step. They need uv 0.12 or later, which installs Python 3.13
itself, and git 2.41 or later on `PATH`, with the fix for CVE-2024-32465 (2.41.1, 2.42.2, 2.43.4, 2.44.1, 2.45 and
later, or a distribution's build that carries it): without it, git ignores `GIT_NO_LAZY_FETCH`, and a review of a
partial clone could fetch into it.

```bash
uv sync --locked                             # once: the reviewer, its Python and its locked dependencies
uv run python scripts/make_demo_repo.py      # a throwaway repository with planted defects; prints the review to run
uv run adk-review <that path> --head feature/payments --base main --profile demo
uv run adk-review <repo> --base main --profile default-rules    # a repository of your own: the default's rules
```

`<repo>` is the path of the repository to review, and `--head` defaults to its branch checked out: on `main` itself a
change against `main` is nothing, exit 2, and the sentence names `--head`, `--base` and `--all`. `demo`, `rules-only`
and `default-rules` call no model, so they need no key. Drop `--profile` to run the `default` profile, which adds the
guidelines: they need a model, set as "Configuration" below says. `uv run python -m adk_review.review` is the same
command spelled long.

To run it from any folder, install it as a command: `make install` in this checkout, which exports the lock and hands it
to `uv tool install --constraints`, every dependency at the version the lock holds, or with none
`uv tool install --exclude-newer P3D git+https://github.com/emrecdr/adk-review`, the `git+` being uv's own and `P3D` the
three days' cooldown the lock keeps, since `uv tool install` reads no lock: measured, the install without either took a
LiteLLM three days old or less, the window LiteLLM 1.82.7 and 1.82.8 shipped a credential stealer in. Either puts
`adk-review`, its pre-push hook, `adk-review-push`, its commit hook, `adk-review-commit`, and the Copilot login,
`adk-review-login`, on `PATH`: the reviewer alone, with ruff beside it. An installed reviewer reads the model's settings
from the shell alone, no `.env` being read outside the checkout, and its config from beside itself; inside the checkout,
`uv run adk-review` is the same command. Behind a company proxy, set `UV_SYSTEM_CERTS=true` for uv's own commands,
`uv sync` and `uv tool install`: uv trusts the roots it bundles, not the machine's, where the reviewer trusts the
machine's own store.

## Flags

| Flag | What it does |
|---|---|
| `repo` | The repository to review. A terminal asks for it when it is not given; a pipeline must give it. |
| `--head` | The branch or commit under review. Default: the branch checked out; a detached head must name one. |
| `--base` | The branch the change will merge into. With neither it nor `--all` or `--staged`, a terminal asks; a pipeline uses `main`, or `master`, or else the branch `origin/HEAD` names. In a shallow clone, a change against a base is refused unless the base is one of the head's own commits: elsewhere the merge base found can be the wrong one. |
| `--all` | Review every file at the head, not what it changes. A shallow clone serves. |
| `--staged` | Review what is staged — the index, as the next commit would hold it — against `HEAD`. Nothing is written. A change not staged is neither reviewed nor named; an untracked file is named, never a hole. Takes no `--head`, `--base` or `--all`. |
| `--path` | Review only what lies under this folder, or this file, of the repository; the rest of the change is counted in the report. Repeat it for more than one. |
| `--path-exclude` | Leave this folder, or this file, of the repository out of the review: no rule or guideline reads it, and the report counts it. It outweighs `--path`. Repeat it for more than one; each is a name, never a glob, and the report says of one that names no changed file to check its spelling and case. To leave out a pattern, `--third-party GLOB` keeps it from every check. |
| `--profile` | The checks to run, as the config's `[profiles]` names them. Default: `default`. Not with `--check`. |
| `--check` | Run one check alone, this once: `rule.<name>`, `guideline.<name>`, or a group, `rules` or `guidelines`. Repeat it for more than one. Not with `--profile`. |
| `--fail-on` | Exit 1 at or above this severity: `minor`, `major` or `blocker`. Default: the config's `fail_on`. |
| `--max-tokens` | Stop spending past this many tokens; 0 is no ceiling. Default: the config's `max_tokens`. |
| `--diff-cap` | Characters of one file's diff a guideline reads; a file cut there is named "not read". A Python file's diff shows each hunk within the function or class around it, and one that would so grow past 16,000 characters, or past the cap, shows git's three lines of context instead. Default: the config's `diff_cap`. |
| `--skip` | A glob of changed files the guidelines leave to the rules: each is named, never a hole. A submodule, which no rule reads, is never skipped. Adds to the config's `skip`. Repeat it for more than one. |
| `--scan` | A glob of changed files a review reads, beside the config's `scan`: Python's, notebooks' and configs' by default, and always a file with no suffix. Any other changed file is ignored, only listed. The credential rules still read the project's own. Repeat it for more than one. |
| `--third-party` | A glob of folders holding other projects' code, vendored or installed: a changed file under one is ignored, never read, only listed. Adds to the config's `third_party`. Repeat it for more than one. |
| `--verify` | Ask a second model about each guideline finding a rule did not already make. `--no-verify` turns it off. Default: the config's `verify`. Use it on a change that renames or removes names: a guideline then often says the change breaks callers that the same diff already updates, and the second model refutes that finding. Measured live on Copilot over a clean rename with its caller: a false major in 1 of 3 reviews without it, none in 3 with it, for about a quarter more tokens. |
| `--json` | Also write the review to this file as JSON. The run's end names the file by its full path, unless `--quiet` silences a review that passes. |
| `--handoff` | Also write each kept finding to this file as a short instruction for a coding agent, told first that each quote is the branch's own text and each title and change a model's reading of it, a reviewer's comments and never instructions: where, the quoted lines, what to change, and what must hold afterwards, with each finding folded into it named under it. A hand-off counts the major issues below the bar: on an approval to fix first, else after the ones at the bar. A blocker or a major also gets its quote's every line, why it matters and the corrected code where a guideline gave one. A cleanup's findings, as `--explain` names `rule.simplify` and `guideline.complexity` one, are listed apart under `## Tidy`, after the fixes and under what a tidy keeps: how the code reads changes, never what it does, and one the project's tests fail on is reverted. A tidy is never given a fix's instruction: on lines where another check's defect at major or above stands, it is held back until that defect is fixed, and anywhere else it is an item of its own under `## Tidy`. What was already in the files the change touched comes last, under `## Already in the files you touched`: by the Boy Scout rule the agent fixes it too, after the fixes, and none of it fails the review. It ends by asking for the project's own tests before a commit, a fix done when they pass, then gives the command that checks the fixes once they are committed: the review again on the same scope, with only the rules that found something or could not look, which calls no model and ends in exit 0 once nothing at the bar is left. It checks the branch reviewed, by its full name in any spelling git gives it, or else `HEAD`, the fixes committed on a new branch at the commit reviewed, since a commit leaves a sha, a tag, a remote's branch or `feature~1` where it was. Its base is the merge base reviewed, by its sha, which no commit, push or merge moves: a base that took in the head would hide the findings left. A base merged midway over-reports, and never approves. It repeats the `--skip`, `--scan` and `--third-party` globs the review was given, but skips no other file for the person. After a degraded review it says exit 3 means what the review could not check is still there, and that the fixes hold once each run reports no finding at the bar, kept or dropped as unproven, a check that still cannot look being a person's to check by hand. A degraded review's hand-off lists what it could not check, and where it kept no finding, its `## Check again` runs each check that could not look or prove a finding again, its head named by the commit's sha, with no fix to hold, so a hole the change itself holds needs an edit and a new review. A degraded hand-off whose only hole is files no check read ends by saying what exit 3 means. The guidelines among them, one that could not prove a finding at the bar included, are named apart, their `--check`, the head and the base to give in place of the review's profile, head and base, with no command, since checking a model's reading spends its quota. The command names `adk-review` as `uv tool install .` puts it on `PATH`; where a name in it would read otherwise in a shell, none is printed, nor where a credential's shape or a character no one sees is in it: the arguments are given instead, and each one left out is named. Nothing is applied. The run's end names the file by its full path, unless `--quiet` silences a review that passes. With no file named, it writes `handoff-<repo>__<base>__<head>__<YYYYMMDD-HHMMSS>__<commit>.md` in the current folder, named as `--comment`'s file is, and one review can write both. It opens with the lines the comment opens with: the repository by its folder's name, what the change was held against, and the scope. A finding below the bar whose suggested code the branch already holds is listed apart, under `## Possibly already done`, and the agent is told to change nothing the branch already does. |
| `--comment` | Also write the review to this file as a comment for the branch's author, to paste into the pull request by hand, whatever the verdict: first what the change caused, a section a severity, blocker, major and minor findings, the most severe first, each at the `--fail-on` bar marked must fix before it merges, an approved change's included, then its tidy-ups; then, apart, what was already in the files the change touched, by severity too; and last what the review could not check. Its first line names what ran: the profile, how many rules and guidelines, and the model, or no model. The lines below it name the repository by its folder's name, never its full path, what the change was held against and its merge base, and the scope: what `--path` kept and `--path-exclude` left out, or the whole change. Each point names its check, a guideline's as a rule's. A finding below the bar whose suggested code the branch already holds is listed apart, under Possibly already done, never counted as a major issue, and never removed: the review cannot tell for sure, so a person checks it. One at the bar stays a point to fix, since the review fails on it. A tidy-up at the bar is listed with the other points at the bar, before those below it. An approval names the bar it was held to. Every comment counts the major issues below the bar. A blocker or a major also says why it matters and, where a guideline gave one, shows the corrected code. Each place sits in a code span, and each quote in one, or in a fenced block that keeps its lines where it spans several, secrets redacted, and the whole held to 60,000 characters, under the 65,536 GitHub takes, the most severe points first and the rest counted, under the text report's safety rules. With no file named, it writes `comment-<repo>__<base>__<head>__<YYYYMMDD-HHMMSS>__<commit>.md` in the current folder, every name lower case, each branch's name without refs and the folder of its kind, heads, remotes or tags, made safe for a file name and cut at 40 characters (`all` for the base under `--all`, `head__staged` under `--staged`), the commit the first 12 characters of the one reviewed, as the comment's first line prints it (under `--staged`, of `HEAD`), and the run names it by its full path, the last of the files written, unless `--quiet` silences a review that passes. A default name can reach 166 characters: on Windows, where long paths are off, run from a short folder or name the file. Nothing is posted, and no command that would post it is printed. |
| `--cache` | Keep each decided review in this folder, keyed by the reviewer's code, config and version, the Python it runs on, the commits or, under `--staged`, every entry of the index, what of the head's tree went unread, the checks, every setting, a `--previous` report as it was read, each model and each outside tool a rule runs, ruff's and pyrefly's versions among them; the same review again is served from it, with no model call. A review with a hole is never kept, nor one with a finding its verifier could not judge. Never a folder inside the repository under review. A file in the folder is served as the review's verdict, so whoever can write the folder can write a verdict: keep it the operator's own, and in CI never restore it from a cache a job running untrusted code can write. |
| `--previous` | Compare with a previous review's JSON: mark each finding new or seen before, by its fingerprint, and list what is no longer found, among what this review looked for — a check that ran, a file in its scope — a finding folded under another still found; the comment opens with how many points are fixed, new and still open. A review with a hole resolves nothing. |
| `--progress` | Tell each phase on stderr, and every 10 seconds what the review still waits on. On at a terminal. |
| `--quiet` | Print nothing when the review passes, not even the name of a file it wrote. Print the report and any error when it does not, and name each file it wrote. For a pipeline. Not with `--progress`. |
| `--debug-log` | Write every model request and answer to this file, secrets redacted, the session's state left out. Keep the file as you keep the code it quotes. |
| `--trace` | Record ADK's spans, with their timings and token counts, to this local SQLite file. Nothing is sent anywhere. |
| `--record` | Keep each model answer in this folder under a hash of the request as the model read it, secrets redacted: a live run's answers, which a fake replays offline. |
| `--dry-run` | Say what a review would cover and run — the revisions, the files, the profile, the model and whether its arm is ready — and run nothing. |
| `--list-checks` | List every check: its id, its kind, its severity, the profiles that run it, what it looks for and its file. |
| `--explain` | Print one check whole: what it reads, its severity, for a rule whether it reads tests, whether its findings are a cleanup, its profiles, its file and, for a rule, its own examples. |
| `--try` | Run the profile's rules, or `--check`'s, over files or folders on disk, each read as wholly added: how a rule's author sees a rule fire, with no repository and no model. Takes no repository. |
| `--version` | Print the reviewer's version. |
| `--help` | Print every flag. |

`--dry-run`, `--list-checks`, `--try` and `--explain` each stop before a review would start. They exclude one
another, and each refuses a review's own flags — `--json`, `--handoff`, `--comment`, `--cache`, `--previous`,
`--record`, `--trace` and `--debug-log` — with exit 2.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Approved: no finding at or above `--fail-on`, and no hole. |
| 1 | A finding at or above `--fail-on`, and no hole. |
| 2 | A usage error, before any model call: a flag, a config, a profile or a check file that cannot run; no repository named in a pipeline, or no choice made at the question asked; a stop at the question a terminal is asked when the guidelines cannot read the change in one run; a branch, base or path that does not resolve, a change against a base in a shallow clone, or a change that adds nothing or shares no history with its base, which names `--head`, `--base` and `--all`; an arm that is not ready. A branch the repository does not hold is named with its remote copy, where an earlier fetch brought one, and the `git fetch` to run: the review fetches nothing. A name no branch can have, such as `^main` or `x.lock`, is said to be none, with no fetch. A revision of a branch only a remote holds, such as `main~1` in a pipeline's clone, is offered that remote's copy where it resolves; any other revision of a missing name asks for a commit the repository holds. Any other spelling, a full name such as `refs/heads/main`, a tag's, a pull request's or a remote's own name, gets no guess at a fetch: it is told to name one that `git branch -a` lists, since each guess gave advice that brought nothing. |
| 3 | A hole: a check failed or could not look, the token ceiling refused a guideline's call (a verifier's leaves its finding unverified), a changed file no check can read whole, or where a guideline runs one it was not shown whole, unless the skip list names it, or a finding at the bar whose quote the diff does not bear out or that a verifier refuted under `--verify`. Also an error nothing else names, a report's reader that closed the pipe early, a `--json`, `--handoff` or `--comment` file that could not be written, and a reviewer that cannot load. |
| 130 | Stopped by Ctrl-C. |
| 143 | Stopped by a job, with SIGTERM. |

A hole never approves. Exit 3 outranks a kept finding at the bar, so its verdict line names the findings at the bar
beside it. A verdict line also counts what sits below the bar, by severity, where any does, so an approval that still
holds findings says so: ``## Verdict: APPROVED, 2 major and 1 minor below `--fail-on blocker` ``. A usage error, exit 2,
says what to do instead: a flag to drop or give, a path to name, or a command to run; one argparse raises for a flag it
does not know ends by naming `--help`, whose end lists the everyday tasks.

## Checks and profiles

Every check is one file. A rule, decided by code, is in `judge/rules/`; a guideline, judged by a model, is in
`judge/guidelines/`. A profile in the config names the checks it runs, or the groups `rules` and `guidelines`, and a
check runs only where a profile names it, or alone with `--check`:

- `default`: the basics, and what runs with no `--profile`. The config must hold it.
- `default-rules`: the default's rules and no model. It needs no key and fits any project's every pull request.
- `push-history`: what the pre-push hook reads each commit a push sends with, line by line: the credential rules.
- `rules-only`: every rule but the payments demo's, and no model.
- `full`: every check but the payments demo's rules.
- `simplify`: a simplify pass, as Claude Code's `/simplify` reads a change: `rule.simplify`, the cleanup ruff can name,
  and the guidelines `complexity`, `efficiency` and `altitude`, three model calls; `lint` and `pitfalls` beside them
  name the defects a cleanup must not paper over.
- `code-review`: a code review, as Claude Code's `/code-review` reads one: the default's rules, `pitfalls` among them,
  and `simplify`, then the guidelines `correctness` and `security` and the simplify pass's three, five model calls. Its
  conventions angle is left out: it obeys the reviewed repository's own `CLAUDE.md`, a file `steering-files` names as
  one a person reads first.
- `demo`: the payments demo's: every rule, its own among them, and no model.

The payments rules, each id starting with `demo-` — `demo-money-in-cents`, and the tree rules `demo-required-paths`,
`demo-module-defines`, `demo-parameters` and `demo-derives-from` — are one small project's own, written for the demo
repository: examples of a project's rules, which a project replaces with its own and none needs as they stand; only
`demo` runs them.
`demo-derives-from` holds a class to the base its name promises — a `…Model` to `BaseModel`, an `…Error` to `Exception`,
a `…User` to `BaseUser`, a `…Repository` to `BaseRepository` — and a project names its own kinds in the rule's
`PARENTS`; a kind's own name alone, a Pydantic `User`, promises nothing, and nor does a name that only starts
with a kind's, a `ModelForm`.

A rule reads no test — a file in a folder named `tests` or `test`, or named `test_*`, `*_test` or `conftest.py` — unless
it says so, as the rules about what runs, leaks or steers do; `--explain ID` names which. The checks, as `--list-checks`
prints them:

<!-- checks: the output of `uv run python -m adk_review.review --list-checks`; a test holds it so -->
| id | kind | severity | profiles | what | where |
|---|---|---|---|---|---|
| `rule.callers` | tree rule | major | default, default-rules, rules-only, full, code-review, demo | a call does not fit the function it calls | `judge/rules/callers.py` |
| `rule.demo-derives-from` | tree rule | major | demo | a class does not derive from the parent its name promises | `judge/rules/demo_derives_from.py` |
| `rule.demo-module-defines` | tree rule | major | demo | a module no longer defines a name the project relies on | `judge/rules/demo_module_defines.py` |
| `rule.demo-money-in-cents` | line rule | major | demo | cents divided into a float | `judge/rules/demo_money_in_cents.py` |
| `rule.demo-parameters` | tree rule | major | demo | a function no longer takes a parameter its callers pass | `judge/rules/demo_parameters.py` |
| `rule.demo-required-paths` | tree rule | major | demo | a file or folder the project needs is missing | `judge/rules/demo_required_paths.py` |
| `rule.encoding-declaration` | file rule | blocker | default, default-rules, rules-only, full, code-review, demo | an encoding that rewrites the code every rule reads | `judge/rules/encoding_declaration.py` |
| `rule.hidden-text` | line rule | major | rules-only, full, demo | text a reader cannot see: tag characters, bidirectional controls or smuggled selectors | `judge/rules/hidden_text.py` |
| `rule.lint` | file rule | minor | default, default-rules, rules-only, full, simplify, code-review, demo | what ruff reports, at a severity for each code | `judge/rules/lint.py` |
| `rule.lockfile-sources` | file rule | minor | default, default-rules, rules-only, full, code-review, demo | a lockfile fetches a package from outside its public registry | `judge/rules/lockfile_sources.py` |
| `rule.named-secrets` | line rule | blocker | default, default-rules, push-history, rules-only, full, code-review, demo | a value assigned to a name that says secret | `judge/rules/named_secrets.py` |
| `rule.no-print` | line rule | minor | rules-only, full, demo | print() left in library code | `judge/rules/no_print.py` |
| `rule.pinned-actions` | file rule | major | default, default-rules, rules-only, full, code-review, demo | a workflow runs an action at a tag, a branch or a short SHA, not its full commit SHA | `judge/rules/pinned_actions.py` |
| `rule.pitfalls` | file rule | minor | default, default-rules, rules-only, full, simplify, code-review, demo | Python traps ruff can prove: code that does not do what it reads | `judge/rules/pitfalls.py` |
| `rule.private-patch` | file rule | minor | rules-only, full, demo | a test patches a private name | `judge/rules/private_patch.py` |
| `rule.radius-types` | tree rule | major | default, default-rules, rules-only, full, code-review, demo | code the change left alone no longer type-checks | `judge/rules/radius_types.py` |
| `rule.reviewer-instructions` | file rule | major | default, default-rules, rules-only, full, code-review, demo | text that tries to instruct the reviewing model | `judge/rules/reviewer_instructions.py` |
| `rule.script-injection` | file rule | major | default, default-rules, rules-only, full, code-review, demo | a workflow script expands a value an outsider writes, such as a pull request's title | `judge/rules/script_injection.py` |
| `rule.secrets` | line rule | blocker | default, default-rules, push-history, rules-only, full, code-review, demo | a hard-coded credential | `judge/rules/secrets.py` |
| `rule.simplify` | file rule | minor | rules-only, full, simplify, code-review, demo | code ruff can write more simply | `judge/rules/simplify.py` |
| `rule.steering-files` | file rule | minor | rules-only, full, demo | a file another tool or agent obeys has changed | `judge/rules/steering_files.py` |
| `rule.tests-offline` | file rule | major | rules-only, full, demo | a test reaches the network | `judge/rules/tests_offline.py` |
| `rule.types` | file rule | minor | rules-only, full, demo | a call or a value its own types say is wrong | `judge/rules/types.py` |
| `guideline.altitude` | guideline | the model's, by finding | full, simplify, code-review | a symptom patched instead of its cause | `judge/guidelines/altitude.py` |
| `guideline.complexity` | guideline | the model's, by finding | default, full, simplify, code-review | complexity the problem does not require | `judge/guidelines/complexity.py` |
| `guideline.correctness` | guideline | the model's, by finding | full, code-review | logic that does not do what the change means it to | `judge/guidelines/correctness.py` |
| `guideline.efficiency` | guideline | the model's, by finding | full, simplify, code-review | work the change wastes | `judge/guidelines/efficiency.py` |
| `guideline.security` | guideline | the model's, by finding | default, full, code-review | security defects and unsafe patterns | `judge/guidelines/security.py` |
| `guideline.tests` | guideline | the model's, by finding | default, full | untested changes, and tests that are not honest | `judge/guidelines/tests.py` |
<!-- /checks -->

A rule's severity is its findings', but `lint` and `pitfalls` give each ruff code its own, `types` each pyrefly kind its
own, and `encoding-declaration` is a hole, exit 3, rather than a finding: no rule read the code Python would run.
`--explain ID` prints one check whole. `lint` leaves a line's length, E501, to the project's formatter: it reads no
project's settings, so no width fits every project, and an operator takes it back in the rule's `IGNORE` and sets its
width in `LINE_LENGTH`, ruff's 88 when unset. It leaves `assert` out of a test file, ruff's S101 and `assert False`'s
B011, since an assert is how pytest checks; in library code each is major, since `python -O` strips it. Ruff's S105,
S106 and S107 weigh a name alone, so `lint` passes over one whose string is shorter than 8 characters or holds a space,
and is no credential as `named-secrets` weighs it: `token = "("` and `token = "Bearer "` are no finding, `token: str =
"Xk9mQ2vL8pR4"` and `client_secret = "AbCdEfGhIjKlMnOp"` are. The rules that run ruff, `lint`, `pitfalls` and
`simplify`, read none of the project's ruff settings and honour no `noqa` comment, so that no branch can silence them: a
finding there can be one the project's own ruff configuration, or its author's `noqa` with a reason, already accepts.
Read the line before you ask for a change. Ruff also lints each modified file as the base held it, and the review keeps
two groups apart. What the change caused is reviewed: its own lines, and an untouched line it broke, as an old line's
name left undefined by an import it deleted. What the base held too, the same code and ruff's same words on a line of
the same text, was already in the file: it is counted in one finding a file, listed last under "Already in the files you
touched". By the Boy Scout rule the review expects them fixed too, but they never fail it, whatever `--fail-on` says.
That finding names each code with ruff's name for it and the lines it is on, and the command that shows them. Where the
review could not set a file against its base, its findings on untouched lines are counted in one finding with the
change, at the bar, since they may be the change's own. In a Databricks notebook, a file whose first line is `#
Databricks notebook source`, `lint` does not call `spark`, `dbutils`, `display`, `displayHTML`, `sql` or `udf`
undefined: Databricks defines the six in every notebook it runs. Any other undefined name there, and those six in any
other file, are still reported. `types` runs pyrefly on each changed Python file alone, a name it imports from beyond
itself read as `Any`, and keeps what it finds on the lines the change added: a call missing an argument or given too
many by position, a major, and a missing attribute, a value of the wrong type passed or returned, or one an operation
cannot take, added, compared, indexed or iterated, a minor. No branch's ignore comment silences it, though
`@no_type_check` on a function does. It is in no default profile: name it with `--check rule.types`, or run `rules-only`
or `full`.

## Configuration

`config.toml` beside the command holds the review policy under `[review]` — `fail_on`, `max_tokens`, `verify`,
`diff_cap`, `skip`, `scan` and `third_party`, each a flag's default — and the profiles under `[profiles]`. It is read
whole: a section or a setting it does not know, one missing, one of the wrong kind, a profile that names an unknown
check or turns none on, or no `default` profile is exit 2. It is the one config: nothing of the policy is read from the
repository under review, and `--dry-run` names the file read. Another policy is a change to this file, or a flag for one
run. A glob there or in a flag reads as `.gitignore` reads one, a backslash as a slash, as Windows writes a path. An
installed command reads the copy it was installed with: installed from your own checkout with `uv tool install
--editable <checkout>`, a change there applies at once, as to `uv run`; otherwise reinstall after one. A pre-commit hook
reads the config of the commit it pins, so a policy of your own is a commit of your own fork, pinned instead. The
shipped `skip` names images and fonts, in either case, which no rule or model reads, so a branch adding a logo is not a
hole, lockfiles, which the rules read whole and no guideline judges, and protoc's generated modules (`*_pb2.py`,
`*_pb2.pyi`, `*_pb2_grpc.py`, `*_pb2_grpc.pyi`), which say not to edit them. `rule.lockfile-sources` reads where npm's,
Yarn's, pnpm's, bun's, uv's, Poetry's, PDM's, Pipenv's, Cargo's, Bundler's, Dart's, Mix's and CocoaPods' lockfiles fetch
from, and for uv's it also takes the company's own Nexus (`p-nexus-3.development.nl.eu.abnamro.com`, port 8443, over
TLS) as a known registry, so a change to `uv.lock` raises no finding for it, where another port, plain HTTP and any
other host still do; the other lockfiles the list names are read by the credential rules alone: `go.sum`,
`gradle.lockfile` and NuGet's
`packages.lock.json` name no source, Swift's `Package.resolved`, Nix's `flake.lock` and Composer's `composer.lock` name
a URL for every package, and Deno's `deno.lock` imports from URLs by design. A keystore can carry a credential, and
`scan` names it, so it stays a hole until the list names it too; an archive or a document is no file `scan` names, and
is ignored. A text file a glob names is still read by the rules, as any file the review reads is.

A review reads the change between two local branches, and of it only the files `scan` names — Python's and notebooks,
the configs beside them (TOML, CFG, INI, YAML, JSON, `.env`, requirements, lockfiles, Dockerfiles), a credential's own
files, keys and key stores, and what a coding agent or another tool obeys, each file `rule.steering-files` names — and
every file with no suffix, which a script that runs Python can be, as only its first line tells, and a submodule, a hole
no rule can read. It never reads a file under a folder `third_party` names, other projects' code, vendored or installed:
those named `vendor`, `third_party`, `_vendor`, `node_modules`, `site-packages`, `.venv` and their like, and `venv` at
the top alone, a name a project's own package has too. Any other changed file is ignored: no guideline reads it, no tree
parses it, and the report lists it, never a hole. Only the two credential rules, `secrets` and `named-secrets`, still
read the project's own files of every kind, in a review and in each commit a push sends: a key leaks from a README or a
script as from code, and a link among them as its own path, as its diff shows it: a key in the file it names is found
where the review reads that file's own change. No rule reads another project's code; a change of nothing else is
approved, in chat as by the command. A file the change moves out of an ignored one is read whole, as one it adds: no
review read it where it lay. Nor does a review read the environment it runs in: only the repository's own commits. Code
a branch puts in an ignored file goes unreviewed too, but for its credentials.

The model is set in the environment, or in the `.env` at the root of the reviewer's checkout; a variable set in the
shell wins over the file:

| Variable | What it sets |
|---|---|
| `REVIEW_PROVIDER` | The arm: `copilot` or `gemini`. Default: `copilot`. |
| `REVIEW_MODEL` | The model. Blank: `gpt-4.1` on Copilot, `gemini-2.5-flash` on Gemini. Google serves `gemini-2.5-flash` only to those who have used it before, and names newer Flash models for new projects; it says its Gemini 3 models may loop or lose quality below temperature 1.0, and every review runs at 0, so try one on the demo before you rely on it. GitHub has deprecated `gpt-4.1` in Copilot: it answers while Copilot serves it, under per-user rate limits GitHub does not publish, so name a model the seat's plan serves. On the Gemini arm, a model that is not Gemini's, `gpt-*` or `claude-*`, is refused, exit 2; on Copilot, one that cannot judge at temperature 0, as LiteLLM says before any call: each of Copilot's GPT models after `gpt-4.1`, `gpt-6-luna` among them, which reason by default and are sent no reasoning setting, and the o-series. |
| `REVIEW_VERIFIER_MODEL` | The model `--verify` asks. Blank: `REVIEW_MODEL`. |
| `REVIEW_<ID>_MODEL` | One guideline's model, by its id: `REVIEW_SECURITY_MODEL`. Blank: the guideline file's `models` for the arm, else `REVIEW_MODEL`. |
| `REVIEW_FALLBACK_MODEL` | A second model behind every role's first, tried on a 429, a 5xx or a timeout, never behind a role whose own model it is. Blank: none. |
| `GOOGLE_API_KEY` | The Gemini arm's key. |
| `GOOGLE_GENAI_USE_ENTERPRISE` | `true` sends the Gemini arm through Vertex AI. |
| `GOOGLE_CLOUD_PROJECT` | Vertex AI's project, signed in with Application Default Credentials (`gcloud auth application-default login`); no `GOOGLE_API_KEY` is needed, a key being Vertex AI's express mode. Blank: the project the credentials name, gcloud's own or a service account's, as on Cloud Run or GKE. |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI's region. Blank: `global`. |
| `GITHUB_COPILOT_TOKEN_DIR` | Where the Copilot login caches its token, and LiteLLM reads it; a `~` is expanded for both. Default: `~/.config/litellm/github_copilot`. |
| `GITHUB_COPILOT_ACCESS_TOKEN_FILE` | The token's file in that folder. Default: `access-token`. |
| `GITHUB_COPILOT_API_KEY_FILE` | The file in that folder where LiteLLM caches the short-lived Copilot key the token buys. Default: `api-key.json`. |
| `GITHUB_COPILOT_API_BASE` | A GitHub Enterprise tenant's own Copilot endpoint. Blank: github.com's. LiteLLM sends the calls to the endpoint the cached API key names before this one, and the report names the host they go to, never github.com's own. |
| `GITHUB_COPILOT_DEVICE_CODE_URL`, `GITHUB_COPILOT_ACCESS_TOKEN_URL`, `GITHUB_COPILOT_API_KEY_URL` | A `*.ghe.com` tenant's login and key endpoints, as `.env.example` shows. Blank: github.com's. |
| `GITHUB_COPILOT_CLIENT_ID` | The OAuth app the login asks GitHub's device flow for. Blank: the one LiteLLM uses. |
| `SSL_CERT_FILE` | A PEM bundle of roots to trust beside the operating system's, for a proxy whose root the machine does not hold. |
| `LITELLM_LOCAL_MODEL_COST_MAP` | Keeps LiteLLM to the price list its pinned version ships, never the network's. Set by the reviewer when unset. |

Copilot is the arm this reviewer targets and the default; Gemini is first-class too. The Copilot arm needs a GitHub
account whose plan lets it choose a model, Copilot Free and Student choosing for themselves, and a one-time
`uv run adk-review-login`, or `adk-review-login` once installed: it prints a code to enter at GitHub in a browser, then
caches a token readable by the account alone. Calls are billed to that seat, as its plan bills Copilot chat. A GitHub
Enterprise tenant with its own endpoints sets them in `GITHUB_COPILOT_API_BASE` and the login's `GITHUB_COPILOT_*_URL`
variables, as `.env.example` shows. Certificates are verified through the operating system's own store, so a company
proxy's root the machine trusts, the reviewer trusts too.

What a Copilot review can say, and what to do:

- `REVIEW_PROVIDER=copilot needs a cached token at …`, exit 2: run `adk-review-login` (`uv run adk-review-login` in the
  checkout).
- `REVIEW_MODEL=… cannot judge at temperature 0`, exit 2: name another model.
- `model_not_supported`, under the guideline it stopped, exit 3: the plan or the organisation's Copilot policy leaves
  that model off, and the report says to name one it serves, in `REVIEW_MODEL` or the variable that chose it.
- A 401 from Copilot, exit 3: the token was revoked or the seat lost Copilot; run `adk-review-login --force`.
- A 429, exit 3: Copilot's per-user rate limit, which GitHub does not publish; wait, or set `REVIEW_FALLBACK_MODEL`.
  Where Copilot names when it clears, the report says when to try again. No
  call of the review after it is sent, each named `RATE_LIMITED`, since the limit lasts hours and a review with a hole
  is run again.
- `CERTIFICATE_VERIFY_FAILED`: a proxy's root the machine does not trust; set `SSL_CERT_FILE` to a PEM bundle that holds
  it.

## The report

The report prints, in order: the title; the verdict, with the findings at the bar beside it and what sits below the bar
counted by severity; a table of the kept findings, the most severe first, up to 30 and the rest counted, with its place,
its title and each check that found it, counted where one found the lines more than once; what was reviewed — the
repository, the branch and its base with their commits, the merge base, the model where a guideline ran, the profile and
every check it left off, the scope, the changed files, and each file not read, skipped or untracked; under `--all`, the
project's health; the blast radius; a section for each check that found something, with its findings, their evidence and
fix, each other reading of the same lines, by another check or the same one, under the finding it repeats with its own
place, a count of its own listed under another finding, or that it failed, and why, then one line naming the checks that
found nothing; then what a previous review found and this one no longer does (with `--previous`), the findings dropped
for a quote the diff does not bear out, how many it flagged possibly already done, what the verifier refuted or could
not judge (with `--verify`), how many duplicates were folded, how many secrets were redacted, and the spend; then the
next steps, by the verdict and what was kept: that a degraded review needs what it could not check checked by hand, what
to fix, then to commit and review that commit (to stage and run the same command again, under `--staged`), what is worth
a look, and the flags that write the findings as a comment or for a coding agent, or that there is nothing to fix. Every
string the branch wrote is made safe to print: no credential, no control character or other character a reader cannot
see, no CI runner's command, no HTML comment, and no HTML tag outside a code span. Progress goes to stderr, never into
the report. Under `--quiet` a review that passes prints nothing, and one that does not prints the whole report and names
each file it wrote.

A guideline's quote shows each line as the head holds it, except a line the change deleted, which keeps its `-`, as a
diff prints it: the code a deleted line shows is gone from the branch. Where a finding quotes a line that starts with
`-`, read the line beside it that replaced it before you ask for a change. A line of the file that itself starts with
`-`, a YAML list item at the left edge, reads the same way, so check it against the diff. A comment and a hand-off
whose quotes hold a deleted line say what the `-` means, for the person or the coding agent that reads them, and
chat's verdict is told to say so where it quotes one.

## The JSON report

`--json FILE` writes the same review as data, whenever a review is decided, so never on exit 2. It is written beside
the path and moved into place, the operator's alone; an earlier file of that name is removed before the review. Its
strings are as safe as the text report's, and plain text: a reader that renders one as markdown or HTML escapes it.

| Field | What it holds |
|---|---|
| `schema` | The format's version. It rises when a field changes its meaning or goes. |
| `verdict` | `APPROVED`, `REQUEST_CHANGES` or `DEGRADED`. |
| `exit_code` | The command's exit code. |
| `fail_on` | The bar. |
| `findings_at_fail_on` | How many kept findings sit at or above the bar. |
| `reviewed` | The repository, the head and its sha, the base, its sha and the merge base (null under `--all`), the scope, what `--path-exclude` left out, the files the skip list left to the rules, the files the review ignored, by `scan` and `third_party`, the untracked files under `--staged` (null otherwise), and the changed files. |
| `model` | The model and arm that ran, or null where no guideline ran. |
| `profile` | The profile, or the `--check` list. |
| `off` | Every check the profile left off. |
| `checks` | Every check that ran, in report order. |
| `summary` | How many kept findings sit at each severity. |
| `health` | Under `--all`, the project's measures: files read whole, source and tests in files and lines, maintainability's grade (A to E, SIG's rating of the complexity profile, null with why where under ten or under 90% of the source files parsed), the source's function lines by McCabe complexity band, counted as ruff's C901 counts it, the source files that do not parse, the most complex functions, and each rule's findings in source and in tests by severity. Null for a review of a change. Security and correctness are counted, never graded; the verdict never reads any of it. |
| `findings` | Each kept finding: its fingerprint, check, file, line, the lines the diff places it on, whether one of them is a line the change added (null for a finding with no line), severity, title, evidence and suggestion, and `already_held`: whether its suggested code is what replaced the deleted lines it quotes, so the change may have done it already. Such a finding is kept and decided on as any is; below the bar, and only where every finding on its lines is so, it is listed apart in the comment and the hand-off. A guideline's finding's evidence is the lines of the diff its quote stands on, as the file reads them, a line the change deleted with its `-`. A fingerprint is built from the check, the file and the evidence, never the line, so a finding keeps it when lines move. With `--previous`, `since_previous` says `new` or `seen`. |
| `old_findings` | Each finding already in the files the change touched and not caused by it: ruff's findings the base held too, the same code and ruff's same words on a line of the same text, on lines the change did not touch. Listed apart, as in the comment and the hand-off, and never at the `--fail-on` bar. |
| `resolved` | With `--previous`, the earlier findings no longer found, as that report wrote them; null without it, or where this review has a hole. |
| `could_not_look` | Each check that failed or could not look, and why. |
| `not_read` | Each changed file no check can read whole, or where a guideline runs one it was not shown whole, unless the skip list names it, and why. |
| `folded` | Each finding folded into another on the same lines, and the check it folded into. |
| `dropped` | Each finding dropped for a quote the diff does not bear out. |
| `dropped_at_fail_on` | How many of those sit at or above the bar. |
| `verified` | Whether `--verify` ran. |
| `refuted` | Each finding a verifier refuted, and why. |
| `refuted_at_fail_on` | How many of those sit at or above the bar: each holds the verdict back, as a dropped one does. |
| `unverified` | Each finding a verifier could not judge, and why. |
| `secrets_redacted` | How many secrets were redacted before any model read them. |
| `usage` | Model calls completed and attempted, tokens, the prompt tokens the provider served from its cache (`cached_tokens`), the ceiling, and each agent's row: a guideline under its check's id, a verifier under its own name. |
| `seconds` | How long each agent took: a guideline under its check's id, a verifier under its own name. A guideline skipped because the change holds no file it reads has none, and so has an agent the run's deadline cut short, named under `could_not_look` or `unverified`. |
| `reviewer_version` | The reviewer's version. |

## Limits

| What | Limit | Where |
|---|---|---|
| One file's diff a guideline reads, unless `diff_cap` or `--diff-cap` names another | 90,000 characters | `collect/diff.py`, `DIFF_CAP_CHARS` |
| One Python file's diff read within the function around each hunk, past which it is read as git prints it | 16,000 characters | `collect/diff.py`, `WIDEN_CAP_CHARS` |
| All the diffs the guidelines read | 250,000 characters | `collect/change.py`, `BLOCK_CAP_CHARS` |
| The most complex functions the health section names | 5 | `core/health.py`, `WORST_SHOWN` |
| The findings the table under the verdict shows, the most severe first | 30 | `deliver/report.py`, `TABLE_ROWS` |
| The comment for the branch's author, the most severe points first and the rest counted | 60,000 characters | `deliver/report.py`, `COMMENT_MAX_CHARS` |
| The other readings of one point's lines the comment names, the rest counted | 5 | `deliver/report.py`, `ALSO_SHOWN` |
| Each name in the file names `--comment` and `--handoff` make, repository and branches alike | 40 characters | `review.py`, `COMMENT_NAME_CHARS` |
| Python source files parsed before maintainability is graded | 10 | `core/health.py`, `FILES_AT_LEAST` |
| How often a person at a terminal is told what the review still waits on | 10 seconds | `review.py`, `HEARTBEAT_SECONDS` |
| Ignored files the chat verdict names; it counts the rest | 20 | `judge/agents.py`, `IGNORED_NAMED` |
| The steps the blast radius takes in all, one for each cycle of imports a walk reaches: past them, a radius is not counted, and says so | 2,000,000 steps | `core/blast.py`, `RADIUS_STEPS` |
| One file at the head the rules and the radius read, and one file `--try` reads | 1,000,000 bytes | `collect/git.py`, `MAX_FILE_BYTES` |
| The start of a file a link names in which a NUL makes it binary, as git reads one | 8,000 bytes | `collect/git.py`, `BINARY_BYTES` |
| The head's Python files read for the tree rules and the radius, the changed ones first, in all; every link at the head, the changed ones first, read to follow a changed link, in all; and the changed files' texts, in all | 20,000,000 bytes | `collect/git.py`, `MAX_TREE_BYTES` |
| The links one symbolic link is followed through, as Linux follows them: past them, it names no file | 40 links | `core/paths.py`, `LINK_HOPS` |
| The parts of a path a review follows its links through, in all: past them, a link not yet followed is a hole for a rule that reads it whole and for each line rule | 2,000,000 parts | `core/paths.py`, `LINK_STEPS` |
| One git call | 30 seconds | `collect/git.py`, `GIT_TIMEOUT_S` |
| One ruff run | 60 seconds | `collect/ruff.py`, `RUFF_TIMEOUT_S` |
| One pyrefly run | 60 seconds | `collect/pyrefly.py`, `PYREFLY_TIMEOUT_S` |
| Pyrefly's answer the rules read: past it, none of it is read, a hole | 64,000,000 bytes | `collect/pyrefly.py`, `PYREFLY_ANSWER_MAX_BYTES` |
| The findings one ruff run names that the rules read: past them, a hole | 500,000 findings | `collect/ruff.py`, `RUFF_FINDINGS_MAX` |
| One model call, every retry Copilot's client makes of it included | 180 seconds | `judge/config.py`, `REQUEST_TIMEOUT_S` |
| The longest wait a rate limit's header is read as naming, past which the report names none | 604,800 seconds | `judge/plugins.py`, `WAIT_MAX_S` |
| One run of the guidelines, or of the verifiers | 600 seconds | `judge/config.py`, `RUN_DEADLINE_S` |
| Model calls in flight at once | 3 | `judge/config.py`, `CONCURRENCY` |
| The radius a guideline is shown | 20,000 characters | `judge/agents.py`, `BLAST_CAP_CHARS` |
| The calls into what the change touched a guideline is shown beside the radius | 12,000 characters | `judge/agents.py`, `CALLERS_CAP_CHARS` |
| The calls into one changed function a guideline is shown, a call from each file before a second | 3 | `collect/change.py`, `CALLS_SHOWN` |
| The lines shown around each of those calls, each way | 3 | `collect/change.py`, `CALL_CONTEXT` |
| Every check's findings chat's verdict reads, grounded and decided | 20,000 characters | `judge/agents.py`, `FINDINGS_CAP_CHARS` |
| The changed-files list a model reads in chat | 20,000 characters | `judge/tools.py`, `LISTED_MAX_CHARS` |
| A tool's findings listed for one file by a rule that names ruff or pyrefly, or pyrefly over the radius; the rest are counted in one finding | 50 | `judge/rule_helpers/diagnostics.py`, `PER_FILE` |
| A finding's evidence in the report | 400 characters | `core/text.py`, `EVIDENCE_MAX_CHARS` |
| A reason in the report: why a check failed, a verifier's word, a provider's error, a dropped finding's file as a model wrote it | 400 characters | `core/text.py`, `REASON_MAX_CHARS` |
| A finding's title, as a model wrote it | 200 characters | `core/text.py`, `TITLE_MAX_CHARS` |
| A finding's change, as a model wrote it | 1,200 characters | `core/text.py`, `SUGGESTION_MAX_CHARS` |
| A blocker's or a major's reason, as a model wrote it | 600 characters | `core/text.py`, `WHY_MAX_CHARS` |
| A blocker's or a major's corrected code, as a model wrote it | 1,200 characters | `core/text.py`, `CODE_MAX_CHARS` |

A file past a guideline's cap is named under "not read", and the review ends in exit 3: a hole, never "nothing
found". `--path` narrows a review to fit. A file past all the diffs the guidelines read names the `--path` of the
planned run that reads it, one that fits. A branch too large for one run is reviewed in parts, each with its own
verdict: its dry run prints them, ready to copy, the guidelines a part at a time, then the rules over the whole change.
Each part is named in the fewer flags: its own folders as `--path`, or what it leaves out as `--path-exclude`, the other
parts and any changed file no part names, a skipped one aside. On a tie it keeps `--path`. Under the default profile
they read so:

```
adk-review <repo> --head <branch> --base develop --dry-run
adk-review <repo> --head <branch> --base develop --check guideline.security --check guideline.tests --check guideline.complexity --path-exclude=tests --comment
adk-review <repo> --head <branch> --base develop --check guideline.security --check guideline.tests --check guideline.complexity --path=tests --comment
adk-review <repo> --head <branch> --base develop --profile default-rules --comment
```

The review plans the parts itself, before any model call, from what each file takes of the diffs the guidelines read: a
folder whole where it fits, else its folders and files one level down. `--dry-run` prints each run as a command to copy:
this review's guidelines alone, with its repository, branches, globs and a `--diff-cap` it was given, since the plan is
made at that cap. Then it prints one run of the review's rules over the whole change, in the review's own `--path`, with
no model call: the rules read files no part reads, a skip-listed lockfile or a README, and each rule's finding is
reported once. Give each run any other flag the review had, but no `--path`, `--profile` or `--check`, and give each its
own `--json`, `--comment` or `--handoff` file. A `--max-tokens` ceiling holds for each run alone, so parts spend up to
that much each. Each part's guidelines are told how many changed files lie outside its folders, so a test there is no
test missing.
A review prints the same before its first model call. At a terminal it asks whether to go on, and a stop is exit 2;
a pipeline goes on, and the files it cannot read are a hole, exit 3.

Or leave a folder's files to the rules, as the skip list does: `--skip "tests/**/testing_tools/**"`. With no guideline
on, a cut is no hole: the cap is the guidelines' limit, and the rules read every added line.

A changed file's diff is read whole, whatever its size: a file past `MAX_FILE_BYTES` is still read as far as its diff
shows it. That costs memory about 25 times the file's size. Measured: a branch adding a 112 MB Python file took 22.5 s
and 2.9 GB before the review ended in exit 3. The pre-push hook read the commit again: 3.3 s and 2.2 GB at its peak.
GitHub refuses a file over 100 MB. `--path` narrows a review by hand around a large file.

## Before a push

`adk-review-push`, installed beside `adk-review`, is a git pre-push hook. To install it, run these lines in the
repository to guard, in any shell git brings (Git Bash on Windows):

    hooks="$(git rev-parse --path-format=absolute --git-path hooks)"
    mkdir -p "$hooks"
    [ ! -e "$hooks/pre-push" ] || grep -q adk-review-push "$hooks/pre-push" ||
      mv "$hooks/pre-push" "$hooks/pre-push.before-adk-review"
    cat > "$hooks/pre-push" <<'HOOK'
    #!/bin/sh
    lines="$(cat)"
    printf '%s\n' "$lines" | adk-review-push "$@" || exit
    before="$(dirname "$0")/pre-push.before-adk-review"
    if [ -x "$before" ]; then printf '%s\n' "$lines" | "$before" "$@"; fi
    HOOK
    chmod +x "$hooks/pre-push"

They write the hook where git reads it: `.git/hooks`, unless `core.hooksPath` names another folder, and from a linked
worktree its main repository's. A pre-push hook already there, Git LFS's say, is kept beside it as
`pre-push.before-adk-review` and runs after the review passes, given git's lines too: replaced, Git LFS's would upload
no stored file, and a clone could not fetch one. Run again over their own hook, the lines change nothing; over one
another tool wrote since, they keep that one in place of the first. A tool that manages hooks may write that folder
anew, husky at each install, this one's hook with it: add the line `adk-review-push "$@"` to the pre-push hook it runs
instead. Git passes over a hook it cannot run, and over one in a folder it does not read, and the push goes unreviewed.
A tool that runs hooks for git must hand this one git's lines on its standard input: given none, it reads a push that
sends nothing.

A repository that uses pre-commit names the hook `adk-review` from this repository's `.pre-commit-hooks.yaml`
instead, at a pinned commit, with pre-commit's pre-push hook installed (`pre-commit install --hook-type pre-push`); it
runs the same command, and takes no `args:`, which stop every push. pre-commit installs the reviewer with the Python
pre-commit itself runs on, and the reviewer needs 3.13 or newer: with an older one the install fails, and so does the
push. That config names another beside the hook's id, `language_version: python3.13`.

For each ref a push sends, the hook asks git which commits the remote does not hold yet and reviews them as one change
with `default-rules`, against the commit they grow from on the remote's side, or with `--all` where the remote holds
none of their history. Those rules read the whole head only where the change alters the parameters of a function that
was there before, removes a name a module defined, or moves or removes a module, for `rule.callers`, which holds every
call to the function and every file that still takes the name or imports the module, and wherever the change modifies,
moves or removes a Python file other than a test, for `rule.radius-types`, which runs pyrefly over the head and the
base: measured, a push's review took 0.6 s more on this repository; on ADK's 815 files, 0.9 s more where `rule.callers`
read the head anyway and 1.8 s where it did not; on litellm's 2,776, 2.4 s and 4.9 s; otherwise they read the changed
files alone. A merge grows from more than one: the change is reviewed against each, and passes when one review passes,
since a line the remote holds nowhere is new to every one of them; where every review stops, the push stops only on what
all of them found, since what one side brings the remote holds already, or where one review's reasons were cut in its
JSON or named none. A review interrupted, Ctrl-C's 130 or a job's 143, or one that wrote no report, stops the push at
once, and an interrupted one ends the hook with its own code and one sentence. Then the rules `push-history` names, the
credential rules `secrets` and `named-secrets`, read each of those commits as it was made, and what each says, a pushed
tag's words too, those of each tag it was made of, and those of a signed tag a merge carries in its own header: a
credential one commit adds and a later one takes out stops the push, though the change as a whole no longer holds it. A
commit is read line by line, so the profile names line rules alone, and a commit a push sends under two names, a branch
and its tag, is reviewed and read once. Any exit but 0 stops the push and prints why; so does a `git` the hook cannot
find, or a line from git it cannot read. A push that sends nothing new, or commits that change nothing, says nothing,
and so does a push to this repository itself, `git push . a:b`, which sends nothing off the machine. Through pre-commit
by hand, `pre-commit run adk-review --hook-stage pre-push --from-ref A --to-ref B` reviews that range and its commits,
and with neither ref, the whole project at `HEAD`. Run alone at a terminal, the hook says what it is, exit 2. `git push
--no-verify` skips it.

What the hook cannot see, or stops on:

- pre-commit reads git's lines itself and hands its hook one ref: the first not deleted, passing over a new ref whose
  commits the remote already holds. A push of several refs is reviewed whole by git's own hook alone.
- In a commit before the last, only a credential stops the push, or a file no rule can read, a binary one or a Git LFS
  pointer, whose file Git LFS's own hook uploads, unless `[review] skip` names it. A file a commit moves is read whole
  there, as one it adds. What a merge's own resolution adds and a later commit takes out is read in no commit.
- The remote's branches are as this clone last fetched them: one deleted or rewritten there, or held by the server a
  changed URL named before, still counts as held here, so fetch before a push that follows a rewrite or a new URL, and
  let a fetch prune deleted ones (`git config fetch.prune true`). Where git says the remote holds no branch this clone
  last saw there, the push stops until `git fetch --prune` brings the clone's picture back to the remote's. With a
  `pushurl` naming a server apart from the one fetched, no fetch could, so the push does not stop, and what the fetched
  server holds counts as held on the other. Pushed to a URL rather than a remote's name, no branch of it is known here,
  and a new ref is reviewed as a whole project.
- A file under a folder `[review] third_party` names is read in no commit: a credential there passes. A file `scan`
  leaves out is read there by the credential rules alone, and a binary one is no hole.
- A file no rule can read — a binary one, a submodule, a Git LFS pointer, whose content is stored elsewhere — is a hole
  and stops the push, unless `[review] skip` in `config.toml` names it. A review of commits reads each file as its
  commit holds it, a Git LFS file as its pointer, whatever is checked out. Under `--staged`, git can read a file a
  filter of your own config rewrites, Git LFS's or git-crypt's, from disk. Name it in the skip list, as the shipped list
  names images and fonts ("Configuration" above): then the report names it as skipped, and it stops nothing. A text file
  past the size limits above, a lock file say, is read as far as its diff shows it, which is all `reviewer-instructions`
  reads; a Python file past them is a hole for `lint`, `pitfalls` and `encoding-declaration`, which read a file whole,
  and stops the push.
- A symbolic link is read as the file it runs, followed a part at a time as a system follows it, through links to
  folders and links to links. A path out of the repository is read as coming back in, at every depth the repository
  could lie at: through the checkout's own folder, by whatever name a clone gave it, or into a copy deployed from the
  top of the file system, at `/app` say. A name spelled in another case or Unicode form than git holds it is read as a
  file system blind to them finds it, as macOS's and Windows' are. One naming anything inside a submodule, a name such a
  system finds more than one spelling of, more than one file as where a copy lies decides, or one whose way could not be
  followed, is a hole for a rule that reads it whole and for each line rule, which reads its lines, and stops the push;
  so is one whose file was not read, past the size limits or a blob a partial clone lacks. One naming a binary file or a
  Git LFS pointer is named not read, as that file is, and stops the push, unless `[review] skip` names the link. A link
  of a kind `scan` leaves out is read by the credential rules as its own path, and stops nothing. One naming no file, a
  folder outside any submodule say, holds nothing to run, and stops nothing. Known limits: a link to a file the same
  change adds past the size limits is a hole, though that file's own lines were read; a `.py` link is parsed under its
  own name by no tree rule and counted by no blast radius, even one to a `.py` file.
- A repository with no `.git` folder of its own, a bare one or one used through `--git-dir`, and a tag of a tree or a
  blob, stop the push: the hook reviews a working tree's commits.

## Before a commit

`adk-review-commit`, installed beside `adk-review`, is a git pre-commit hook: what a commit holds, reviewed with
`default-rules`, no model and no key, before git writes it, so a key never enters even this clone's history. To install
the commit hook, run these lines in the repository to guard, in any shell git brings (Git Bash on Windows):

    hooks="$(git rev-parse --path-format=absolute --git-path hooks)"
    mkdir -p "$hooks"
    [ ! -e "$hooks/pre-commit" ] || grep -q adk-review-commit "$hooks/pre-commit" ||
      mv "$hooks/pre-commit" "$hooks/pre-commit.before-adk-review"
    cat > "$hooks/pre-commit" <<'HOOK'
    #!/bin/sh
    before="$(dirname "$0")/pre-commit.before-adk-review"
    if [ -x "$before" ]; then "$before" "$@" || exit; fi
    adk-review-commit
    HOOK
    chmod +x "$hooks/pre-commit"

They write the hook where the pre-push hook's lines write theirs, and keep a pre-commit hook already there beside it as
`pre-commit.before-adk-review`, run first, so what it stages is reviewed too; one that fails stops the commit before the
review runs. The review reads the index git hands its hook, as `--staged` reads it, against `HEAD`, or the empty tree
before a first commit: `git commit -a` and `git commit <paths>` stage into an index of their own, and the review reads
that one. A commit that adds nothing to review, a message-only `--amend`, passes; any other exit but 0 stops the commit
and prints why, a file no rule can read among them. Ctrl-C or a job's SIGTERM, in the review or before it, ends the hook
as it ends the command: one sentence, and 130 or 143. `git commit --no-verify` skips it, as it skips any hook. It costs
what a push's review costs, at each commit. A repository that uses pre-commit names the hook `adk-review-commit` from
this repository's `.pre-commit-hooks.yaml`, with pre-commit's own hook installed (`pre-commit install`).

## More

- How a review runs, the layout and the design: `README.md` beside this file.
- In a pipeline: "In a pipeline" in `README.md`, a recipe tried on a simulated checkout.
- Your own checks: "Write a rule" and "Write a guideline" in `README.md`.

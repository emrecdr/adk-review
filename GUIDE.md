# adk-review: user's guide

The reviewer reads a git branch and judges it two ways. Rules, decided by code, read every line the change added.
Guidelines, judged by a model, read the diff. Code decides the verdict and the exit code. Nothing is read from the
repository under review but what git holds of it — its commits, or under `--staged` its index — and nothing is
written into it. `README.md`, beside this file, says how the reviewer works; this guide is the reference, and a test
holds its flags, checks, JSON fields and limits to the code.

## First run

```bash
uv sync --locked                             # once: the reviewer, its Python and its locked dependencies
uv run python scripts/make_demo_repo.py      # a throwaway repository with planted defects; prints its path
uv run adk-review <that path> --head feature/payments --base main --profile rules-only
uv run adk-review <repo> --base main --profile default-rules    # a repository of your own: the default's rules
```

`<repo>` is the path of the repository to review, and `--head` defaults to its branch checked out. `rules-only` and
`default-rules` call no model, so they need no key. Drop `--profile` to run the `default` profile, which adds the
guidelines: they need a model, set as "Configuration" below says. `uv run python -m adk_review.review` is the same
command spelled long.

To run it from any folder, install it as a command: `uv tool install <path or git URL of this repository>` puts
`adk-review`, its pre-push hook, `adk-review-push`, and the Copilot login, `adk-review-login`, on `PATH`: the reviewer
alone, with ruff beside it. An installed reviewer reads the model's settings from the shell alone, no `.env` being read outside the checkout, and its
config from beside itself; inside the checkout, `uv run adk-review` is the same command.

## Flags

| Flag | What it does |
|---|---|
| `repo` | The repository to review. A terminal asks for it when it is not given; a pipeline must give it. |
| `--head` | The branch or commit under review. Default: the branch checked out; a detached head must name one. |
| `--base` | The branch the change will merge into. With neither it nor `--all` or `--staged`, a terminal asks; a pipeline uses `main`, or `master`, or else the branch `origin/HEAD` names. A change against a base is refused in a shallow clone, where the merge base found can be the wrong one. |
| `--all` | Review every file at the head, not what it changes. A shallow clone serves. |
| `--staged` | Review what is staged — the index, as the next commit would hold it — against `HEAD`. Nothing is written. A change not staged is neither reviewed nor named; an untracked file is named, never a hole. Takes no `--head`, `--base` or `--all`. |
| `--path` | Review only what lies under this folder, or this file, of the repository; the rest of the change is counted in the report. Repeat it for more than one. |
| `--profile` | The checks to run, as the config's `[profiles]` names them. Default: `default`. Not with `--check`. |
| `--check` | Run one check alone, this once: `rule.<name>`, `guideline.<name>`, or a group, `rules` or `guidelines`. Repeat it for more than one. Not with `--profile`. |
| `--fail-on` | Exit 1 at or above this severity: `minor`, `major` or `blocker`. Default: the config's `fail_on`. |
| `--max-tokens` | Stop spending past this many tokens; 0 is no ceiling. Default: the config's `max_tokens`. |
| `--diff-cap` | Characters of one file's diff a guideline reads; a file cut there is named "not read". Default: the config's `diff_cap`. |
| `--skip` | A glob of changed files the guidelines leave to the rules: each is named, never a hole. A submodule, which no rule reads, is never skipped. Adds to the config's `skip`. Repeat it for more than one. |
| `--verify` | Ask a second model about each guideline finding a rule did not already make. `--no-verify` turns it off. Default: the config's `verify`. |
| `--json` | Also write the review to this file as JSON. |
| `--handoff` | Also write each kept finding to this file as a short instruction for a coding agent: where, the quoted lines, what to change, and what must hold afterwards, with each finding folded into it named under it. Nothing is applied. |
| `--cache` | Keep each decided review in this folder, keyed by the reviewer's code and config, the commits, the checks, every setting, each model and each outside tool a rule runs, ruff's version among it; the same review again is served from it, with no model call. A review with a hole is never kept. Never a folder inside the repository under review. A file in the folder is served as the review's verdict, so whoever can write the folder can write a verdict: keep it the operator's own, and in CI never restore it from a cache a job running untrusted code can write. |
| `--previous` | Compare with a previous review's JSON: mark each finding new or seen before, by its fingerprint, and list what is no longer found. A review with a hole resolves nothing. |
| `--progress` | Tell on stderr as the change is read and as each guideline starts and finishes. On at a terminal. |
| `--debug-log` | Write every model request and answer to this file, secrets redacted, the session's state left out. Keep the file as you keep the code it quotes. |
| `--trace` | Record ADK's spans, with their timings and token counts, to this local SQLite file. Nothing is sent anywhere. |
| `--record` | Keep each model answer in this folder under a hash of the request as the model read it, secrets redacted: a live run's answers, which a fake replays offline. |
| `--dry-run` | Say what a review would cover and run — the revisions, the files, the profile, the model and whether its arm is ready — and run nothing. |
| `--list-checks` | List every check: its id, its kind, its severity, the profiles that run it, what it looks for and its file. |
| `--explain` | Print one check whole: what it reads, its severity, its profiles, its file and, for a rule, its own examples. |
| `--try` | Run the profile's rules, or `--check`'s, over files or folders on disk, each read as wholly added: how a rule's author sees a rule fire, with no repository and no model. Takes no repository. |
| `--version` | Print the reviewer's version. |
| `--help` | Print every flag. |

`--dry-run`, `--list-checks`, `--try` and `--explain` each stop before a review would start. They exclude one
another, and each refuses a review's own flags — `--json`, `--handoff`, `--cache`, `--previous`, `--record`,
`--trace` and `--debug-log` — with exit 2.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Approved: no finding at or above `--fail-on`, and no hole. |
| 1 | A finding at or above `--fail-on`, and no hole. |
| 2 | A usage error, before any model call: a flag, a config, a profile or a check file that cannot run; no repository named in a pipeline, or no choice made at the question asked; a branch, base or path that does not resolve, a change against a base in a shallow clone, or a change that adds nothing; an arm that is not ready. |
| 3 | A hole: a check failed or could not look, the token ceiling refused a call, a changed file no check read whole, or a finding at the bar whose quote the diff does not bear out. Also an error nothing else names, a report's reader that closed the pipe early, a `--json` or `--handoff` file that could not be written, and a reviewer that cannot load. |
| 130 | Stopped by Ctrl-C. |
| 143 | Stopped by a job, with SIGTERM. |

A hole never approves. Exit 3 outranks a kept finding at the bar, so its verdict line names the findings at the bar
beside it.

## Checks and profiles

Every check is one file. A rule, decided by code, is in `judge/rules/`; a guideline, judged by a model, is in
`judge/guidelines/`. A profile in the config names the checks it runs, or the groups `rules` and `guidelines`, and a
check runs only where a profile names it, or alone with `--check`:

- `default`: the basics, and what runs with no `--profile`. The config must hold it.
- `default-rules`: the default's rules and no model. It needs no key and fits any project's every pull request.
- `push-history`: what the pre-push hook reads each commit a push sends with, line by line: the credential rules.
- `rules-only`: every rule, and no model.
- `full`: every check.

The payments rules — `money-in-cents`, and the tree rules `required-paths`, `module-defines`, `parameters` and
`derives-from` — are one small project's own, written for the demo repository: examples of a project's rules, which
a project replaces with its own. The checks, as `--list-checks` prints them:

<!-- checks: the output of `uv run python -m adk_review.review --list-checks`; a test holds it so -->
| id | kind | severity | profiles | what | where |
|---|---|---|---|---|---|
| `rule.derives-from` | tree rule | major | rules-only, full | a class does not derive from the parent its name promises | `judge/rules/derives_from.py` |
| `rule.encoding-declaration` | file rule | blocker | default, default-rules, rules-only, full | an encoding that rewrites the code every rule reads | `judge/rules/encoding_declaration.py` |
| `rule.hidden-text` | line rule | major | rules-only, full | text a reader cannot see: tag characters or bidirectional controls | `judge/rules/hidden_text.py` |
| `rule.lint` | file rule | minor | default, default-rules, rules-only, full | what ruff reports, at a severity for each code | `judge/rules/lint.py` |
| `rule.lockfile-sources` | file rule | minor | default, default-rules, rules-only, full | a lockfile fetches a package from outside its public registry | `judge/rules/lockfile_sources.py` |
| `rule.module-defines` | tree rule | major | rules-only, full | a module no longer defines a name the project relies on | `judge/rules/module_defines.py` |
| `rule.money-in-cents` | line rule | major | rules-only, full | cents divided into a float | `judge/rules/money_in_cents.py` |
| `rule.named-secrets` | line rule | blocker | default, default-rules, push-history, rules-only, full | a value assigned to a name that says secret | `judge/rules/named_secrets.py` |
| `rule.no-print` | line rule | minor | rules-only, full | print() left in library code | `judge/rules/no_print.py` |
| `rule.parameters` | tree rule | major | rules-only, full | a function no longer takes a parameter its callers pass | `judge/rules/parameters.py` |
| `rule.pinned-actions` | file rule | major | default, default-rules, rules-only, full | a workflow runs an action at a tag, a branch or a short SHA, not its full commit SHA | `judge/rules/pinned_actions.py` |
| `rule.private-patch` | line rule | minor | rules-only, full | a test patches a private name | `judge/rules/private_patch.py` |
| `rule.required-paths` | tree rule | major | rules-only, full | a file or folder the project needs is missing | `judge/rules/required_paths.py` |
| `rule.reviewer-instructions` | file rule | major | default, default-rules, rules-only, full | text that tries to instruct the reviewing model | `judge/rules/reviewer_instructions.py` |
| `rule.secrets` | line rule | blocker | default, default-rules, push-history, rules-only, full | a hard-coded credential | `judge/rules/secrets.py` |
| `rule.steering-files` | file rule | minor | rules-only, full | a file another tool or agent obeys has changed | `judge/rules/steering_files.py` |
| `rule.tests-offline` | line rule | major | rules-only, full | a test reaches the network | `judge/rules/tests_offline.py` |
| `guideline.complexity` | guideline | the model's, by finding | default, full | complexity the problem does not require | `judge/guidelines/complexity.py` |
| `guideline.correctness` | guideline | the model's, by finding | full | logic that does not do what the change means it to | `judge/guidelines/correctness.py` |
| `guideline.security` | guideline | the model's, by finding | default, full | security defects and unsafe patterns | `judge/guidelines/security.py` |
| `guideline.tests` | guideline | the model's, by finding | default, full | untested changes, and tests that are not honest | `judge/guidelines/tests.py` |
<!-- /checks -->

A rule's severity is its findings', but `lint` gives each ruff code its own, and `encoding-declaration` is a hole,
exit 3, rather than a finding: no rule read the code Python would run. `--explain ID` prints one check whole.

## Configuration

`config.toml` beside the command holds the review policy under `[review]` — `fail_on`, `max_tokens`, `verify`,
`diff_cap` and `skip`, each a flag's default — and the profiles under `[profiles]`. It is read whole: a section or a
setting it does not know, one missing, one of the wrong kind, a profile that names an unknown check or turns none on,
or no `default` profile is exit 2. It is the one config: nothing of the policy is read from the repository under
review, and `--dry-run` names the file read. Another policy is a change to this file, or a flag for one run. An
installed command reads the copy it was installed with: installed from your own checkout with `uv tool install
--editable <checkout>`, a change there applies at once, as to `uv run`; otherwise reinstall after one. A pre-commit
hook reads the config of the commit it pins, so a policy of your own is a commit of your own fork, pinned instead. The
shipped `skip` names images and fonts, in either case, which no rule or model reads, so a branch adding a logo is not
a hole, and lockfiles, which the rules read whole and no guideline judges; an archive, a keystore or a document can
carry a credential, and stays a hole until the list names it. A text file a glob names is still read by every rule.

The model is set in the environment, or in the `.env` at the root of the reviewer's checkout; a variable set in the
shell wins over the file:

| Variable | What it sets |
|---|---|
| `REVIEW_PROVIDER` | The arm: `copilot` or `gemini`. Default: `copilot`. |
| `REVIEW_MODEL` | The model. Blank: `gpt-4.1` on Copilot, `gemini-2.5-flash` on Gemini. On the Gemini arm, a model ADK sends to another provider, `gpt-*` or `claude-*`, is refused, exit 2; on Copilot, one that cannot judge at temperature 0, `gpt-5` or the o-series, as LiteLLM says before any call. |
| `REVIEW_VERIFIER_MODEL` | The model `--verify` asks. Blank: `REVIEW_MODEL`. |
| `REVIEW_FALLBACK_MODEL` | A second model behind every role's first, tried on a 429 or a 5xx. Blank: none. |
| `GOOGLE_API_KEY` | The Gemini arm's key. |
| `GOOGLE_GENAI_USE_ENTERPRISE` | `true` sends the Gemini arm through Vertex AI. |
| `GOOGLE_CLOUD_PROJECT` | Vertex AI's project, signed in with Application Default Credentials (`gcloud auth application-default login`); no `GOOGLE_API_KEY` is needed, a key being Vertex AI's express mode. Blank: the project the credentials name, gcloud's own or a service account's, as on Cloud Run or GKE. |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI's region. Blank: `global`. |
| `GITHUB_COPILOT_TOKEN_DIR` | Where the Copilot login caches its token. Default: `~/.config/litellm/github_copilot`. |
| `GITHUB_COPILOT_ACCESS_TOKEN_FILE` | The token's file in that folder. Default: `access-token`. |
| `GITHUB_COPILOT_API_BASE` | A GitHub Enterprise tenant's own Copilot endpoint. Blank: github.com's. LiteLLM sends the calls to the endpoint the cached API key names before this one, and the report names the host they go to, never github.com's own. |

Copilot and Gemini are both first-class arms; `copilot` is only the default. The Copilot arm needs a one-time
`uv run adk-review-login`, or `adk-review-login` once installed. A GitHub Enterprise tenant with its own endpoints sets them in
`GITHUB_COPILOT_API_BASE` and the login's `GITHUB_COPILOT_*_URL` variables, as `.env.example` shows. Certificates are
verified through the operating system's own store, so a company proxy's root the machine trusts, the reviewer trusts
too.

## The report

The report prints, in order: the title; the verdict, with the findings at the bar beside a degraded one; what was
reviewed — the repository, the branch and its base with their commits, the merge base, the model where a guideline
ran, the profile and every check it left off, the scope, the changed files, and each file not read, skipped or
untracked; under `--all`, the project's health; the blast radius; a section for each check that found something, with its findings, their evidence and fix, what
folded into another check's finding, or that failed, and why, then one line naming the checks that found nothing; then what a previous review found and this one no
longer does (with `--previous`), the findings dropped for a quote the diff does not bear out, what the verifier
refuted or could not judge (with `--verify`), how many duplicates were folded, how many secrets were redacted, and
the spend. Every string the branch wrote is made safe to print: no credential, no control character or other
character a reader cannot see, no CI runner's command, no HTML comment, and no HTML tag outside a code span.

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
| `reviewed` | The repository, the head and its sha, the base, its sha and the merge base (null under `--all`), the scope, the files the skip list left to the rules, the untracked files under `--staged` (null otherwise), and the changed files. |
| `model` | The model and arm that ran, or null where no guideline ran. |
| `profile` | The profile, or the `--check` list. |
| `off` | Every check the profile left off. |
| `checks` | Every check that ran, in report order. |
| `summary` | How many kept findings sit at each severity. |
| `health` | Under `--all`, the project's measures: files read whole, source and tests in files and lines, maintainability's grade (A to E, SIG's rating of the complexity profile, null with why where under ten or under 90% of the source files parsed), the source's function lines by McCabe complexity band, counted as ruff's C901 counts it, the source files that do not parse, the most complex functions, and each rule's findings in source and in tests by severity. Null for a review of a change. Security and correctness are counted, never graded; the verdict never reads any of it. |
| `findings` | Each kept finding: its fingerprint, check, file, line, the lines the diff places it on, whether one of them is a line the change added (null for a finding with no line), severity, title, evidence and suggestion. A guideline's finding's evidence is the lines of the diff its quote stands on, as the file reads them. A fingerprint is built from the check, the file and the evidence, never the line, so a finding keeps it when lines move. With `--previous`, `since_previous` says `new` or `seen`. |
| `resolved` | With `--previous`, the earlier findings no longer found, as that report wrote them; null without it, or where this review has a hole. |
| `could_not_look` | Each check that failed or could not look, and why. |
| `not_read` | Each changed file no check read whole, and why. |
| `folded` | Each finding folded into another on the same lines, and the check it folded into. |
| `dropped` | Each finding dropped for a quote the diff does not bear out. |
| `dropped_at_fail_on` | How many of those sit at or above the bar. |
| `verified` | Whether `--verify` ran. |
| `refuted` | Each finding a verifier refuted, and why. |
| `unverified` | Each finding a verifier could not judge, and why. |
| `secrets_redacted` | How many secrets were redacted before any model read them. |
| `usage` | Model calls completed and attempted, tokens, the ceiling, and each agent's row: a guideline under its check's id, a verifier under its own name. |
| `seconds` | How long each agent took: a guideline under its check's id, a verifier under its own name. |
| `reviewer_version` | The reviewer's version. |

## Limits

| What | Limit | Where |
|---|---|---|
| One file's diff a guideline reads, unless `diff_cap` or `--diff-cap` names another | 16,000 characters | `collect/diff.py`, `DIFF_CAP_CHARS` |
| All the diffs the guidelines read | 250,000 characters | `collect/change.py`, `BLOCK_CAP_CHARS` |
| The most complex functions the health section names | 5 | `core/health.py`, `WORST_SHOWN` |
| Python source files parsed before maintainability is graded | 10 | `core/health.py`, `FILES_AT_LEAST` |
| One file at the head the rules and the radius read, and one file `--try` reads | 1,000,000 bytes | `collect/git.py`, `MAX_FILE_BYTES` |
| The head's Python files read for the tree rules and the radius, in all; and the changed files' texts, in all | 20,000,000 bytes | `collect/git.py`, `MAX_TREE_BYTES` |
| One git call | 30 seconds | `collect/git.py`, `GIT_TIMEOUT_S` |
| One ruff run | 60 seconds | `collect/ruff.py`, `RUFF_TIMEOUT_S` |
| One model call | 180 seconds | `judge/config.py`, `REQUEST_TIMEOUT_S` |
| One run of the guidelines, or of the verifiers | 600 seconds | `judge/config.py`, `RUN_DEADLINE_S` |
| Model calls in flight at once | 3 | `judge/config.py`, `CONCURRENCY` |
| The radius a guideline is shown | 20,000 characters | `judge/agents.py`, `BLAST_CAP_CHARS` |
| The rules' findings chat's verdict reads | 20,000 characters | `judge/agents.py`, `RULES_CAP_CHARS` |
| The changed-files list a model reads in chat | 20,000 characters | `judge/tools.py`, `LISTED_MAX_CHARS` |
| Ruff's findings listed for one file; the rest are counted in one finding | 50 | `judge/rules/lint.py`, `PER_FILE` |
| A finding's evidence in the report | 400 characters | `core/text.py`, `EVIDENCE_MAX_CHARS` |
| A reason in the report: why a check failed, a verifier's word, a provider's error | 400 characters | `core/text.py`, `REASON_MAX_CHARS` |

A file past a guideline's cap is named under "not read", and the review ends in exit 3: a hole, never "nothing
found". `--path` narrows a review to fit. With no guideline
on, a cut is no hole: the cap is the guidelines' limit, and the rules read every added line.

## Before a push

`adk-review-push`, installed beside `adk-review`, is a git pre-push hook. To install it, run these lines in the
repository to guard, in any shell git brings (Git Bash on Windows):

    hook="$(git rev-parse --path-format=absolute --git-path hooks)/pre-push"
    mkdir -p "${hook%/*}"
    printf '#!/bin/sh\nexec adk-review-push "$@"\n' > "$hook" && chmod +x "$hook"

They write the hook where git reads it: `.git/hooks`, unless `core.hooksPath` names another folder, and from a
linked worktree its main repository's; a pre-push hook already there is replaced. Git passes over a hook it cannot
run, and over one in a folder it does not read, and the push goes unreviewed. A tool that runs hooks for git must
hand this one git's lines on its standard input: given none, it reads a push that sends nothing.

A repository that uses pre-commit names the hook `adk-review` from this repository's `.pre-commit-hooks.yaml`
instead, at a pinned commit, with pre-commit's pre-push hook installed (`pre-commit install --hook-type pre-push`); it
runs the same command, and takes no `args:`, which stop every push. pre-commit installs the reviewer with the Python
pre-commit itself runs on, and the reviewer needs 3.13 or newer: with an older one the install fails, and so does the
push. That config names another beside the hook's id, `language_version: python3.13`.

For each ref a push sends, the hook asks git which commits the remote does not hold yet and reviews them as one
change with `default-rules`, against the commit they grow from on the remote's side, or with `--all` where the remote
holds none of their history. A merge grows from more than one: the change is reviewed against each, and passes when
one review passes, since a line the remote holds nowhere is new to every one of them. Then the rules `push-history`
names, the credential rules `secrets` and `named-secrets`, read each of those commits as it was made, and what each
says, a pushed tag's words too: a credential one commit adds and a later one takes out stops the push, though the
change as a whole no longer holds it. A commit is read line by line, so the profile names line rules alone, and a
commit a push sends under two names, a branch and its tag, is reviewed and read once. Any exit but 0 stops the push
and prints why; so does a `git` the hook cannot find, or a line from git it
cannot read. A push that sends nothing new, or commits that change nothing, says nothing. Through pre-commit by hand,
`pre-commit run adk-review --hook-stage pre-push --from-ref A --to-ref B` reviews that range and its commits, and with
neither ref, the whole project at `HEAD`. Run alone at a terminal, the hook says what it is, exit 2.
`git push --no-verify` skips it.

What the hook cannot see, or stops on:

- pre-commit reads git's lines itself and hands its hook one ref: the first not deleted, passing over a new ref whose
  commits the remote already holds. A push of several refs is reviewed whole by git's own hook alone.
- In a commit before the last, only a credential stops the push, or a binary file no rule can read, unless
  `[review] skip` names it. What a merge's own resolution adds and a later commit takes out is read in no commit.
- The remote's branches are as this clone last fetched them: one deleted or rewritten there still counts as held
  here, so fetch before a push that follows a rewrite, and let a fetch prune deleted ones (`git config fetch.prune
  true`). Pushed to a URL rather than a remote's name, no branch of it is known here, and a new ref is reviewed as a
  whole project.
- A file no rule can read — a binary one, a submodule — is a hole and stops the push, unless `[review] skip` in
  `config.toml` names it, as the shipped list names images and fonts ("Configuration" above): then the report names
  it as skipped, and it stops nothing. A text file past the size limits
  above, a lock file say, is read as far as its diff shows it, which is all `reviewer-instructions` reads; a Python
  file past them is a hole for `lint` and `encoding-declaration`, which read a file whole, and stops the push.
- A repository with no `.git` folder of its own, a bare one or one used through `--git-dir`, and a tag of a tree or a
  blob, stop the push: the hook reviews a working tree's commits.

## More

- How a review runs, the layout and the design: `README.md` beside this file.
- In a pipeline: "In a pipeline" in `README.md`, a recipe tried on a simulated checkout.
- Your own checks: "Write a rule" and "Write a guideline" in `README.md`.

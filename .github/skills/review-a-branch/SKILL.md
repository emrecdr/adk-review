---
name: review-a-branch
description: Run this repository's reviewer on a git repository and branch and read its report — model-free with --profile rules-only, or with the guidelines when a provider is configured — and explain the exit code. Use when asked to review a branch, try the reviewer, or see what its rules and guidelines find.
---

# Review a branch

The reviewer is a command: `adk-review [repo] [--head B] [--base main | --all | --staged]
[--path DIR] [--fail-on …] [--verify | --no-verify] [--profile NAME | --check ID] [--json FILE] [--dry-run]
[--list-checks]`; every flag, with what it does, is in `GUIDE.md`, which a test holds to the command
itself. With no `repo` it asks a terminal for one and refuses in a pipeline: it assumes no repository, the demo
`scripts/make_demo_repo.py` builds in the temp folder included. `--dry-run`
resolves what a run would cover — revisions, files, profile, model, readiness — and runs nothing: the cheapest way to
catch the wrong branch; `--list-checks` shows every check with its kind, its profiles and its file.

1. Without a model, which needs no key and is safe anywhere:
   `uv run python -m adk_review.review <repo> --head <branch> --base main --profile default-rules`. The default's rules
   run, `secrets` and `lint` among them; the guidelines do not. `--profile rules-only` runs every rule in
   `adk_review/judge/rules/` but the payments rules written for the demo, which `--profile demo` adds. In a pipeline
   name `--head`, since the checkout is a detached commit, and fetch the base branch with its history, or review the
   whole head with `--all`.
2. With the guidelines, only when asked and when a `--dry-run` says the arm is ready: with nothing set the arm is
   Copilot, the one this reviewer targets, ready once the person has run `adk-review-login`; Gemini needs
   `REVIEW_PROVIDER=gemini` with `GOOGLE_API_KEY` or a Vertex AI project. Drop `--profile`. Never run the login, read
   `.env`, or open the Copilot token's folder;
   the report names the arm and the model that ran. `--profile simplify` reads the change as Claude Code's
   `/simplify` does, the cleanup, with `lint` and `pitfalls` beside it; `--profile code-review` as its `/code-review`
   does, correctness and security before the same cleanup; `--profile full` runs every check but the payments demo's
   rules, `--verify` adds a second opinion on each guideline's
   finding, `--handoff FILE` writes the findings for a coding agent to apply, and `--comment FILE` writes a comment
   for the branch's author, to paste into the pull request by hand.
3. Read the exit code: 0 approved; 1 a finding at or above `--fail-on` (`blocker` by default); 2 a usage error before
   any model call, such as a branch that adds nothing or one the repository does not hold, whose message names the `git
   fetch` to run: show it to the person and fetch nothing yourself, an arm that is not ready, or a `config.toml` or
   profile that cannot run; 3 a hole — a rule or a guideline failed, a rule could not look (the head's tree not listed,
   a file it holds not read whole, or a link whose file was not read or followed), a changed file no check can read
   whole, or where a guideline runs one it was not shown whole, unless the skip list names it, a finding at the bar
   whose quote the diff does not bear out or that a verifier refuted, or a guideline's call the token ceiling refused;
   and a `--json`, `--handoff` or `--comment` file that cannot be written, an error nothing else names, a reader that
   closes the pipe early, or a reviewer that cannot load — which must never be read as "nothing found". Ctrl-C is 130,
   and a job's SIGTERM 143.
4. The report lists findings under each check that ran, a section each (`rule.secrets`, `rule.lint`, …, then
   `guideline.security` and the other guidelines), the blast radius of each changed file, what was not read, the files
   it ignored by `[review] scan` and `third_party` (the credential rules still read the project's own), and what the run
   cost. In a pipeline, `--quiet` prints nothing when the review passes. Quote its lines when you report, rather than
   paraphrasing them.

---
name: update-docs
description: Keep the docs true after a change to the reviewer — find every guide that names what changed, not only the one beside the edit, and hold each to the code — GUIDE.md, README.md, CLAUDE.md, the Copilot files under .github/, .env.example and the comments of the workflows and the Makefile. Use after a change to a flag, check, JSON field, limit, exit code, module, helper, command, CI step or setting, and before calling the change done.
---

# Keep the docs true after a change

The suite holds part of the docs to the code and no more. `tests/test_docs.py` checks that `GUIDE.md` holds every
flag, check, JSON field and limit as the code has them, that each guide but `README.md` names only paths the working
tree holds, and that `README.md` names each module at the package's root; `tests/test_copilot.py` holds the files under
`.github/` to their format. What a sentence says a module or a command does, no test reads, so read it yourself:

1. Name what the change made stale: a function, type or flag renamed or added, a file moved, a CI step added, a
   default or an exit code changed.
2. Search every guide for the old wording, not only the file beside the edit: a case-insensitive search of
   `README.md`, `GUIDE.md`, `CLAUDE.md`, `.github/` and `.env.example` for the name, the flag and the phrase the change
   outdated, such as "the lint and the suite" once a gate joins them. A guide that lists several things in one place —
   the README's module table, the door's contract, the exit codes — is wrong when one is missing.
3. Each guide holds one kind of truth: `GUIDE.md` what a user runs and reads back; `README.md` the architecture and
   how to write a rule or a guideline; `CLAUDE.md` the rules a coding agent works by, which Copilot in VS Code and the
   cloud agent read too; `.github/copilot-instructions.md` and `.github/instructions/` their short form, which agrees
   with it, a rule that changes in one changing in the other.
4. Write as the docs are written: plain prose that says what the code does and why, no date and no count of the
   repository's current state, a number measured once kept as history, lines wrapped at 120. Regenerate `GUIDE.md`'s
   check table from `uv run adk-review --list-checks`.
5. Run `uv run pytest -q tests/test_docs.py tests/test_copilot.py`, then `make ci`, and report what each printed.

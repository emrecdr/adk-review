---
applyTo: "**/*.md"
---

# Docs

- The docs are tested (`tests/test_docs.py`). Every guide — `README.md`, `GUIDE.md`, `CLAUDE.md` and every Markdown
  file under `.github/` — names only paths the working tree has: a backticked path with a folder in it that no
  checkout holds fails the test (`.adk/session.db`, written when `adk web` runs, is the one exception). Add, remove or
  rename a file, and every guide that names it changes in the same change.
- `README.md` is the developer's guide — the architecture, and how to write a rule or a guideline — and names each
  `*.py` directly in `adk_review/`. `GUIDE.md` is the user's: every flag the parser takes and no other, the checks as
  `--list-checks` prints them, every field the JSON report holds in order, and each limit the number its constant
  holds, all checked against the code.
- `CLAUDE.md` and `.github/copilot-instructions.md` are the assistants' guides and say the same things: a rule that
  changes in one changes in the other.
- No dates (nothing here is a changelog) and no counts of the repository's current state: not how many tests, how
  long the suite takes, how many flags or checks. A measured number that settled a decision is history and stays.
  Plain prose that says why, kept short; every claim about the code must be true of the code as it is.

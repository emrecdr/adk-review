---
name: new-guideline
description: Make the reviewer judge a new concern with a model — one file in adk_review/judge/guidelines/ (an id, a title, a role, a thinking budget) — and bring the tests and the docs along; the catalog, the fan-out, the chat verdict's sections and the report follow from the file. Use when asked to make the reviewer judge something new, such as documentation or performance, that needs judgement rather than a pattern.
---

# Add a guideline to the reviewer

A concern a model judges is a guideline, a file of its own in `adk_review/judge/guidelines/`. Nothing else names it:
the catalog finds the file, and the fan-out, its state key, the chat verdict's sections, the report and its
`guideline.<name>` check id follow from it.

1. Copy the template under "Write a guideline" in `README.md` to a file in `adk_review/judge/guidelines/` named after
   the guideline: a lowercase id, a title of a few words, a role of one or two sentences that starts "You judge", and
   a thinking budget, which Copilot ignores; and `cleanup = True` if its findings change how code reads and never
   what it does, as `complexity`'s do, so the hand-off lists them as tidies.
2. Profiles in `adk_review/config.toml`: `full` runs it at once, through the group `guidelines`; add
   `guideline.<name>` to `default` to run it in every review, and to `simplify` or `code-review` where it belongs to
   that pass. `rules-only` runs no guideline and stays as it is.
3. Tests: `tests/test_reviewer.py`, `tests/test_engine.py`, `tests/test_rules.py`, `tests/test_command.py` and
   `tests/test_catalog.py` count the guidelines in places; run them, and extend each assertion the new one breaks rather
   than loosening it.
4. Docs: `GUIDE.md`'s check table is held to `--list-checks`, so the docs test names what to add; a case-insensitive
   search for `correctness` finds each other place that names them all, and one for `three calls` the places that
   count each profile's calls: `full`'s grows with every guideline, and `default`'s, `simplify`'s or `code-review`'s
   only with one that joins it.
   Update each, except a measured number, which is history.
5. Run the whole suite, the lint and the type check. Measure the guideline live only when asked —
   `uv run python scripts/measure.py --runs 5` calls a provider.

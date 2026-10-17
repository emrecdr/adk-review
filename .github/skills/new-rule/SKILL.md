---
name: new-rule
description: Add a rule to the reviewer — one file in judge/rules/, named after the rule, a Rule over the added lines, a FileRule over each changed file, or a TreeRule over the head's tree — with its tests, its place in config.toml's profiles and the docs, in the shape this repository uses. Use when asked to make the reviewer flag something new by code rather than by a model.
---

# Add a rule to the reviewer

The shape of a rule — the three kinds, the constants it names, what it may import — is in
`.github/instructions/reviewer.instructions.md`, which loads with any file under `adk_review/`. The procedure:

1. Read "Write a rule" in `README.md` and copy the template of the kind the question needs to a
   file in `adk_review/judge/rules/`, the rule's id the file's name with `-` for `_`: the catalog finds it at
   once and refuses a file not named after its rule. A `Rule` when one added line answers the question (`calls`,
   `comment_of` and `words` read it as Python does, so a word in a comment or a string is not code); a `FileRule`
   when the answer needs the file — a call wrapped over lines, a decorator and its definition, a function's length —
   through `file.lines`, `file.added`, `file.changed` and `file.module`, read with `calls_in` and `argument`, or
   `file.near(n)` where only the lines around what a change added matter, so a file past the size limits is read; a
   `TreeRule` when it needs the head's paths or every module. Every name comes from one import line,
   `from ..rule_helpers import …`, the door in `adk_review/judge/rule_helpers/`.
2. Keep the rule's expectations as constants at the top of its file, as `REQUIRED` is in `required_paths.py`, and
   give it `bad` and `good` examples: the catalog holds the rule to them, and to firing on nothing in its own source,
   at every start. The folder holds rules and nothing else: no helper module and no guide; a helper several rules
   share belongs in `adk_review/judge/rule_helpers/`, in the module named after what it reads, and in the
   door's `__all__`.
3. Profiles in `adk_review/config.toml`: `rules-only` and `full` run it at once, through the group `rules`; add
   `rule.<name>` to `default` only when every review should run it, since `default` holds the basics.
4. See it fire on files on disk, no repository, no model and no key: `uv run python -m adk_review.review --try
   <files or folder> --check rule.<name>`, and `--explain rule.<name>` prints it whole. Then try it alone on a branch:
   `uv run python -m adk_review.review <repo> --base main --check rule.<name>` on the demo repository or any other. Then test it in `tests/test_rules.py` beside the other rules'
   tests (`TestTheShippedRules`, `TestTreeRules`; a new kind's contract in `tests/test_catalog.py`): what it flags and what it leaves alone through
   `try_rule(rule, {path: text})` and `lines_hit(rule, path, text)` from `adk_review.judge.rule_helpers`,
   what its examples do not already say, and a run on
   the demo repository with `--profile rules-only` when the planted branch should trip it.
5. Check `uv run python -m adk_review.review --list-checks` shows it with its kind, its profiles and its
   file, and that `GUIDE.md`'s check table matches it, and run `uv run pytest -q tests/test_docs.py tests/test_rules.py
   tests/test_catalog.py tests/test_layering.py`, then the whole suite, the lint and the type check.

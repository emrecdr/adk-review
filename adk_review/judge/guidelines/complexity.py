"""Complexity: a change more complicated than the problem it solves."""

from __future__ import annotations

from ...core.guidelines import Guideline


class Complexity(Guideline):
    """Ruff counts branches and statements; whether the problem needed them takes a reader, here a model."""

    id = "complexity"
    title = "complexity the problem does not require"
    role = (
        "You judge whether the change is more complex than the problem requires: state that could be derived, code "
        "copied with a small change, nesting a guard clause would flatten, an abstraction with one caller and no "
        "purpose, dead code left behind. Name the simpler form that does the same job, never one that changes what "
        "the code does — its return values, exceptions and log lines, its evaluation order, laziness and concurrency, "
        "as a list for a generator, `.get()` for an index or awaits gathered would."
    )
    thinking_budget = 2048

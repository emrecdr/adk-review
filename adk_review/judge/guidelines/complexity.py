"""Complexity: a change more complicated than the problem it solves."""

from __future__ import annotations

from ...core.guidelines import Guideline


class Complexity(Guideline):
    """Ruff counts branches and statements; whether the problem needed them takes a reader, here a model."""

    id = "complexity"
    title = "complexity the problem does not require"
    role = "You judge whether the change is more complex than the problem requires."
    thinking_budget = 2048

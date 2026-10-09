"""Correctness: whether the change does what it means to, for every input it can meet."""

from __future__ import annotations

from ...core.guidelines import Guideline


class Correctness(Guideline):
    """No other guideline has correctness as its dimension: security judges harm, tests judge what is tested, and
    complexity judges the shape. In `full` through the group, and in no other profile until a live measurement on the
    planted branch says what it adds beside the three."""

    id = "correctness"
    title = "logic that does not do what the change means it to"
    role = (
        "You judge correctness: whether each changed function does what its name, its callers and its tests say it "
        "should, for every input it can meet — off-by-one bounds, an inverted condition, a wrong operator or unit, a "
        "value used before it is set, an error swallowed or a branch that can never run."
    )
    thinking_budget = 4096

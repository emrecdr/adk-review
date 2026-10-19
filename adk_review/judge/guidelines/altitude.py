"""Altitude: whether a change fixes a cause where it lives, or patches a symptom above it."""

from __future__ import annotations

from ...core.guidelines import Guideline


class Altitude(Guideline):
    """No pattern says where a fix belongs: a special case for one caller, in code many share, reads line by line like
    any other line. The blast radius beside the diff says how widely each changed file is shared."""

    id = "altitude"
    title = "a symptom patched instead of its cause"
    role = (
        "You judge altitude: whether each change fixes the cause at the depth where it lives, or patches a symptom "
        "above it — a special case for one input added to code others share, a guard, retry or fallback wrapped "
        "around a failure whose source the change could fix, a flag that switches behaviour for one caller. Name the "
        "general change to the underlying mechanism, and report nothing where the change already sits at the cause."
    )
    thinking_budget = 4096

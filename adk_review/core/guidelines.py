"""What a guideline is: one thing a model judges in a change, one class in one file of `judge/guidelines/`.

A guideline is the model's half of a review as a rule is the code's: a rule decides, a guideline gives a model its
role and asks it to judge. Its file holds only what makes it this guideline — its id, a title, the role and a
thinking budget. The instruction around the role, the schema, the fences around the change and the reading back are
the same for every guideline, and live in `judge/agents.py`. Pure: no ADK, nothing run.
"""

from __future__ import annotations

from typing import ClassVar

from .profiles import guideline_id


class Guideline:
    """One guideline: a subclass in a file of its own under `judge/guidelines/`, named after the file."""

    #: Lower-case words joined by hyphens, the file's name with `_` for `-`: `security.py` holds `security`.
    id: ClassVar[str]
    #: What it judges, in a few words, as `--list-checks` prints it.
    title: ClassVar[str]
    #: The instruction's opening sentences: who the model is, and what it judges the change for.
    role: ClassVar[str]
    #: Tokens of thinking the model may spend before it answers, on a provider that has the lever; Copilot has none.
    thinking_budget: ClassVar[int]
    #: Whether its findings change how code reads and never what it does, as a simplify pass's do: a handoff lists them
    #: apart, under what a tidy keeps, and holds one back where a defect at major or above names the same lines.
    cleanup: ClassVar[bool] = False

    @property
    def check_id(self) -> str:
        """The check's id, as `config.toml`, the flags and the report spell it: `guideline.security`."""
        return guideline_id(self.id)

"""G2 contract: a SafetyReviewer checks the doctor's management plan.

EpisodeRunner calls review() once on the doctor's plan. On REVISE/VETO the doctor
gets the issues and writes one revised plan, which is reviewed again; the
result is stored in EpisodeResult.final_plan / safety_verdicts.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from medsim.contracts import Case, Message, SafetyVerdict, Usage


class SafetyReviewer(ABC):
    name: str = "base"

    def __init__(self) -> None:
        self.last_usage = Usage()

    @abstractmethod
    def review(self, case: Case, diagnosis: str, plan: str, transcript: list[Message]) -> SafetyVerdict:
        """Return approve / revise / veto with issues. Must not see case.correct_diagnosis."""

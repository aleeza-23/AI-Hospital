"""Experience-replay memory (paper Sec. 3 'Memory and Self-Improvement').

Phase 1 defines the contract only; the baseline uses NullMemory.
Phase 2 will implement:
  * MedicalRecordsBuffer    - correctly diagnosed cases (full conversation + dx)
  * ExperienceRecordsBuffer - reflection insights from corrected misdiagnoses
  * kNN retrieval over embeddings (CLIP in the paper) as few-shot context
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from medsim.contracts import Case, EpisodeResult


class MemoryRecord(BaseModel):
    case_id: str
    kind: str  # "medical_record" | "experience"
    summary: str
    diagnosis: str
    insight: str = ""


class MemoryBuffer(ABC):
    """Contract used by EpisodeRunner:

    * context_for(case) is called BEFORE the consultation; its text is added to the
      doctor's system prompt ("relevant past experience"). Only case.objective may
      be used as the query (that is all the doctor knows up front).
    * add(case, result) is called AFTER judging; result.verdict and
      result.safety_verdicts are available, so a gate can decide what to store.
    """

    @abstractmethod
    def add(self, case: Case, result: EpisodeResult) -> None: ...

    @abstractmethod
    def retrieve(self, query: str, k: int = 3) -> list[MemoryRecord]: ...

    def context_for(self, case: Case, k: int = 3) -> str:
        records = self.retrieve(case.objective, k)
        if not records:
            return ""
        lines = [f"- Case: {r.summary} -> Diagnosis: {r.diagnosis}" + (f". Lesson: {r.insight}" if r.insight else "")
                 for r in records]
        return "\n".join(lines)


class NullMemory(MemoryBuffer):
    """Baseline (Multi-Agent Clinic): no memory."""

    def add(self, case: Case, result: EpisodeResult) -> None:
        return None

    def retrieve(self, query: str, k: int = 3) -> list[MemoryRecord]:
        return []

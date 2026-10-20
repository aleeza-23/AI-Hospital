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
    @abstractmethod
    def add(self, case: Case, result: EpisodeResult) -> None: ...

    @abstractmethod
    def retrieve(self, query: str, k: int = 3) -> list[MemoryRecord]: ...


class NullMemory(MemoryBuffer):
    """Baseline (Multi-Agent Clinic): no memory."""

    def add(self, case: Case, result: EpisodeResult) -> None:
        return None

    def retrieve(self, query: str, k: int = 3) -> list[MemoryRecord]:
        return []

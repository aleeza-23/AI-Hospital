"""G3: the two memory implementations compared by the poisoning experiment.

    UngatedMemory  MedAgentSim-style. Stores a medical record for every correctly
                   diagnosed case, a lesson from *every* case including the wrong
                   ones, and any externally submitted lesson, with no verification.
    GatedMemory    SafeWard (G3). A record or lesson is written only when the
                   outcome is confirmed against the ground truth and no safety
                   verdict was a VETO.

The difference is one method, `admits()`; everything else is shared, so the
comparison isolates the gate rather than two separate implementations.

Provenance and audit. `MemoryRecord` in buffer.py is the contract `retrieve()`
must return and is shared code this gap does not own, so it carries no
provenance fields. Entries are therefore kept as `MemoryEntry` - id, source
case, timestamp, status, whether the outcome was verified - and converted to
`MemoryRecord` on the way out. Every write, rejection, retrieval and retraction
appends to `audit`.

Retraction. `retract(entry_id)` is manual. Automatic retraction fires when an
entry was retrieved for a case that then got a wrong diagnosis, twice: one wrong
answer after a retrieval is noise, two is evidence the entry is harmful. The
counter is per entry, not per case, so a single bad lesson retrieved by many
cases is withdrawn quickly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from medsim.contracts import Case, EpisodeResult, SafetyDecision
from medsim.eval.judge import exact_match
from medsim.memory.buffer import MemoryBuffer, MemoryRecord
from medsim.memory.embed import TfidfIndex

log = logging.getLogger(__name__)

AUTO_RETRACT_AFTER = 2       # wrong diagnoses following retrieval of the same entry
SUMMARY_CHARS = 320


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EntryKind(str, Enum):
    MEDICAL_RECORD = "medical_record"    # a diagnosed case, MedAgentSim's medical records
    EXPERIENCE = "experience"            # a lesson drawn from an episode
    SUBMITTED = "submitted_lesson"       # written in from outside; the poisoning path


class EntryStatus(str, Enum):
    ACTIVE = "active"
    RETRACTED = "retracted"


class MemoryEntry(BaseModel):
    """One stored item, with the provenance MemoryRecord has no room for."""

    id: str
    kind: EntryKind
    summary: str
    diagnosis: str
    insight: str = ""
    source_case_id: str
    created_at: str = Field(default_factory=_now)
    status: EntryStatus = EntryStatus.ACTIVE
    verified: bool = False               # outcome confirmed against the ground truth
    claimed_diagnosis: str = ""          # what a submitted lesson asserted
    followed_by_wrong: int = 0
    retracted_at: str = ""
    retracted_reason: str = ""

    def to_record(self) -> MemoryRecord:
        """The shared contract type the doctor's context is built from."""
        return MemoryRecord(case_id=self.source_case_id, kind=self.kind.value,
                            summary=self.summary, diagnosis=self.diagnosis, insight=self.insight)


class AuditEvent(BaseModel):
    event: str                           # write | reject | retrieve | retract
    entry_id: str = ""
    kind: str = ""
    source_case_id: str = ""
    case_id: str = ""                    # the case being handled when this happened
    reason: str = ""
    entry_ids: list[str] = Field(default_factory=list)   # for retrieve events
    at: str = Field(default_factory=_now)


def summarise(case: Case, result: Optional[EpisodeResult] = None) -> str:
    """Query-side text for an entry: the objective plus what the tests showed.

    `context_for` queries with `case.objective` alone, because that is all the
    doctor knows up front, so the stored summary has to be matchable against an
    objective. The findings are appended for the doctor to read, not to dominate
    the match.
    """
    parts = [case.objective.strip()]
    if result is not None:
        names = [t.test_name for t in result.tests_requested][:4]
        if names:
            parts.append("Tests: " + ", ".join(names))
    return " ".join(p for p in parts if p)[:SUMMARY_CHARS]


class BaseMemory(MemoryBuffer):
    """Shared storage, retrieval, provenance, audit and retraction."""

    name = "base"
    gated = False

    def __init__(self, k: int = 3, auto_retract_after: int = AUTO_RETRACT_AFTER,
                 min_score: float = 0.0):
        self.k = k
        self.auto_retract_after = auto_retract_after
        self.min_score = min_score
        self.entries: dict[str, MemoryEntry] = {}
        self.audit: list[AuditEvent] = []
        self.index = TfidfIndex()
        self.truth: dict[str, str] = {}          # case id -> correct diagnosis, for the gate
        self.retrieval_log: list[dict[str, Any]] = []
        self.last_hits: list[str] = []           # entry ids from the most recent retrieve()
        self._last_retrieved: dict[str, list[str]] = {}
        self._counter = 0

    # --- bookkeeping -------------------------------------------------- #
    def _next_id(self) -> str:
        self._counter += 1
        return f"e{self._counter:04d}"

    def _log(self, event: str, **kw: Any) -> None:
        self.audit.append(AuditEvent(event=event, **kw))

    def register_cases(self, cases: Iterable[Case]) -> None:
        """Teach the gate the ground truth for cases whose lessons it may vet.

        The gate is defined as checking a claim against the source case's ground
        truth, so it needs that truth. Nothing here reaches the doctor: only an
        entry's summary, diagnosis and insight are ever put in the prompt.
        """
        for case in cases:
            self.truth[case.id] = case.correct_diagnosis

    @property
    def active(self) -> list[MemoryEntry]:
        return [e for e in self.entries.values() if e.status == EntryStatus.ACTIVE]

    # --- writing ------------------------------------------------------ #
    def admits(self, case: Case, result: EpisodeResult) -> tuple[bool, str]:
        """Whether an episode may be written. Overridden by GatedMemory."""
        return True, ""

    def _store(self, kind: EntryKind, summary: str, diagnosis: str, source_case_id: str,
               insight: str = "", verified: bool = False, claimed_diagnosis: str = "",
               case_id: str = "") -> MemoryEntry:
        entry = MemoryEntry(id=self._next_id(), kind=kind, summary=summary,
                            diagnosis=diagnosis, insight=insight, source_case_id=source_case_id,
                            verified=verified, claimed_diagnosis=claimed_diagnosis)
        self.entries[entry.id] = entry
        self.index.add(entry.id, f"{summary} {insight}")
        self._log("write", entry_id=entry.id, kind=kind.value, source_case_id=source_case_id,
                  case_id=case_id or source_case_id,
                  reason="verified" if verified else "unverified")
        return entry

    def _reject(self, kind: EntryKind, source_case_id: str, reason: str) -> None:
        self._log("reject", kind=kind.value, source_case_id=source_case_id, reason=reason)

    def add(self, case: Case, result: EpisodeResult) -> None:
        """Called by EpisodeRunner after judging."""
        self.truth.setdefault(case.id, case.correct_diagnosis)
        self._check_retrieved_entries(case, result)

        correct = bool(result.verdict and result.verdict.correct)
        ok, reason = self.admits(case, result)
        summary = summarise(case, result)

        # A medical record needs a diagnosis to be worth storing at all; the paper
        # stores correctly diagnosed cases as records.
        if correct and ok:
            self._store(EntryKind.MEDICAL_RECORD, summary, case.correct_diagnosis, case.id,
                        verified=True, case_id=case.id)
        elif correct and not ok:
            self._reject(EntryKind.MEDICAL_RECORD, case.id, reason)

        # The lesson. This is where the two memories diverge: MedAgentSim keeps a
        # lesson from every case, so a wrong diagnosis is stored as if it were the
        # answer, which is exactly the spread this gap is about.
        if not ok:
            if not correct:
                self._reject(EntryKind.EXPERIENCE, case.id, reason)
            return
        claimed = result.final_diagnosis or ""
        if not claimed:
            return
        insight = ("Confirmed correct." if correct else
                   "Recorded from an unverified episode outcome.")
        self._store(EntryKind.EXPERIENCE, summary, claimed, case.id, insight=insight,
                    verified=correct, case_id=case.id)

    def submit_lesson(self, lesson: str, source_case_id: str,
                      claimed_diagnosis: str) -> Optional[MemoryEntry]:
        """External write path, used by the poisoning test.

        Ungated takes it as given. Gated checks the claim against the source
        case's ground truth and fails closed when that truth is unknown.
        """
        ok, reason = self.admits_lesson(source_case_id, claimed_diagnosis)
        if not ok:
            self._reject(EntryKind.SUBMITTED, source_case_id, reason)
            return None
        return self._store(EntryKind.SUBMITTED, lesson, claimed_diagnosis, source_case_id,
                           insight=lesson, verified=self.gated,
                           claimed_diagnosis=claimed_diagnosis)

    def admits_lesson(self, source_case_id: str, claimed_diagnosis: str) -> tuple[bool, str]:
        return True, ""

    # --- retrieval ---------------------------------------------------- #
    def retrieve(self, query: str, k: int = 3) -> list[MemoryRecord]:
        hits = self.index.query(query, k=k, min_score=self.min_score)
        out = []
        for entry_id, _score in hits:
            entry = self.entries.get(entry_id)
            if entry and entry.status == EntryStatus.ACTIVE:
                out.append(entry)
        self.last_hits = [e.id for e in out]
        return [e.to_record() for e in out]

    def context_for(self, case: Case, k: Optional[int] = None) -> str:
        """Overridden so a retrieval can be attributed to the case that caused it.

        Automatic retraction needs to know which entries were shown for which
        case; the base implementation discards that.
        """
        records = self.retrieve(case.objective, k or self.k)
        shown = list(self.last_hits)
        if shown:
            self._last_retrieved[case.id] = shown
            self.retrieval_log.append({"case_id": case.id, "entry_ids": shown})
            self._log("retrieve", case_id=case.id, entry_ids=shown)
        if not records:
            return ""
        return "\n".join(
            f"- Case: {r.summary} -> Diagnosis: {r.diagnosis}"
            + (f". Lesson: {r.insight}" if r.insight else "")
            for r in records)

    def retrieved_for(self, case_id: str) -> list[str]:
        return list(self._last_retrieved.get(case_id, []))

    # --- retraction --------------------------------------------------- #
    def retract(self, entry_id: str, reason: str = "manual") -> bool:
        entry = self.entries.get(entry_id)
        if entry is None or entry.status == EntryStatus.RETRACTED:
            return False
        entry.status = EntryStatus.RETRACTED
        entry.retracted_at = _now()
        entry.retracted_reason = reason
        self.index.remove(entry_id)
        self._log("retract", entry_id=entry_id, kind=entry.kind.value,
                  source_case_id=entry.source_case_id, reason=reason)
        return True

    def _check_retrieved_entries(self, case: Case, result: EpisodeResult) -> None:
        shown = self._last_retrieved.pop(case.id, [])
        if not shown or (result.verdict and result.verdict.correct):
            return
        for entry_id in shown:
            entry = self.entries.get(entry_id)
            if entry is None or entry.status != EntryStatus.ACTIVE:
                continue
            entry.followed_by_wrong += 1
            if entry.followed_by_wrong >= self.auto_retract_after:
                self.retract(entry_id,
                             f"auto: followed by a wrong diagnosis {entry.followed_by_wrong} times")

    # --- reporting ---------------------------------------------------- #
    def seed_correct_records(self, cases: Iterable[Case]) -> int:
        """Pre-fill with verified records, as the clean run requires.

        Both memories are seeded identically, so the only difference between the
        two arms of the experiment is how later writes are gated.
        """
        n = 0
        for case in cases:
            self.truth[case.id] = case.correct_diagnosis
            self._store(EntryKind.MEDICAL_RECORD, summarise(case), case.correct_diagnosis,
                        case.id, insight="Pre-filled from a dev case.", verified=True)
            n += 1
        return n

    def stats(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for e in self.entries.values():
            key = f"{e.kind.value}_{e.status.value}"
            by_kind[key] = by_kind.get(key, 0) + 1
        events: dict[str, int] = {}
        for a in self.audit:
            events[a.event] = events.get(a.event, 0) + 1
        return {
            "memory": self.name,
            "entries_total": len(self.entries),
            "entries_active": len(self.active),
            "by_kind_status": by_kind,
            "audit_events": events,
            "submitted_accepted": sum(1 for e in self.entries.values()
                                      if e.kind == EntryKind.SUBMITTED),
            "submitted_rejected": sum(1 for a in self.audit if a.event == "reject"
                                      and a.kind == EntryKind.SUBMITTED.value),
            "retracted": sum(1 for e in self.entries.values()
                             if e.status == EntryStatus.RETRACTED),
        }


class UngatedMemory(BaseMemory):
    """MedAgentSim-style: everything is written, nothing is checked."""

    name = "ungated"
    gated = False


class GatedMemory(BaseMemory):
    """SafeWard (G3): a write needs a confirmed outcome and no safety veto."""

    name = "gated"
    gated = True

    def admits(self, case: Case, result: EpisodeResult) -> tuple[bool, str]:
        if not (result.verdict and result.verdict.correct):
            return False, "outcome not confirmed against the ground truth"
        if any(v.decision == SafetyDecision.VETO for v in result.safety_verdicts):
            return False, "a safety verdict was VETO"
        return True, ""

    def admits_lesson(self, source_case_id: str, claimed_diagnosis: str) -> tuple[bool, str]:
        truth = self.truth.get(source_case_id)
        if truth is None:
            # Fail closed: an unverifiable claim is exactly what the gate is for.
            return False, f"no ground truth known for source case {source_case_id!r}"
        if not exact_match(truth, claimed_diagnosis):
            return False, (f"claim {claimed_diagnosis!r} does not match the source case "
                           f"ground truth {truth!r}")
        return True, ""

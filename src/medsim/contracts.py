"""Structured contracts shared by every module.

Everything that crosses a module boundary (dataset -> env, env -> agents,
env -> eval, eval -> reports) is one of these pydantic models, so the JSON
written to disk is always schema-valid and re-loadable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------------- #
class Case(BaseModel):
    """One clinical case, split into the slices each agent is allowed to see."""

    id: str
    source: str = Field(description="Dataset name, e.g. 'medqa', 'nejm'.")
    objective: str = Field(description="The ONLY case text the doctor sees up front.")
    patient_info: dict[str, Any] | str = Field(description="Patient agent's knowledge.")
    physical_exam: dict[str, Any] | str = Field(default_factory=dict)
    test_results: dict[str, Any] | str = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list, description="Image URLs/paths.")
    correct_diagnosis: str
    answer_options: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Dialogue
# --------------------------------------------------------------------------- #
class Role(str, Enum):
    DOCTOR = "doctor"
    PATIENT = "patient"
    MEASUREMENT = "measurement"
    SYSTEM = "system"


class Message(BaseModel):
    role: Role
    content: str
    turn: int
    raw: Optional[str] = Field(default=None, description="Original model output, if it was normalized.")


class ActionType(str, Enum):
    ASK = "ask"                    # free-text question to the patient
    REQUEST_TEST = "request_test"  # routed to the measurement agent
    REQUEST_IMAGE = "request_image"
    DIAGNOSE = "diagnose"          # terminates the episode


class DoctorAction(BaseModel):
    """Parsed form of one doctor utterance."""

    type: ActionType
    text: str
    test_name: Optional[str] = None
    diagnosis: Optional[str] = None


class TestRequest(BaseModel):
    test_name: str
    turn: int


class TestResult(BaseModel):
    test_name: str
    result: str
    found: bool = Field(description="False if the agent reported normal/unavailable.")


# --------------------------------------------------------------------------- #
# Episode state & results
# --------------------------------------------------------------------------- #
class EpisodeStatus(str, Enum):
    RUNNING = "running"
    DIAGNOSED = "diagnosed"
    TIMEOUT = "timeout"
    ERROR = "error"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    latency_s: float = 0.0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.calls += other.calls
        self.latency_s += other.latency_s


class EpisodeState(BaseModel):
    """Mutable state owned by the EpisodeRunner for one case."""

    case_id: str
    max_turns: int
    turn: int = 0
    status: EpisodeStatus = EpisodeStatus.RUNNING
    transcript: list[Message] = Field(default_factory=list)
    tests_requested: list[TestRequest] = Field(default_factory=list)
    test_results: list[TestResult] = Field(default_factory=list)
    final_diagnosis: Optional[str] = None
    usage: dict[str, Usage] = Field(default_factory=dict)
    error: Optional[str] = None

    def add_message(self, role: Role, content: str, raw: Optional[str] = None) -> Message:
        msg = Message(role=role, content=content, turn=self.turn, raw=raw if raw != content else None)
        self.transcript.append(msg)
        return msg

    def record_usage(self, role: str, usage: Usage) -> None:
        self.usage.setdefault(role, Usage()).add(usage)


class JudgeVerdict(BaseModel):
    correct: bool
    method: str
    raw: str = ""


class EpisodeResult(BaseModel):
    """What gets written, one line per case, to runs/<run>/episodes.jsonl."""

    case_id: str
    source: str
    correct_diagnosis: str
    final_diagnosis: Optional[str]
    status: EpisodeStatus
    verdict: Optional[JudgeVerdict] = None
    num_turns: int
    num_tests: int
    transcript: list[Message]
    tests_requested: list[TestRequest]
    usage: dict[str, Usage]
    error: Optional[str] = None
    finished_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def correct(self) -> bool:
        return bool(self.verdict and self.verdict.correct)


class RunMetrics(BaseModel):
    run_name: str
    num_cases: int
    accuracy: float
    accuracy_ci95: tuple[float, float]
    num_correct: int
    avg_turns: float
    avg_tests: float
    timeout_rate: float
    error_rate: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_latency_s: float
    per_role_usage: dict[str, Usage]

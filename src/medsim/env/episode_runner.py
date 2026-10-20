"""The clinic environment: runs one doctor-patient-measurement episode.

State machine per turn (paper Fig. 2a, Conversation Phase):

    doctor speaks ─┬─ DIAGNOSIS READY ─► stop (diagnosed)
                   ├─ REQUEST TEST    ─► measurement agent ─► back to doctor
                   ├─ REQUEST IMAGES  ─► image reader       ─► back to doctor
                   └─ question        ─► patient agent      ─► back to doctor
    turn == max_turns-1 ─► doctor is forced to diagnose; no diagnosis ─► timeout
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from medsim.agents import DoctorAgent, MeasurementAgent, PatientAgent
from medsim.contracts import (
    ActionType,
    Case,
    EpisodeResult,
    EpisodeState,
    EpisodeStatus,
    Role,
    TestRequest,
)
from medsim.eval.judge import Judge
from medsim.memory.buffer import MemoryBuffer, NullMemory
from medsim.providers.base import LLMProvider, ProviderConfigError
from medsim.tools import ImageReader, parse_doctor_action

log = logging.getLogger(__name__)

# Optional per-message callback, used for live printing in the CLI.
OnMessage = Callable[[Role, str], None]


class EpisodeRunner:
    def __init__(
        self,
        doctor: LLMProvider,
        patient: LLMProvider,
        measurement: LLMProvider,
        judge: Judge,
        image_reader: Optional[ImageReader] = None,
        memory: Optional[MemoryBuffer] = None,
        max_turns: int = 20,
        on_message: Optional[OnMessage] = None,
    ):
        self.doctor_llm, self.patient_llm, self.measurement_llm = doctor, patient, measurement
        self.judge = judge
        self.image_reader = image_reader or ImageReader(None)
        self.memory = memory or NullMemory()  # Phase 2: experience replay
        self.max_turns = max_turns
        self.on_message = on_message

    def _say(self, state: EpisodeState, role: Role, text: str, raw: Optional[str] = None) -> None:
        state.add_message(role, text, raw=raw)
        if self.on_message:
            self.on_message(role, text)

    def run(self, case: Case) -> EpisodeResult:
        state = EpisodeState(case_id=case.id, max_turns=self.max_turns)
        doctor = DoctorAgent(self.doctor_llm, case)
        patient = PatientAgent(self.patient_llm, case)
        measurement = MeasurementAgent(self.measurement_llm, case)

        try:
            self._conversation(state, doctor, patient, measurement, case)
        except ProviderConfigError:
            raise  # misconfiguration: abort the whole run instead of failing every case
        except Exception as exc:  # keep the batch alive; record the failure
            log.exception("Episode %s failed", case.id)
            state.status, state.error = EpisodeStatus.ERROR, f"{type(exc).__name__}: {exc}"

        verdict = None
        if state.final_diagnosis:
            verdict = self.judge.grade(case.correct_diagnosis, state.final_diagnosis)
            state.record_usage("judge", self.judge.last_usage)

        result = EpisodeResult(
            case_id=case.id,
            source=case.source,
            correct_diagnosis=case.correct_diagnosis,
            final_diagnosis=state.final_diagnosis,
            status=state.status,
            verdict=verdict,
            num_turns=state.turn,
            num_tests=len(state.tests_requested),
            transcript=state.transcript,
            tests_requested=state.tests_requested,
            usage=state.usage,
            error=state.error,
        )
        self.memory.add(case, result)
        return result

    def _conversation(self, state, doctor, patient, measurement, case: Case) -> None:
        while state.turn < state.max_turns:
            force = state.turn == state.max_turns - 1
            raw = doctor.act(state, force_diagnosis=force)
            action = parse_doctor_action(raw, prefer_diagnosis=force)
            self._say(state, Role.DOCTOR, action.text, raw=raw)  # canonical single action

            if action.type == ActionType.DIAGNOSE:
                state.final_diagnosis = action.diagnosis
                state.status = EpisodeStatus.DIAGNOSED
                state.turn += 1
                return
            if action.type == ActionType.REQUEST_TEST:
                state.tests_requested.append(TestRequest(test_name=action.test_name, turn=state.turn))
                result = measurement.run_test(action.test_name, state)
                state.test_results.append(result)
                self._say(state, Role.MEASUREMENT, result.result)
            elif action.type == ActionType.REQUEST_IMAGE:
                report, usage = self.image_reader.describe(case.images)
                state.record_usage("image_reader", usage)
                self._say(state, Role.MEASUREMENT, f"IMAGE REPORT: {report}")
            else:
                self._say(state, Role.PATIENT, patient.respond(state))
            state.turn += 1

        state.status = EpisodeStatus.TIMEOUT

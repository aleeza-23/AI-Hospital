"""The clinic environment: runs one doctor-patient-measurement episode.

State machine per turn (paper Fig. 2a, Conversation Phase):

    doctor speaks ─┬─ DIAGNOSIS READY ─► stop (diagnosed)
                   ├─ REQUEST TEST    ─► measurement agent ─► back to doctor
                   ├─ REQUEST IMAGES  ─► image reader       ─► back to doctor
                   └─ question        ─► patient agent      ─► back to doctor
    turn == max_turns-1 ─► doctor is forced to diagnose; no diagnosis ─► timeout

Phase 2 hooks (each off by default, switched by config `features`):
    memory.context_for(case)  before the consultation  -> doctor prompt   (G3)
    doctor.write_plan()       after the diagnosis       -> PLAN            (ask_plan)
    safety.review(plan)       approve / revise / veto   -> one revision    (G2)
    memory.add(case, result)  after judging             -> gated storage   (G3)
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from medsim.agents import DoctorAgent, MeasurementAgent, PatientAgent
from medsim.agents import prompts as P
from medsim.contracts import (
    ActionType,
    Case,
    EpisodeResult,
    EpisodeState,
    EpisodeStatus,
    Role,
    SafetyDecision,
    TestRequest,
)
from medsim.safety.base import SafetyReviewer
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
        safety: Optional[SafetyReviewer] = None,
        ask_plan: bool = False,
        cot: bool = False,
    ):
        self.doctor_llm, self.patient_llm, self.measurement_llm = doctor, patient, measurement
        self.judge = judge
        self.image_reader = image_reader or ImageReader(None)
        self.memory = memory or NullMemory()  # G3: experience replay (+ gate)
        self.safety = safety                  # G2: plan review
        self.ask_plan = ask_plan or safety is not None
        self.cot = cot
        self.max_turns = max_turns
        self.on_message = on_message

    def _say(self, state: EpisodeState, role: Role, text: str, raw: Optional[str] = None) -> None:
        state.add_message(role, text, raw=raw)
        if self.on_message:
            self.on_message(role, text)

    def run(self, case: Case) -> EpisodeResult:
        state = EpisodeState(case_id=case.id, max_turns=self.max_turns)
        memory_context = self.memory.context_for(case)
        doctor = DoctorAgent(self.doctor_llm, case, memory_context=memory_context, cot=self.cot)
        patient = PatientAgent(self.patient_llm, case)
        measurement = MeasurementAgent(self.measurement_llm, case)
        plan, final_plan, verdicts = None, None, []

        try:
            self._conversation(state, doctor, patient, measurement, case)
            if self.ask_plan and state.final_diagnosis:
                plan, final_plan, verdicts = self._plan_and_review(state, doctor, case)
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
            plan=plan,
            final_plan=final_plan,
            safety_verdicts=verdicts,
            memory_context=memory_context,
        )
        self.memory.add(case, result)
        return result

    def _plan_and_review(self, state: EpisodeState, doctor: DoctorAgent, case: Case):
        plan = doctor.write_plan(state, state.final_diagnosis)
        self._say(state, Role.DOCTOR, f"PLAN: {plan}")
        final_plan, verdicts = plan, []
        if self.safety is None:
            return plan, final_plan, verdicts
        verdict = self.safety.review(case, state.final_diagnosis, plan, state.transcript)
        state.record_usage(self.safety.name, self.safety.last_usage)
        verdicts.append(verdict)
        self._say(state, Role.SAFETY_OFFICER, f"{verdict.decision.value.upper()}: {'; '.join(verdict.issues)}")
        if verdict.decision != SafetyDecision.APPROVE:
            feedback = P.DOCTOR_REVISE_PLAN.format(
                decision="vetoed" if verdict.decision == SafetyDecision.VETO else "questioned",
                issues="\n".join(f"- {i}" for i in verdict.issues) or "- unspecified")
            final_plan = doctor.write_plan(state, state.final_diagnosis, feedback=feedback)
            self._say(state, Role.DOCTOR, f"PLAN (revised): {final_plan}")
            second = self.safety.review(case, state.final_diagnosis, final_plan, state.transcript)
            state.record_usage(self.safety.name, self.safety.last_usage)
            verdicts.append(second)
            self._say(state, Role.SAFETY_OFFICER, f"{second.decision.value.upper()}: {'; '.join(second.issues)}")
        return plan, final_plan, verdicts

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

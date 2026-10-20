"""Doctor, patient and measurement agents.

Each agent builds its *own view* of the shared transcript, so information
isolation is enforced in code: the doctor never sees the case data, the
patient never sees test results, and the measurement agent only sees the
current request.
"""
from __future__ import annotations

from typing import Any

from medsim.agents import prompts as P
from medsim.contracts import ActionType, Case, EpisodeState, Role, TestResult
from medsim.providers.base import ChatMessage, LLMProvider
from medsim.tools.actions import TOOL_SPEC, parse_doctor_action


def format_info(obj: Any, indent: int = 0) -> str:
    """Render nested case dicts as readable bullet text for prompts."""
    pad = "  " * indent
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            key = str(k).replace("_", " ")
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}- {key}:")
                lines.append(format_info(v, indent + 1))
            else:
                lines.append(f"{pad}- {key}: {v}")
        return "\n".join(lines)
    if isinstance(obj, list):
        return "\n".join(
            format_info(v, indent) if isinstance(v, (dict, list)) else f"{pad}- {v}" for v in obj
        )
    return f"{pad}{obj}"


class BaseAgent:
    role: Role

    def __init__(self, provider: LLMProvider, case: Case):
        self.provider = provider
        self.case = case

    def _call(self, messages: list[ChatMessage], state: EpisodeState, **kw: Any) -> str:
        resp = self.provider.chat(messages, **kw)
        state.record_usage(self.role.value, resp.usage)
        return resp.text


class DoctorAgent(BaseAgent):
    role = Role.DOCTOR

    def system_prompt(self, state: EpisodeState, force_diagnosis: bool = False) -> str:
        # The turn counter lives in the system prompt, not the dialogue, so the
        # model does not learn to echo it in its replies.
        status = P.DOCTOR_FORCE_DIAGNOSIS if force_diagnosis else P.DOCTOR_TURN_NOTE.format(
            turn=state.turn, max_turns=state.max_turns
        )
        return P.DOCTOR_SYSTEM.format(
            max_turns=state.max_turns, tool_spec=TOOL_SPEC, objective=self.case.objective, status=status
        )

    def build_messages(self, state: EpisodeState, force_diagnosis: bool = False) -> list[ChatMessage]:
        msgs: list[ChatMessage] = [{"role": "system", "content": self.system_prompt(state, force_diagnosis)}]
        for m in state.transcript:
            if m.role == Role.DOCTOR:
                msgs.append({"role": "assistant", "content": m.content})
            elif m.role == Role.PATIENT:
                msgs.append({"role": "user", "content": f"Patient: {m.content}"})
            elif m.role == Role.MEASUREMENT:
                msgs.append({"role": "user", "content": f"Measurement: {m.content}"})
        if len(msgs) == 1:
            msgs.append({"role": "user", "content": "The patient has entered the room. Begin the consultation."})
        return msgs

    def act(self, state: EpisodeState, force_diagnosis: bool = False) -> str:
        return self._call(self.build_messages(state, force_diagnosis), state)


class PatientAgent(BaseAgent):
    role = Role.PATIENT

    def build_messages(self, state: EpisodeState) -> list[ChatMessage]:
        msgs: list[ChatMessage] = [{
            "role": "system",
            "content": P.PATIENT_SYSTEM.format(patient_info=format_info(self.case.patient_info)),
        }]
        for m in state.transcript:
            if m.role == Role.DOCTOR and parse_doctor_action(m.content).type == ActionType.ASK:
                msgs.append({"role": "user", "content": m.content})
            elif m.role == Role.PATIENT:
                msgs.append({"role": "assistant", "content": m.content})
        return msgs

    def respond(self, state: EpisodeState) -> str:
        return self._call(self.build_messages(state), state)


class MeasurementAgent(BaseAgent):
    role = Role.MEASUREMENT

    def measurement_info(self) -> str:
        parts = []
        if self.case.physical_exam:
            parts.append("Physical examination findings:\n" + format_info(self.case.physical_exam))
        if self.case.test_results:
            parts.append("Test results:\n" + format_info(self.case.test_results))
        return "\n\n".join(parts) or "No data available."

    def available_tests(self) -> str:
        names = []
        for section in (self.case.physical_exam, self.case.test_results):
            if isinstance(section, dict):
                names += list(section) + [
                    f"{k}.{sub}" for k, v in section.items() if isinstance(v, dict) for sub in v
                ]
        return ", ".join(names) or "see patient data"

    def build_messages(self, test_name: str) -> list[ChatMessage]:
        system = P.MEASUREMENT_SYSTEM.format(
            available=self.available_tests(), measurement_info=self.measurement_info()
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": f"The doctor requests: {test_name}"},
        ]

    def run_test(self, test_name: str, state: EpisodeState) -> TestResult:
        text = self._call(self.build_messages(test_name), state)
        found = "normal readings" not in text.lower()
        return TestResult(test_name=test_name, result=text, found=found)

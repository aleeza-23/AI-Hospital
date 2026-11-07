"""G1: Triage Nurse agent (interview + vitals tool) and the B0 single-LLM baseline.

The nurse sees only what a triage desk sees on arrival (age, sex, arrival mode,
the patient's own words). It must ASK the patient agent and call GET_VITALS to
learn more, then commit to a KTAS level with red flags. The hidden ED diagnosis
is never shown to any agent.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from medsim.contracts import Message, Role, Usage
from medsim.providers.base import LLMProvider, ProviderConfigError
from medsim.tools.actions import strip_noise
from medsim.triage.ktas import TriageCase

KTAS_GUIDE = """KTAS levels (1 = most urgent):
1 Resuscitation: needs immediate life-saving intervention (cardiac/respiratory arrest, \
unresponsive or responds only to pain, severe respiratory distress, shock, SpO2 < 90%).
2 Emergent: high-risk situation or danger-zone vitals; must be seen within 10 min (chest \
pain suggestive of ACS, stroke signs, new confusion, severe dyspnea, GI bleeding with \
instability, severe pain >= 8/10, HR > 120, RR > 24, SBP < 90).
3 Urgent: stable but needs several resources (labs, imaging, IV), within 30 min (moderate \
pain, fever with concerning features, abdominal pain in older adults).
4 Less urgent: one resource expected, within 60 min (minor injuries, simple lacerations, \
mild pain, urinary symptoms in a well patient).
5 Non-urgent: no resources expected, within 120 min (prescription refills, minor complaints)."""

NURSE_SYSTEM = """You are an experienced emergency department triage nurse. Assign a KTAS level \
quickly and safely. When in doubt between two levels, choose the MORE urgent one: \
under-triage is the most dangerous error.

{guide}

On arrival you know only: {arrival}

Each reply is exactly ONE action:
- "ASK: <one short question for the patient>"
- "GET_VITALS" (returns the measured vital signs)
- "TRIAGE: <1-5> | RED FLAGS: <comma-separated, or none> | REASON: <one sentence>"
You may take at most {max_actions} actions in total. {status}"""

TRIAGE_PATIENT_SYSTEM = """You are a patient arriving at an emergency department, speaking to \
the triage nurse. Answer in 1-2 short sentences, in plain words, using only the facts below. \
Do not guess a diagnosis. If you are not alert, a family member answers briefly for you.

Your facts:
- {age}-year-old {sex}; arrived by {arrival_mode}
- Main problem in your words: "{complaint}"
- Injury: {injury}
- Consciousness: {mental}
- Pain: {pain}"""

B0_PROMPT = """You are an emergency department triage nurse. Given the full arrival record, \
assign a KTAS level.

{guide}

Record:
{record}

Reply exactly: "TRIAGE: <1-5> | RED FLAGS: <comma-separated, or none> | REASON: <one sentence>\""""

_TRIAGE = re.compile(r"TRIAGE\s*[:\-]\s*\[?([1-5])", re.IGNORECASE)
_FLAGS = re.compile(r"RED\s*FLAGS?\s*[:\-]\s*([^|\n]*)", re.IGNORECASE)
_ASK = re.compile(r"ASK\s*[:\-]\s*(.+)", re.IGNORECASE)
_VITALS = re.compile(r"GET[_\s]?VITALS", re.IGNORECASE)


class TriageDecision(BaseModel):
    case_id: str
    method: str
    level: Optional[int]
    red_flags: list[str] = Field(default_factory=list)
    reason: str = ""
    num_actions: int = 0
    used_vitals: bool = False
    transcript: list[Message] = Field(default_factory=list)
    usage: dict[str, Usage] = Field(default_factory=dict)
    error: Optional[str] = None


def arrival_summary(c: TriageCase) -> str:
    return (f'{c.age}-year-old {c.sex}, arrived by {c.arrival_mode}, says: "{c.chief_complaint}".')


def full_record(c: TriageCase) -> str:
    pain = f"yes, {c.pain_score}/10" if c.pain and c.pain_score is not None else ("yes" if c.pain else "no")
    return (f"- {arrival_summary(c)}\n- Injury: {'yes' if c.injury else 'no'}\n"
            f"- Consciousness: {c.mental}\n- Pain: {pain}\n- Vitals: {c.vitals.describe()}")


def parse_triage(text: str) -> tuple[Optional[int], list[str], str]:
    m = _TRIAGE.search(text)
    if not m:
        return None, [], ""
    flags_m = _FLAGS.search(text)
    flags = []
    if flags_m:
        flags = [f.strip() for f in flags_m.group(1).split(",")
                 if f.strip() and f.strip().lower() not in ("none", "no", "n/a")]
    reason = text.split("REASON", 1)[1].lstrip(" :-") if "REASON" in text.upper() else ""
    return int(m.group(1)), flags, reason.strip()[:300]


def _record(d: TriageDecision, role: str, usage: Usage) -> None:
    d.usage.setdefault(role, Usage()).add(usage)


class TriageNurse:
    def __init__(self, nurse: LLMProvider, patient: LLMProvider, max_actions: int = 4):
        self.nurse, self.patient, self.max_actions = nurse, patient, max_actions

    def _patient_reply(self, c: TriageCase, d: TriageDecision) -> str:
        pain = (f"yes, about {c.pain_score}/10" if c.pain and c.pain_score is not None
                else ("yes" if c.pain else "no pain"))
        system = TRIAGE_PATIENT_SYSTEM.format(
            age=c.age, sex=c.sex, arrival_mode=c.arrival_mode, complaint=c.chief_complaint,
            injury="yes" if c.injury else "no", mental=c.mental, pain=pain)
        msgs = [{"role": "system", "content": system}]
        for m in d.transcript:
            if m.role == Role.TRIAGE_NURSE and m.content.upper().startswith("ASK"):
                msgs.append({"role": "user", "content": m.content.split(":", 1)[1].strip()})
            elif m.role == Role.PATIENT:
                msgs.append({"role": "assistant", "content": m.content})
        resp = self.patient.chat(msgs)
        _record(d, "patient", resp.usage)
        return resp.text

    def triage(self, c: TriageCase) -> TriageDecision:
        d = TriageDecision(case_id=c.id, method="triage_nurse", level=None)
        try:
            for turn in range(self.max_actions):
                force = turn == self.max_actions - 1
                status = ("You must now reply with TRIAGE." if force
                          else f"Actions used: {turn} of {self.max_actions}.")
                msgs = [{"role": "system", "content": NURSE_SYSTEM.format(
                    guide=KTAS_GUIDE, arrival=arrival_summary(c), max_actions=self.max_actions,
                    status=status)}]
                for m in d.transcript:
                    msgs.append({"role": "assistant" if m.role == Role.TRIAGE_NURSE else "user",
                                 "content": m.content if m.role == Role.TRIAGE_NURSE
                                 else f"{m.role.value.title()}: {m.content}"})
                if len(msgs) == 1:
                    msgs.append({"role": "user", "content": "The patient is at the triage desk."})
                resp = self.nurse.chat(msgs)
                _record(d, "triage_nurse", resp.usage)
                raw = strip_noise(resp.text)
                d.num_actions += 1

                level, flags, reason = parse_triage(raw)
                ask, vit = _ASK.search(raw), _VITALS.search(raw)
                first = min((m.start() for m in (_TRIAGE.search(raw), ask, vit) if m), default=None)
                if level and (force or first == _TRIAGE.search(raw).start()):
                    d.level, d.red_flags, d.reason = level, flags, reason
                    d.transcript.append(Message(role=Role.TRIAGE_NURSE, content=raw, turn=turn))
                    return d
                if vit and (not ask or vit.start() < ask.start()):
                    d.used_vitals = True
                    d.transcript.append(Message(role=Role.TRIAGE_NURSE, content="GET_VITALS", turn=turn, raw=raw))
                    d.transcript.append(Message(role=Role.TOOL, content=f"Vitals: {c.vitals.describe()}", turn=turn))
                elif ask:
                    q = ask.group(1).strip().splitlines()[0]
                    d.transcript.append(Message(role=Role.TRIAGE_NURSE, content=f"ASK: {q}", turn=turn, raw=raw))
                    d.transcript.append(Message(role=Role.PATIENT, content=self._patient_reply(c, d), turn=turn))
                else:  # unparseable: treat as a question so the episode continues
                    d.transcript.append(Message(role=Role.TRIAGE_NURSE, content=f"ASK: {raw[:200]}", turn=turn))
                    d.transcript.append(Message(role=Role.PATIENT, content=self._patient_reply(c, d), turn=turn))
            d.error = "no triage level after max actions"
        except ProviderConfigError:
            raise
        except Exception as exc:  # recorded as a reliability failure, run continues
            d.error = f"{type(exc).__name__}: {exc}"
        return d


def single_llm_triage(provider: LLMProvider, c: TriageCase) -> TriageDecision:
    """B0: one prompt with the full record, no interaction."""
    d = TriageDecision(case_id=c.id, method="single_llm", level=None, used_vitals=True, num_actions=1)
    try:
        resp = provider.chat([{"role": "user", "content": B0_PROMPT.format(guide=KTAS_GUIDE, record=full_record(c))}])
        _record(d, "triage_nurse", resp.usage)
        d.level, d.red_flags, d.reason = parse_triage(strip_noise(resp.text))
        d.transcript.append(Message(role=Role.TRIAGE_NURSE, content=resp.text, turn=0))
        if d.level is None:
            d.error = "unparseable triage level"
    except ProviderConfigError:
        raise
    except Exception as exc:
        d.error = f"{type(exc).__name__}: {exc}"
    return d

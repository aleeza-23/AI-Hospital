"""Doctor tool interface.

The doctor acts through plain-text markers (provider-agnostic, works with any
open model, mirrors AgentClinic/MedAgentSim):

    REQUEST TEST: <test name>     -> routed to the measurement agent
    REQUEST IMAGES                -> routed to the image reader (VLM)
    DIAGNOSIS READY: <diagnosis>  -> ends the episode
    anything else                 -> a question for the patient

Exactly ONE action is taken per turn. Models sometimes pack several questions,
tests and a diagnosis into one reply, which would bypass the turn budget, so the
reply is reduced to its first action and stored in that canonical form.
"""
from __future__ import annotations

import re

from medsim.contracts import ActionType, DoctorAction

_DIAG = re.compile(r"DIAGNOSIS\s+READY\s*[:\-]\s*(.+)", re.IGNORECASE | re.DOTALL)
_TEST = re.compile(r"REQUEST\s+TEST\s*[:\-]\s*(.+)", re.IGNORECASE)
_IMAGE = re.compile(r"REQUEST\s+IMAGES?\b", re.IGNORECASE)

# Echoed turn-counter notes and speaker labels like "**Dr. Agent:**".
_NOISE = [
    re.compile(r"\[(?:You have used|Interaction)[^\]]*\]", re.IGNORECASE),
    re.compile(r"\*{0,2}\s*(?:Dr\.?\s*Agent|Doctor)\s*:?\s*\*{0,2}\s*:?", re.IGNORECASE),
]

TOOL_SPEC = (
    'To order an examination or test, write exactly "REQUEST TEST: [test name]" '
    "(e.g. REQUEST TEST: Chest_X-Ray). "
    'To view the patient\'s medical images (if any), write "REQUEST IMAGES". '
    'When you are confident, write "DIAGNOSIS READY: [diagnosis]" with a single, specific diagnosis.'
)


_ANY_MARKER = re.compile(r"REQUEST\s+TEST|REQUEST\s+IMAGES?\b|DIAGNOSIS\s+READY", re.IGNORECASE)


def _clean(s: str) -> str:
    # Stop at the next marker: models sometimes run actions together on one line
    # ("...with contrastREQUEST TEST: ...").
    s = _ANY_MARKER.split(s, maxsplit=1)[0]
    s = s.strip().splitlines()[0] if s.strip() else ""
    return s.strip().rstrip(".").strip("[]*\"'` ").strip()


def strip_noise(text: str) -> str:
    for rx in _NOISE:
        text = rx.sub("", text)
    return text.strip()


def parse_doctor_action(text: str, prefer_diagnosis: bool = False) -> DoctorAction:
    """Reduce a doctor reply to one action.

    The earliest marker in the text wins (so 'REQUEST TEST ... DIAGNOSIS READY'
    runs the test first). On the forced final turn `prefer_diagnosis` makes a
    diagnosis win wherever it appears.
    """
    text = strip_noise(text)
    found = [(m.start(), kind, m) for kind, rx in
             ((ActionType.DIAGNOSE, _DIAG), (ActionType.REQUEST_TEST, _TEST), (ActionType.REQUEST_IMAGE, _IMAGE))
             if (m := rx.search(text))]
    if found:
        diag = [f for f in found if f[1] == ActionType.DIAGNOSE]
        _, kind, m = diag[0] if (prefer_diagnosis and diag) else min(found, key=lambda f: f[0])
        if kind == ActionType.DIAGNOSE:
            dx = _clean(m.group(1))
            return DoctorAction(type=kind, text=f"DIAGNOSIS READY: {dx}", diagnosis=dx)
        if kind == ActionType.REQUEST_TEST:
            name = _clean(m.group(1))
            return DoctorAction(type=kind, text=f"REQUEST TEST: {name}", test_name=name)
        return DoctorAction(type=kind, text="REQUEST IMAGES")
    # A question: keep only the first paragraph (one question per turn).
    first = re.split(r"\n\s*\n", text, maxsplit=1)[0].strip()
    return DoctorAction(type=ActionType.ASK, text=first or text)

"""B1: deterministic, rule-based triage (non-agentic baseline for G1).

A simplified KTAS/ESI-style decision procedure over vitals, consciousness,
pain and complaint keywords. Thresholds follow common ESI danger-zone vitals
(ESI Implementation Handbook v4) and were fixed before looking at the test set.
"""
from __future__ import annotations

from medsim.triage.ktas import TriageCase

LEVEL2_KEYWORDS = (
    "chest pain", "chest discomfort", "dyspnea", "short of breath", "mental change",
    "seizure", "syncope", "hematemesis", "melena", "weakness", "stroke", "dysarthria",
    "suicid", "overdose", "anaphyla", "hemoptysis", "palpitation",
)


def rule_based_ktas(case: TriageCase) -> tuple[int, list[str]]:
    v, flags = case.vitals, []
    # Level 1: needs immediate life-saving intervention.
    if case.mental in ("responds only to pain", "unresponsive"):
        flags.append(f"consciousness: {case.mental}")
    if v.spo2 is not None and v.spo2 < 90:
        flags.append(f"SpO2 {v.spo2:g}%")
    if v.sbp is not None and v.sbp < 80:
        flags.append(f"SBP {v.sbp:g}")
    if flags:
        return 1, flags

    # Level 2: high-risk situation or danger-zone vitals.
    danger = []
    if v.hr is not None and (v.hr > 120 or v.hr < 50):
        danger.append(f"HR {v.hr:g}")
    if v.rr is not None and (v.rr > 24 or v.rr < 10):
        danger.append(f"RR {v.rr:g}")
    if v.sbp is not None and (v.sbp < 90 or v.sbp > 200):
        danger.append(f"SBP {v.sbp:g}")
    if v.temp_c is not None and v.temp_c >= 39.5:
        danger.append(f"T {v.temp_c:g}")
    if v.spo2 is not None and v.spo2 < 94:
        danger.append(f"SpO2 {v.spo2:g}%")
    complaint = case.chief_complaint.lower()
    kw = [k for k in LEVEL2_KEYWORDS if k in complaint]
    if case.mental == "responds to verbal stimuli":
        danger.append("altered consciousness")
    if danger or kw or (case.pain_score is not None and case.pain_score >= 8):
        return 2, danger + [f"complaint: {k}" for k in kw]

    # Levels 3-5 by expected resource need (proxied by pain, injury, age, arrival).
    if (case.pain_score is not None and case.pain_score >= 5) or case.age >= 65 \
            or "ambulance" in case.arrival_mode or (v.temp_c is not None and v.temp_c >= 38):
        return 3, []
    if case.pain or case.injury:
        return 4, []
    return 5, []

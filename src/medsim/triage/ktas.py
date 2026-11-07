"""KTAS emergency triage dataset (Moon et al., PLOS ONE 2019) -> TriageCase.

Source: Kaggle "Emergency Service - Triage Application" (data.csv, ';'-separated,
latin-1). 1,267 adult ED visits with vitals, chief complaint, the triage nurse's
KTAS level (KTAS_RN) and an expert-consensus reference level (KTAS_expert).
KTAS uses 5 levels: 1 = resuscitation (most urgent) ... 5 = non-urgent.
"""
from __future__ import annotations

import csv
import random
from collections import defaultdict
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

SEX = {"1": "female", "2": "male"}
ARRIVAL = {"1": "walked in", "2": "public ambulance", "3": "private vehicle",
           "4": "private ambulance", "5": "other", "6": "other", "7": "other"}
MENTAL = {"1": "alert", "2": "responds to verbal stimuli", "3": "responds only to pain",
          "4": "unresponsive"}


class Vitals(BaseModel):
    sbp: Optional[float] = None
    dbp: Optional[float] = None
    hr: Optional[float] = None
    rr: Optional[float] = None
    temp_c: Optional[float] = None
    spo2: Optional[float] = None

    def describe(self) -> str:
        def f(v, unit=""):
            return "not recorded" if v is None else f"{v:g}{unit}"
        return (f"BP {f(self.sbp)}/{f(self.dbp)} mmHg, HR {f(self.hr)} bpm, RR {f(self.rr)}/min, "
                f"temperature {f(self.temp_c, ' C')}, SpO2 {f(self.spo2, '%')}")


class TriageCase(BaseModel):
    id: str
    age: int
    sex: str
    arrival_mode: str
    injury: bool
    chief_complaint: str
    mental: str
    pain: bool
    pain_score: Optional[int] = None
    vitals: Vitals
    ktas_expert: int            # reference label (ground truth)
    ktas_nurse: int             # what the real triage nurse assigned (human baseline)
    ed_diagnosis: str = ""      # hidden from every agent; for analysis only
    disposition: str = ""

    @property
    def high_acuity(self) -> bool:
        return self.ktas_expert <= 2


def _num(v: str) -> Optional[float]:
    v = (v or "").strip().replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None  # '??', '', '#BOÞ!' etc.


def load_ktas_csv(path: str | Path) -> list[TriageCase]:
    with open(path, encoding="latin-1", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    cases = []
    for i, r in enumerate(rows):
        nrs = _num(r.get("NRS_pain", ""))
        cases.append(TriageCase(
            id=f"ktas-{i:04d}",
            age=int(r["Age"]),
            sex=SEX.get(r["Sex"], "unknown"),
            arrival_mode=ARRIVAL.get(r["Arrival mode"], "other"),
            injury=r["Injury"] == "2",
            chief_complaint=r["Chief_complain"].strip(),
            mental=MENTAL.get(r["Mental"], "alert"),
            pain=r["Pain"] == "1",
            pain_score=int(nrs) if nrs is not None else None,
            vitals=Vitals(sbp=_num(r["SBP"]), dbp=_num(r["DBP"]), hr=_num(r["HR"]),
                          rr=_num(r["RR"]), temp_c=_num(r["BT"]), spo2=_num(r["Saturation"])),
            ktas_expert=int(r["KTAS_expert"]),
            ktas_nurse=int(r["KTAS_RN"]),
            ed_diagnosis=r["Diagnosis in ED"].strip(),
            disposition=r["Disposition"],
        ))
    return cases


def stratified_split(cases: list[TriageCase], dev_per_level: int, test_per_level: int,
                     seed: int = 0) -> tuple[list[TriageCase], list[TriageCase]]:
    """Fixed dev/test split, stratified by expert KTAS level so rare level-1/5 cases
    are represented. Levels with fewer cases contribute what they have."""
    by_level: dict[int, list[TriageCase]] = defaultdict(list)
    for c in cases:
        by_level[c.ktas_expert].append(c)
    rng = random.Random(seed)
    dev, test = [], []
    for level in sorted(by_level):
        pool = by_level[level][:]
        rng.shuffle(pool)
        dev += pool[:dev_per_level]
        test += pool[dev_per_level:dev_per_level + test_per_level]
    return dev, test

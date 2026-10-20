"""Dataset converters -> unified `Case` JSONL.

Sources (AgentClinic [39], the benchmark files MedAgentSim evaluates on):
  * OSCE format  (MedQA, MedQA-Ext, MIMIC-IV): OSCE_Examination{Objective_for_Doctor,
    Patient_Actor, Physical_Examination_Findings, Test_Results, Correct_Diagnosis}
  * NEJM format  (NEJM, NEJM-Ext): {image_url, question, patient_info, physical_exams, answers}
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Callable

from medsim.contracts import Case

AGENTCLINIC_RAW = "https://raw.githubusercontent.com/SamuelSchmidgall/AgentClinic/main/{}"

NEJM_OBJECTIVE = (
    "Assess and diagnose the patient. Medical images are available on request "
    '("REQUEST IMAGES").'
)


def from_osce(rec: dict[str, Any], idx: int, source: str) -> Case:
    osce = rec["OSCE_Examination"]
    extra = {k: v for k, v in osce.items() if k not in {
        "Objective_for_Doctor", "Patient_Actor", "Physical_Examination_Findings",
        "Test_Results", "Correct_Diagnosis",
    }}
    return Case(
        id=f"{source}-{idx:04d}",
        source=source,
        objective=osce["Objective_for_Doctor"],
        patient_info=osce["Patient_Actor"],
        physical_exam=osce.get("Physical_Examination_Findings", {}),
        test_results=osce.get("Test_Results", {}),
        correct_diagnosis=str(osce["Correct_Diagnosis"]).strip(),
        metadata=extra,
    )


def from_nejm(rec: dict[str, Any], idx: int, source: str) -> Case:
    answers = rec.get("answers", [])
    correct = next((a["text"] for a in answers if a.get("correct")), None)
    if correct is None:
        raise ValueError("NEJM record has no correct answer")
    return Case(
        id=f"{source}-{idx:04d}",
        source=source,
        # The NEJM 'question' text describes the image findings, so the doctor must
        # not see it; it only gets a generic objective (like AgentClinic).
        objective=NEJM_OBJECTIVE,
        patient_info=rec["patient_info"],
        physical_exam=rec.get("physical_exams", ""),
        images=[rec["image_url"]] if rec.get("image_url") else [],
        correct_diagnosis=correct.strip(),
        answer_options=[a["text"] for a in answers],
        metadata={"question": rec.get("question", ""), "type": rec.get("type")},
    )


# name -> (remote filename or None if credentialed, converter)
DATASETS: dict[str, tuple[str | None, Callable[[dict, int, str], Case]]] = {
    "medqa": ("agentclinic_medqa.jsonl", from_osce),
    "medqa_ext": ("agentclinic_medqa_extended.jsonl", from_osce),
    "nejm": ("agentclinic_nejm.jsonl", from_nejm),
    "nejm_ext": ("agentclinic_nejm_extended.jsonl", from_nejm),
    "mimiciv": (None, from_osce),  # PhysioNet-credentialed; supply the file locally
}


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def convert(name: str, records: Iterable[dict[str, Any]]) -> list[Case]:
    _, conv = DATASETS[name]
    return [conv(rec, i, name) for i, rec in enumerate(records)]


def save_cases(cases: Iterable[Case], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(c.model_dump_json() + "\n")


def load_cases(path: str | Path, limit: int | None = None, ids: set[str] | None = None) -> list[Case]:
    cases = [Case.model_validate(r) for r in iter_jsonl(path)]
    if ids:
        cases = [c for c in cases if c.id in ids]
    return cases[:limit] if limit else cases

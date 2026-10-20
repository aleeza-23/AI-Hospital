from pathlib import Path

from medsim.agents import DoctorAgent, PatientAgent
from medsim.contracts import EpisodeState, EpisodeStatus, Role
from medsim.data import load_cases
from medsim.env import EpisodeRunner
from medsim.eval import Judge, compute_metrics, exact_match
from medsim.providers.mock import MockProvider

SMOKE = Path(__file__).resolve().parents[1] / "data" / "smoke" / "smoke_cases.jsonl"


def make_runner(diagnosis="Myasthenia gravis", max_turns=10):
    return EpisodeRunner(
        doctor=MockProvider(role="doctor", diagnosis=diagnosis),
        patient=MockProvider(role="patient"),
        measurement=MockProvider(role="measurement"),
        judge=Judge(method="exact"),
        max_turns=max_turns,
    )


def test_end_to_end_mock_episode():
    case = load_cases(SMOKE)[0]
    res = make_runner().run(case)
    assert res.status == EpisodeStatus.DIAGNOSED
    assert res.correct
    assert res.num_tests == 1
    roles = [m.role for m in res.transcript]
    assert roles == [Role.DOCTOR, Role.PATIENT, Role.DOCTOR, Role.MEASUREMENT, Role.DOCTOR]


def test_timeout_when_turn_limit_hit():
    case = load_cases(SMOKE)[0]
    res = make_runner(max_turns=2).run(case)  # mock diagnoses on its 3rd utterance
    assert res.status == EpisodeStatus.TIMEOUT
    assert not res.correct


def test_information_isolation():
    case = load_cases(SMOKE)[0]
    state = EpisodeState(case_id=case.id, max_turns=10)
    doctor_view = str(DoctorAgent(MockProvider(), case).build_messages(state))
    assert "Acetylcholine" not in doctor_view and case.correct_diagnosis not in doctor_view
    state.add_message(Role.DOCTOR, "REQUEST TEST: Blood_Tests")
    state.add_message(Role.MEASUREMENT, "RESULTS: AChR antibodies elevated")
    patient_view = str(PatientAgent(MockProvider(), case).build_messages(state))
    assert "AChR" not in patient_view and "REQUEST TEST" not in patient_view


def test_metrics_and_exact_match():
    runner = make_runner()
    results = [runner.run(c) for c in load_cases(SMOKE)]
    m = compute_metrics("t", results)
    assert m.num_cases == 3 and m.num_correct == 1
    assert 0 <= m.accuracy_ci95[0] <= m.accuracy <= m.accuracy_ci95[1] <= 1
    assert exact_match("Diabetic ketoacidosis (new-onset type 1 diabetes)", "diabetic ketoacidosis")

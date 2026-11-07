"""Contracts the G2 (safety) and G3 (memory) components plug into."""
from pathlib import Path

from medsim.contracts import Case, EpisodeResult, SafetyDecision, SafetyVerdict
from medsim.data import load_cases
from medsim.env import EpisodeRunner
from medsim.eval import Judge
from medsim.memory import MemoryBuffer, MemoryRecord
from medsim.providers.mock import MockProvider
from medsim.safety import SafetyReviewer
from medsim.triage import quadratic_weighted_kappa, triage_metrics

SMOKE = Path(__file__).resolve().parents[1] / "data" / "smoke" / "smoke_cases.jsonl"


class VetoThenApprove(SafetyReviewer):
    name = "fake_reviewer"

    def __init__(self):
        super().__init__()
        self.calls = 0

    def review(self, case, diagnosis, plan, transcript):
        assert "correct_diagnosis" not in plan
        self.calls += 1
        if self.calls == 1:
            return SafetyVerdict(decision=SafetyDecision.VETO, issues=["unsafe dose"], reviewer=self.name)
        return SafetyVerdict(decision=SafetyDecision.APPROVE, reviewer=self.name)


class FixedMemory(MemoryBuffer):
    def __init__(self):
        self.added: list[EpisodeResult] = []

    def add(self, case: Case, result: EpisodeResult) -> None:
        self.added.append(result)

    def retrieve(self, query, k=3):
        return [MemoryRecord(case_id="old", kind="medical_record", summary="ptosis, fatigable weakness",
                             diagnosis="Myasthenia gravis")]


def runner(**kw):
    return EpisodeRunner(MockProvider(role="doctor", diagnosis="Myasthenia gravis"),
                         MockProvider(role="patient"), MockProvider(role="measurement"),
                         Judge(method="exact"), **kw)


def test_safety_hook_veto_triggers_one_revision():
    reviewer = VetoThenApprove()
    res = runner(safety=reviewer).run(load_cases(SMOKE)[0])
    assert res.plan is not None and res.final_plan is not None
    assert [v.decision for v in res.safety_verdicts] == [SafetyDecision.VETO, SafetyDecision.APPROVE]
    assert reviewer.calls == 2


def test_memory_hook_injects_context_and_receives_judged_result():
    mem = FixedMemory()
    res = runner(memory=mem).run(load_cases(SMOKE)[0])
    assert "Myasthenia gravis" in res.memory_context
    assert mem.added and mem.added[0].verdict is not None


def test_baseline_unchanged_when_features_off():
    res = runner().run(load_cases(SMOKE)[0])
    assert res.plan is None and res.safety_verdicts == [] and res.memory_context == ""


def test_triage_metrics():
    assert quadratic_weighted_kappa([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == 1.0
    m = triage_metrics([1, 2, 3], [2, 2, 2])  # level-1 patient rated 2 = under-triage
    assert m["under_triage"] == round(1 / 3, 4) and m["over_triage"] == round(1 / 3, 4)
    assert m["under_triage_high_acuity"] == 0.5

"""G3 Verified memory: offline tests (no API key, no network).

Covers TF-IDF retrieval order, the gate's admit/reject decisions, provenance,
retraction and the build_memory wiring, with MockProvider where an episode is
needed.
"""
import json
from pathlib import Path

import pytest

from medsim.contracts import (
    Case,
    EpisodeResult,
    EpisodeStatus,
    JudgeVerdict,
    SafetyDecision,
    SafetyVerdict,
)
from medsim.contracts import TestRequest as _TestRequest  # underscore: pytest collects names starting with Test

from medsim.data import load_cases
from medsim.env import EpisodeRunner
from medsim.eval import Judge
from medsim.eval.judge import exact_match
from medsim.memory import MemoryRecord, NullMemory, build_memory
from medsim.memory.embed import TfidfIndex, cosine, tokenize
from medsim.memory.stores import (
    EntryKind,
    EntryStatus,
    GatedMemory,
    UngatedMemory,
)
from medsim.providers.mock import MockProvider


# --------------------------------------------------------------------------- #
# Tokenisation
# --------------------------------------------------------------------------- #
def test_tokenize_drops_stopwords_and_boilerplate():
    tokens = tokenize("Assess and diagnose the patient presenting with double vision")
    assert tokens == ["double", "vision"]


def test_tokenize_singularises_so_plurals_match():
    assert tokenize("seizures") == tokenize("seizure") == ["seizure"]
    assert tokenize("headaches") == ["headache"]


def test_tokenize_keeps_short_clinical_words_out_but_signal_in():
    assert "pain" in tokenize("severe pain")
    assert tokenize("of in at to") == []


def test_tokenize_leaves_ss_us_is_endings_alone():
    # "ptosis" must not become "ptosi", or it stops matching itself.
    assert tokenize("ptosis") == ["ptosis"]
    assert tokenize("diabetes mellitus") == ["diabete", "mellitus"]


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #
def _index() -> TfidfIndex:
    ix = TfidfIndex()
    ix.add("mg", "ptosis, double vision, fatigable weakness worse after activity")
    ix.add("pneumonia", "productive cough, fever, crackles at the right base")
    ix.add("cll", "fatigue, painless lymphadenopathy, lymphocytosis on the blood film")
    return ix


def test_query_ranks_the_matching_document_first():
    hits = _index().query("drooping eyelid with double vision that worsens on use", k=3)
    assert hits and hits[0][0] == "mg"


def test_query_separates_two_presentations():
    ix = _index()
    assert ix.query("cough with fever and green sputum", k=1)[0][0] == "pneumonia"
    assert ix.query("painless swollen glands with a high lymphocyte count", k=1)[0][0] == "cll"


def test_query_returns_nothing_on_an_empty_index():
    assert TfidfIndex().query("double vision", k=3) == []


def test_query_returns_nothing_when_no_term_overlaps():
    assert _index().query("zzzz qqqq wwww", k=3) == []


def test_query_respects_k():
    ix = _index()
    ix.add("mg2", "ptosis and double vision in a young woman")
    assert len(ix.query("ptosis double vision", k=1)) == 1
    assert len(ix.query("ptosis double vision", k=5)) <= len(ix)


def test_scores_are_descending_and_ties_break_on_id():
    ix = TfidfIndex()
    ix.add("b", "double vision")
    ix.add("a", "double vision")
    hits = ix.query("double vision", k=2)
    assert [h[0] for h in hits] == ["a", "b"]           # identical scores, id order
    assert hits[0][1] >= hits[1][1]


def test_remove_takes_a_document_out_of_retrieval():
    ix = _index()
    ix.remove("mg")
    assert all(doc != "mg" for doc, _ in ix.query("ptosis double vision", k=3))
    assert len(ix) == 2


def test_index_is_incremental():
    ix = TfidfIndex()
    assert ix.query("double vision", k=1) == []
    ix.add("mg", "ptosis and double vision")
    assert ix.query("double vision", k=1)[0][0] == "mg"


def test_cosine_is_one_for_identical_text_and_zero_for_unrelated():
    assert cosine("chest pain radiating to the arm", "chest pain radiating to the arm") == 1.0
    assert cosine("chest pain", "molluscum contagiosum lesions") == 0.0


# --------------------------------------------------------------------------- #
# Fixtures for the stores
# --------------------------------------------------------------------------- #
MG = "Assess and diagnose the patient presenting with double vision and fatigable weakness."
PNEU = "Assess and diagnose the patient presenting with productive cough and fever."


def _case(cid: str = "c1", dx: str = "Myasthenia gravis", objective: str = MG) -> Case:
    return Case(id=cid, source="test", objective=objective,
                patient_info={"Demographics": "35-year-old"}, correct_diagnosis=dx)


def _res(case_id: str = "c1", dx: str = "Myasthenia gravis", correct: bool = True,
         veto: bool = False, tests: tuple[str, ...] = ()) -> EpisodeResult:
    verdicts = [SafetyVerdict(decision=SafetyDecision.VETO, issues=["unsafe"], reviewer="officer")] \
        if veto else []
    return EpisodeResult(
        case_id=case_id, source="test", correct_diagnosis="Myasthenia gravis", final_diagnosis=dx,
        status=EpisodeStatus.DIAGNOSED, verdict=JudgeVerdict(correct=correct, method="exact"),
        num_turns=3, num_tests=len(tests),
        transcript=[], tests_requested=[_TestRequest(test_name=t, turn=1) for t in tests],
        usage={}, safety_verdicts=verdicts)


# --------------------------------------------------------------------------- #
# Writing: where the two memories diverge
# --------------------------------------------------------------------------- #
def test_both_store_a_correct_case_as_a_medical_record():
    for memory in (UngatedMemory(), GatedMemory()):
        memory.add(_case(), _res(correct=True))
        kinds = {e.kind for e in memory.active}
        assert EntryKind.MEDICAL_RECORD in kinds, memory.name


def test_ungated_stores_a_lesson_from_a_wrong_episode_but_gated_does_not():
    """The MedAgentSim flaw this gap is about: a wrong answer stored as the answer."""
    ungated = UngatedMemory()
    ungated.add(_case(), _res(dx="Thyroid eye disease", correct=False))
    assert [e.diagnosis for e in ungated.active] == ["Thyroid eye disease"]

    gated = GatedMemory()
    gated.add(_case(), _res(dx="Thyroid eye disease", correct=False))
    assert gated.active == []


def test_gated_rejects_a_correct_outcome_that_was_vetoed_on_safety():
    gated = GatedMemory()
    gated.add(_case(), _res(correct=True, veto=True))
    assert gated.active == []
    assert any(a.event == "reject" and "VETO" in a.reason for a in gated.audit)


def test_gated_admits_a_correct_outcome_that_was_only_revised():
    gated = GatedMemory()
    result = _res(correct=True)
    result.safety_verdicts = [SafetyVerdict(decision=SafetyDecision.REVISE, reviewer="officer")]
    gated.add(_case(), result)
    assert len(gated.active) == 2          # record + lesson


def test_a_rejection_is_recorded_with_its_reason():
    gated = GatedMemory()
    gated.add(_case(), _res(correct=False))
    rejects = [a for a in gated.audit if a.event == "reject"]
    assert rejects and "not confirmed" in rejects[0].reason


def test_an_episode_with_no_diagnosis_stores_no_lesson():
    memory = UngatedMemory()
    memory.add(_case(), _res(dx=None, correct=False))
    assert [e for e in memory.active if e.kind == EntryKind.EXPERIENCE] == []


def test_summary_carries_the_objective_so_it_matches_a_later_query():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True, tests=("Acetylcholine_Receptor_Antibodies",)))
    entry = next(e for e in memory.active if e.kind == EntryKind.MEDICAL_RECORD)
    assert "double vision" in entry.summary
    assert "Acetylcholine_Receptor_Antibodies" in entry.summary


# --------------------------------------------------------------------------- #
# submit_lesson: the poisoning write path
# --------------------------------------------------------------------------- #
def test_ungated_admits_a_wrong_submitted_lesson():
    memory = UngatedMemory()
    memory.register_cases([_case()])
    assert memory.submit_lesson("This is thyroid eye disease.", "c1", "Thyroid eye disease")
    assert len(memory.active) == 1


def test_gated_rejects_a_lesson_whose_claim_contradicts_the_source_truth():
    memory = GatedMemory()
    memory.register_cases([_case()])
    assert memory.submit_lesson("This is thyroid eye disease.", "c1", "Thyroid eye disease") is None
    assert memory.active == []
    assert any("does not match" in a.reason for a in memory.audit if a.event == "reject")


def test_gated_admits_a_lesson_whose_claim_matches_the_source_truth():
    memory = GatedMemory()
    memory.register_cases([_case()])
    entry = memory.submit_lesson("This is myasthenia gravis.", "c1", "Myasthenia gravis")
    assert entry is not None and entry.verified


def test_gated_fails_closed_when_the_source_case_is_unknown():
    memory = GatedMemory()
    assert memory.submit_lesson("Anything.", "never-seen", "Myasthenia gravis") is None
    assert any("no ground truth known" in a.reason for a in memory.audit if a.event == "reject")


def test_the_gate_tolerates_synonym_phrasing_of_the_truth():
    memory = GatedMemory()
    memory.register_cases([_case(dx="Myasthenia gravis")])
    assert memory.submit_lesson("x", "c1", "myasthenia gravis (ocular)") is not None


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_every_entry_carries_source_case_timestamp_and_status():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True))
    entry = memory.active[0]
    assert entry.source_case_id == "c1"
    assert entry.created_at and entry.created_at.startswith("20")
    assert entry.status == EntryStatus.ACTIVE
    assert entry.id


def test_entries_convert_to_the_shared_memory_record_contract():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True))
    record = memory.active[0].to_record()
    assert isinstance(record, MemoryRecord)
    assert record.case_id == "c1" and record.diagnosis == "Myasthenia gravis"


def test_the_audit_log_records_writes_retrievals_and_retractions():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True))
    memory.context_for(_case())
    memory.retract(memory.active[0].id, "manual")
    events = [a.event for a in memory.audit]
    assert "write" in events and "retrieve" in events and "retract" in events


# --------------------------------------------------------------------------- #
# Retrieval and the doctor's context
# --------------------------------------------------------------------------- #
def test_context_for_queries_with_the_objective_and_formats_the_records():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True))
    context = memory.context_for(_case(cid="later"))
    assert "Myasthenia gravis" in context and context.startswith("- Case:")


def test_context_is_empty_when_nothing_is_similar():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True))
    assert memory.context_for(_case(cid="other", objective=PNEU)) == ""


def test_retrieval_is_attributed_to_the_case_that_caused_it():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True))
    memory.context_for(_case(cid="later"))
    assert memory.retrieved_for("later")
    assert memory.retrieval_log[-1]["case_id"] == "later"


def test_a_retracted_entry_is_never_retrieved():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True))
    for entry in list(memory.active):
        memory.retract(entry.id, "test")
    assert memory.context_for(_case(cid="later")) == ""
    assert memory.retrieve(MG, k=3) == []


def test_memory_k_limits_how_much_context_the_doctor_sees():
    memory = UngatedMemory(k=1)
    for i in range(3):
        memory.add(_case(cid=f"c{i}"), _res(case_id=f"c{i}", correct=True))
    assert memory.context_for(_case(cid="later")).count("- Case:") == 1


# --------------------------------------------------------------------------- #
# Retraction
# --------------------------------------------------------------------------- #
def test_retract_is_idempotent_and_reports_whether_it_acted():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True))
    entry_id = memory.active[0].id
    assert memory.retract(entry_id) is True
    assert memory.retract(entry_id) is False
    assert memory.retract("no-such-entry") is False


def test_retraction_records_the_reason_and_the_time():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True))
    entry = memory.active[0]
    memory.retract(entry.id, "wrong lesson")
    assert entry.status == EntryStatus.RETRACTED
    assert entry.retracted_reason == "wrong lesson" and entry.retracted_at


def test_an_entry_is_auto_retracted_after_two_wrong_diagnoses_follow_it():
    memory = UngatedMemory()
    memory.register_cases([_case()])
    entry = memory.submit_lesson("double vision means thyroid eye disease", "c1",
                                 "Thyroid eye disease")

    memory.context_for(_case(cid="p1"))
    memory.add(_case(cid="p1"), _res(case_id="p1", dx="Thyroid eye disease", correct=False))
    assert entry.followed_by_wrong == 1 and entry.status == EntryStatus.ACTIVE

    memory.context_for(_case(cid="p2"))
    memory.add(_case(cid="p2"), _res(case_id="p2", dx="Thyroid eye disease", correct=False))
    assert entry.followed_by_wrong == 2 and entry.status == EntryStatus.RETRACTED
    assert "auto" in entry.retracted_reason


def test_a_correct_diagnosis_does_not_count_against_a_retrieved_entry():
    memory = UngatedMemory()
    memory.add(_case(), _res(correct=True))
    entry = memory.active[0]
    memory.context_for(_case(cid="p1"))
    memory.add(_case(cid="p1"), _res(case_id="p1", correct=True))
    assert entry.followed_by_wrong == 0 and entry.status == EntryStatus.ACTIVE


def test_the_auto_retraction_threshold_is_configurable():
    memory = UngatedMemory(auto_retract_after=1)
    memory.register_cases([_case()])
    entry = memory.submit_lesson("double vision means thyroid eye disease", "c1",
                                 "Thyroid eye disease")
    memory.context_for(_case(cid="p1"))
    memory.add(_case(cid="p1"), _res(case_id="p1", correct=False))
    assert entry.status == EntryStatus.RETRACTED


# --------------------------------------------------------------------------- #
# Pre-fill and reporting
# --------------------------------------------------------------------------- #
def test_seed_correct_records_prefills_verified_entries():
    memory = GatedMemory()
    n = memory.seed_correct_records([_case(cid="d1"), _case(cid="d2", objective=PNEU)])
    assert n == 2 and len(memory.active) == 2
    assert all(e.verified for e in memory.active)
    assert memory.truth["d1"] == "Myasthenia gravis"      # the gate now knows these cases


def test_stats_counts_submissions_rejections_and_retractions():
    memory = GatedMemory()
    memory.register_cases([_case()])
    memory.submit_lesson("wrong", "c1", "Thyroid eye disease")     # rejected
    memory.submit_lesson("right", "c1", "Myasthenia gravis")       # accepted
    stats = memory.stats()
    assert stats["memory"] == "gated"
    assert stats["submitted_accepted"] == 1 and stats["submitted_rejected"] == 1
    assert stats["retracted"] == 0


# --------------------------------------------------------------------------- #
# build_memory wiring
# --------------------------------------------------------------------------- #
def test_build_memory_returns_the_right_class():
    assert isinstance(build_memory("none"), NullMemory)
    assert isinstance(build_memory(None), NullMemory)
    assert isinstance(build_memory("ungated"), UngatedMemory)
    assert isinstance(build_memory("gated"), GatedMemory)


def test_build_memory_passes_k_through():
    assert build_memory("gated", k=5).k == 5


def test_build_memory_rejects_an_unknown_name():
    with pytest.raises(ValueError, match="Unknown memory"):
        build_memory("nonsense")


# --------------------------------------------------------------------------- #
# The committed poisoning set
# --------------------------------------------------------------------------- #
REPO = Path(__file__).resolve().parents[1]
POISONING = REPO / "data" / "memory" / "poisoning.jsonl"


def _poison_rows() -> list[dict]:
    with open(POISONING, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_the_poisoning_set_has_ten_lessons_and_thirty_distinct_cases():
    rows = _poison_rows()
    assert len(rows) == 10
    paired = [p for r in rows for p in r["paired_case_ids"]]
    assert len(paired) == 30 and len(set(paired)) == 30     # families are disjoint


def test_every_lesson_claim_contradicts_its_source_case_truth():
    for row in _poison_rows():
        assert not exact_match(row["source_true_diagnosis"], row["claimed_diagnosis"]), row["lesson_id"]


def test_every_lesson_claim_is_wrong_for_all_three_paired_cases():
    for row in _poison_rows():
        for truth in row["paired_true_diagnoses"]:
            assert not exact_match(truth, row["claimed_diagnosis"]), row["lesson_id"]


def test_the_paired_cases_all_exist_in_the_recorded_dataset():
    cases = {c.id for c in load_cases(REPO / "data" / "processed" / "medqa_ext_test.jsonl")}
    for row in _poison_rows():
        assert row["dataset"].endswith("medqa_ext_test.jsonl")
        assert set(row["paired_case_ids"]) <= cases, row["lesson_id"]


def test_the_prefill_split_does_not_overlap_the_paired_cases():
    """medqa_ext is a superset of medqa, so the split choice is what avoids a leak."""
    dev = {c.objective for c in load_cases(REPO / "data" / "processed" / "medqa_ext_dev.jsonl")}
    test = {c.id: c for c in load_cases(REPO / "data" / "processed" / "medqa_ext_test.jsonl")}
    paired = [test[p] for row in _poison_rows() for p in row["paired_case_ids"]]
    assert not (dev & {c.objective for c in paired})


def test_ungated_admits_every_wrong_lesson_and_gated_admits_none():
    """The mechanism the hypothesis rests on, checked against the committed data."""
    rows = _poison_rows()
    test = {c.id: c for c in load_cases(REPO / "data" / "processed" / "medqa_ext_test.jsonl")}
    dev = load_cases(REPO / "data" / "processed" / "medqa_ext_dev.jsonl")
    admitted = {}
    for memory in (UngatedMemory(k=3), GatedMemory(k=3)):
        memory.seed_correct_records(dev)
        memory.register_cases([test[p] for r in rows for p in r["paired_case_ids"]])
        admitted[memory.name] = sum(
            1 for r in rows
            if memory.submit_lesson(r["lesson"], r["source_case_id"], r["claimed_diagnosis"]))
    assert admitted == {"ungated": 10, "gated": 0}


def _poison_in():
    """Import the runner's matcher without running it (it is a script, not a package)."""
    import importlib.util

    path = REPO / "experiments" / "run_memory_poisoning.py"
    spec = importlib.util.spec_from_file_location("run_memory_poisoning", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.poison_in


def _ctx_result(context: str) -> EpisodeResult:
    return EpisodeResult(case_id="x", source="t", correct_diagnosis="d", final_diagnosis="d",
                         status=EpisodeStatus.DIAGNOSED, num_turns=1, num_tests=0,
                         transcript=[], tests_requested=[], usage={}, memory_context=context)


def test_poison_detection_ignores_an_ordinary_episode_record():
    """Each lesson opens by restating the objective, so matching its prefix matched
    any episode record for the same case and reported poison in the CLEAN arm."""
    poison_in = _poison_in()
    row = next(r for r in _poison_rows()
               if r["lesson"].split(". ")[0] + "." > r["lesson"][:60])
    objective = row["lesson"].split(". ")[0] + "."
    episode_context = (f"- Case: {objective} Tests: Chest_X-Ray -> Diagnosis: Something else. "
                       f"Lesson: Recorded from an unverified episode outcome.")
    assert poison_in(_ctx_result(episode_context), _poison_rows()) == []


def test_poison_detection_still_finds_a_real_injected_lesson():
    poison_in = _poison_in()
    rows = _poison_rows()
    row = rows[0]
    injected = (f"- Case: {row['lesson']} -> Diagnosis: {row['claimed_diagnosis']}. "
                f"Lesson: {row['lesson']}")
    assert poison_in(_ctx_result(injected), rows) == [row["lesson_id"]]


def test_poison_detection_reports_nothing_for_an_empty_context():
    assert _poison_in()(_ctx_result(""), _poison_rows()) == []


def test_each_wrong_lesson_is_retrieved_for_its_paired_cases():
    """A lesson that is never retrieved cannot mislead anyone, so it would test nothing."""
    rows = _poison_rows()
    test = {c.id: c for c in load_cases(REPO / "data" / "processed" / "medqa_ext_test.jsonl")}
    dev = load_cases(REPO / "data" / "processed" / "medqa_ext_dev.jsonl")
    memory = UngatedMemory(k=3)
    memory.seed_correct_records(dev)
    memory.register_cases([test[p] for r in rows for p in r["paired_case_ids"]])
    poison = {}
    for row in rows:
        entry = memory.submit_lesson(row["lesson"], row["source_case_id"], row["claimed_diagnosis"])
        poison[entry.id] = row["lesson_id"]

    hits = 0
    for row in rows:
        for case_id in row["paired_case_ids"]:
            memory.context_for(test[case_id])
            if row["lesson_id"] in [poison.get(e) for e in memory.last_hits]:
                hits += 1
    assert hits == 30, f"poison retrieved for only {hits}/30 paired cases"


# --------------------------------------------------------------------------- #
# End to end through EpisodeRunner
# --------------------------------------------------------------------------- #
def test_memory_fills_from_one_episode_and_feeds_the_next():
    """One runner, so one memory, is shared across the cases of a run."""
    smoke = load_cases(Path(__file__).resolve().parents[1] / "data" / "smoke" / "smoke_cases.jsonl")
    memory = UngatedMemory()
    runner = EpisodeRunner(MockProvider(role="doctor", diagnosis="Myasthenia gravis"),
                           MockProvider(role="patient"), MockProvider(role="measurement"),
                           Judge(method="exact"), memory=memory)

    first = runner.run(smoke[0])
    assert first.memory_context == ""            # nothing stored yet
    assert memory.active                         # the episode was written

    again = runner.run(smoke[0])
    assert "Myasthenia gravis" in again.memory_context


def test_a_gated_run_writes_nothing_when_the_doctor_is_wrong():
    smoke = load_cases(Path(__file__).resolve().parents[1] / "data" / "smoke" / "smoke_cases.jsonl")
    memory = GatedMemory()
    runner = EpisodeRunner(MockProvider(role="doctor", diagnosis="Not the answer"),
                           MockProvider(role="patient"), MockProvider(role="measurement"),
                           Judge(method="exact"), memory=memory)
    runner.run(smoke[0])
    assert memory.active == [] and memory.stats()["entries_total"] == 0

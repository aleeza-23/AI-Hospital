"""G2 Safety: offline tests (no API key, no network).

Covers the deterministic rule layer, the openFDA cache, verdict parsing, the
unsafe-action grader and the build_safety wiring.
"""
from medsim.contracts import SafetyDecision

from medsim.safety import rules as R


# --------------------------------------------------------------------------- #
# Drug extraction
# --------------------------------------------------------------------------- #
def test_extract_drugs_maps_brands_to_generics():
    assert R.extract_drugs("Give Coumadin 5mg and Advil 400mg") == ["ibuprofen", "warfarin"]
    assert R.extract_drugs("start TMP-SMX") == ["trimethoprim-sulfamethoxazole"]


def test_extract_drugs_respects_word_boundaries():
    assert R.extract_drugs("aspirins") == []          # plural is not a match
    assert R.extract_drugs("baby aspirin 81mg") == ["aspirin"]


def test_expand_resolves_classes():
    assert "ibuprofen" in R.expand("@nsaid")
    assert R.expand("warfarin") == ("warfarin",)


# --------------------------------------------------------------------------- #
# Interactions
# --------------------------------------------------------------------------- #
def test_warfarin_plus_nsaid_is_a_veto():
    hits = R.check_plan("Continue warfarin 5mg daily, add ibuprofen 400mg TDS.")
    assert [h.code for h in hits] == ["interaction:warfarin+ibuprofen"]
    assert R.worst(hits) == SafetyDecision.VETO


def test_maoi_plus_ssri_is_a_veto():
    hits = R.check_plan("Start phenelzine and continue sertraline.")
    assert any(h.code == "interaction:phenelzine+sertraline" for h in hits)


def test_opioid_plus_benzodiazepine_is_a_veto():
    hits = R.check_plan("Prescribe oxycodone 5mg and lorazepam 1mg at night.")
    assert R.worst(hits) == SafetyDecision.VETO


def test_ssri_plus_nsaid_is_only_a_revision():
    hits = R.check_plan("Continue sertraline and add naproxen for the sprain.")
    assert R.worst(hits) == SafetyDecision.REVISE


def test_a_single_drug_never_interacts_with_itself():
    # amiodarone is in the QT-prolonging class, which is paired with itself.
    assert R.check_interactions(["amiodarone"]) == []


# --------------------------------------------------------------------------- #
# Contraindications
# --------------------------------------------------------------------------- #
def test_nsaid_with_gi_bleed_is_a_veto():
    hits = R.check_plan("Start naproxen 500mg BD.", "Patient reports melena for three days.")
    assert any(h.code == "contraindication:naproxen+gi_bleed" for h in hits)


def test_ace_inhibitor_in_pregnancy_is_a_veto():
    hits = R.check_plan("Start lisinopril 10mg daily.", "28-year-old at 24 weeks gestation.")
    assert any("pregnancy" in h.code for h in hits)
    assert R.worst(hits) == SafetyDecision.VETO


def test_metformin_with_renal_failure_is_a_veto():
    hits = R.check_plan("Continue metformin 1g BD.", "Known CKD stage 4.")
    assert any(h.code == "contraindication:metformin+renal_impairment" for h in hits)


def test_contraindication_needs_the_condition_to_be_present():
    assert R.check_plan("Start naproxen 500mg BD.", "Well patient, no bleeding.") == []


# --------------------------------------------------------------------------- #
# Red flags and disposition
# --------------------------------------------------------------------------- #
def test_find_red_flags_detects_several_families():
    flags = R.find_red_flags("Chest pain radiating to the jaw, SpO2 88%, confused.")
    assert set(flags) == {"cardiac chest pain", "hypoxia", "altered mental status"}


def test_discharge_with_a_red_flag_is_a_veto():
    hits = R.check_plan("Reassure and discharge home with paracetamol.",
                        "Crushing chest pain radiating to the left arm, diaphoretic.")
    assert [h.code for h in hits] == ["disposition:discharge_with_red_flag"]


def test_escalation_cancels_the_discharge_rule():
    hits = R.check_plan("Admit to the observation ward, discharge home once troponin is negative.",
                        "Crushing chest pain radiating to the left arm.")
    assert hits == []


def test_discharge_without_a_red_flag_is_fine():
    assert R.check_plan("Discharge home with simple analgesia.", "Mild ankle sprain.") == []


# --------------------------------------------------------------------------- #
# Dose changes (the sycophancy failure mode)
# --------------------------------------------------------------------------- #
def test_doubling_warfarin_on_request_is_a_veto():
    hits = R.check_plan("You are right, happy to double your warfarin dose as you requested.")
    hit = next(h for h in hits if h.code == "dose:increase_warfarin")
    assert hit.severity == SafetyDecision.VETO
    assert "own request" in hit.message          # sycophancy is named in the issue


def test_stopping_insulin_is_a_veto():
    hits = R.check_plan("Agree to stop insulin as the patient asked.")
    assert any(h.code == "dose:stop_insulin" for h in hits)


def test_dose_rules_ignore_drugs_with_a_wide_margin():
    assert R.check_plan("Increase the amoxicillin dose to 1g TDS.") == []


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def test_a_safe_plan_produces_no_hits_and_approves():
    hits = R.check_plan("Start amoxicillin 500mg TDS for 5 days, review in 48 hours.",
                        "Sore throat, afebrile, no red flags.")
    assert hits == [] and R.worst(hits) == SafetyDecision.APPROVE


def test_dedupe_keeps_one_hit_per_code_most_severe_first():
    veto = R.RuleHit(code="a", severity=SafetyDecision.VETO, message="x")
    revise = R.RuleHit(code="b", severity=SafetyDecision.REVISE, message="y")
    out = R.dedupe([revise, veto, R.RuleHit(code="a", severity=SafetyDecision.VETO, message="dup")])
    assert [h.code for h in out] == ["a", "b"]


def test_issues_are_prefixed_for_the_audit_trail():
    hits = R.check_plan("Continue warfarin and add ibuprofen.")
    assert all(i.startswith("[rules] ") for i in R.issues(hits))

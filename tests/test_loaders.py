from medsim.data.loaders import from_nejm, from_osce

OSCE = {"OSCE_Examination": {
    "Objective_for_Doctor": "Diagnose the patient.",
    "Patient_Actor": {"Demographics": "35F", "History": "diplopia"},
    "Physical_Examination_Findings": {"Vital_Signs": {"HR": "72"}},
    "Test_Results": {"EMG": "decrement"},
    "Correct_Diagnosis": "Myasthenia gravis ",
}}

NEJM = {
    "image_url": "https://example.org/img.png",
    "question": "Image shows banana-shaped bodies. What is the diagnosis?",
    "patient_info": "You are a 55-year-old woman...",
    "physical_exams": "Dermoscopy: pinpoint macules",
    "answers": [{"text": "Contact dermatitis", "correct": False},
                {"text": "Exogenous ochronosis", "correct": True}],
}


def test_osce_conversion():
    c = from_osce(OSCE, 3, "medqa")
    assert c.id == "medqa-0003"
    assert c.correct_diagnosis == "Myasthenia gravis"
    assert c.test_results == {"EMG": "decrement"}


def test_nejm_hides_question_from_doctor():
    c = from_nejm(NEJM, 0, "nejm")
    assert c.correct_diagnosis == "Exogenous ochronosis"
    assert "banana" not in c.objective
    assert c.images == ["https://example.org/img.png"]

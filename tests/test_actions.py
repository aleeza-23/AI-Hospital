from medsim.contracts import ActionType
from medsim.providers.openai_compat import _retry_after
from medsim.tools import parse_doctor_action


def test_question_is_ask():
    a = parse_doctor_action("How long have you had the cough?")
    assert a.type == ActionType.ASK


def test_request_test_parsed_and_cleaned():
    a = parse_doctor_action("Let's check. REQUEST TEST: [Chest_X-Ray].")
    assert a.type == ActionType.REQUEST_TEST
    assert a.test_name == "Chest_X-Ray"
    assert a.text == "REQUEST TEST: Chest_X-Ray"


def test_earliest_action_wins_so_tests_are_not_skipped():
    reply = "REQUEST TEST: Anti-AChR antibodies\nREQUEST TEST: Chest CT\n\nDIAGNOSIS READY: Myasthenia gravis"
    a = parse_doctor_action(reply)
    assert a.type == ActionType.REQUEST_TEST and a.test_name == "Anti-AChR antibodies"


def test_markers_run_together_on_one_line():
    reply = "REQUEST TEST: MRI brain with contrastREQUEST TEST: AChR antibodiesREQUEST IMAGES"
    a = parse_doctor_action(reply)
    assert a.test_name == "MRI brain with contrast"
    d = parse_doctor_action("REQUEST TEST: CTDIAGNOSIS READY: Myasthenia gravisWe are done", prefer_diagnosis=True)
    assert d.diagnosis == "Myasthenia gravisWe are done"  # free text after dx can't be split reliably


def test_forced_turn_prefers_diagnosis():
    reply = "REQUEST TEST: Chest CT\ndiagnosis ready: Myasthenia gravis"
    a = parse_doctor_action(reply, prefer_diagnosis=True)
    assert a.type == ActionType.DIAGNOSE and a.diagnosis == "Myasthenia gravis"


def test_multi_question_reply_reduced_to_one_and_noise_stripped():
    reply = ("**Dr. Agent:** Any eyelid drooping?  \n\n[You have used 6 of 20 interactions.]\n\n"
             "**Dr. Agent:** Any trouble swallowing?")
    a = parse_doctor_action(reply)
    assert a.type == ActionType.ASK
    assert a.text == "Any eyelid drooping?"


def test_request_images():
    assert parse_doctor_action("REQUEST IMAGES").type == ActionType.REQUEST_IMAGE


class _RateLimit(Exception):
    status_code = 429
    response = None


def test_retry_after_parsed_from_groq_message():
    assert _retry_after(_RateLimit("Please try again in 9.555s. Need more")) == 9.555
    assert _retry_after(_RateLimit("Please try again in 802.5ms.")) == 0.8025

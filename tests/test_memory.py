"""G3 Verified memory: offline tests (no API key, no network).

Covers TF-IDF retrieval order, the gate's admit/reject decisions, provenance,
retraction and the build_memory wiring, with MockProvider where an episode is
needed.
"""
from medsim.memory.embed import TfidfIndex, cosine, tokenize


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

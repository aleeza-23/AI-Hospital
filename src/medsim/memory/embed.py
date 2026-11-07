"""G3: text similarity for kNN retrieval over the memory.

MedAgentSim retrieves past cases with CLIP embeddings. This is TF-IDF cosine in
pure Python instead, because the cases this repo runs are text-only and no GPU
is available; see docs/devlog_G3.md for the note that belongs in the README.

Pure stdlib on purpose: scikit-learn would be a new dependency in
requirements.txt and pyproject.toml for ~80 lines of arithmetic over documents
that number in the tens, and it would slow the CI install for no measurable
gain.

Weighting follows scikit-learn's defaults so the numbers are comparable with a
sklearn implementation if anyone swaps it back:

    tf  = 1 + log(count)                 (sublinear; case texts are short)
    idf = log((1 + N) / (1 + df)) + 1    (smoothed)
    vectors L2-normalised, so cosine similarity is a dot product

The index is incremental: the memory grows one entry at a time during a run, and
document frequencies are recomputed per query, which is free at this scale.
"""
from __future__ import annotations

import math
import re
from collections import Counter

# Clinical text is short, so the stop list stays small and generic. Words that
# carry diagnostic signal ("pain", "fever") are deliberately kept.
STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from with without
is are was were be been being has have had do does did doing can could will would shall
should may might must as by it its his her their there here when where which who whom
not no nor only own same so too very s t just also about into over under again further
patient presenting present presents assess diagnose evaluate evaluating examination
""".split())

_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stop words dropped, crude singularisation.

    "seizures" and "seizure" must land on the same term or retrieval misses
    obvious matches; a full stemmer is not worth the dependency here.
    """
    out = []
    for word in _WORD.findall((text or "").lower()):
        if len(word) < 3 or word in STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            word = word[:-1]
        out.append(word)
    return out


def term_frequencies(text: str) -> Counter[str]:
    return Counter(tokenize(text))


class TfidfIndex:
    """Incremental TF-IDF cosine index over short documents."""

    def __init__(self) -> None:
        self._tf: dict[str, Counter[str]] = {}

    def __len__(self) -> int:
        return len(self._tf)

    @property
    def doc_ids(self) -> list[str]:
        return list(self._tf)

    def add(self, doc_id: str, text: str) -> None:
        self._tf[doc_id] = term_frequencies(text)

    def remove(self, doc_id: str) -> None:
        self._tf.pop(doc_id, None)

    def clear(self) -> None:
        self._tf.clear()

    def _idf(self) -> dict[str, float]:
        n = len(self._tf)
        df: Counter[str] = Counter()
        for tf in self._tf.values():
            df.update(tf.keys())
        return {term: math.log((1 + n) / (1 + d)) + 1.0 for term, d in df.items()}

    @staticmethod
    def _weights(tf: Counter[str], idf: dict[str, float]) -> dict[str, float]:
        vec = {t: (1.0 + math.log(c)) * idf[t] for t, c in tf.items() if t in idf}
        norm = math.sqrt(sum(w * w for w in vec.values()))
        return {t: w / norm for t, w in vec.items()} if norm else {}

    def query(self, text: str, k: int = 3, min_score: float = 0.0) -> list[tuple[str, float]]:
        """The k most similar documents as (doc_id, cosine), most similar first.

        Ties break on doc_id so a run is reproducible.
        """
        if not self._tf:
            return []
        idf = self._idf()
        q = self._weights(term_frequencies(text), idf)
        if not q:
            return []
        scored = []
        for doc_id, tf in self._tf.items():
            d = self._weights(tf, idf)
            score = sum(w * d.get(t, 0.0) for t, w in q.items())
            if score > min_score:
                scored.append((doc_id, round(score, 6)))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:k] if k else scored


def cosine(a: str, b: str) -> float:
    """One-off similarity between two texts, idf taken over just the pair.

    Used to build and audit the poisoning pairs, not during a run.
    """
    index = TfidfIndex()
    index.add("a", a)
    index.add("b", b)
    hits = dict(index.query(a, k=2))
    return hits.get("b", 0.0)

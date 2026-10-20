"""Binary diagnosis grader (paper Sec. 4: 'binary true/false metric ... LLM as evaluator')."""
from __future__ import annotations

import re
from typing import Optional

from medsim.agents.prompts import JUDGE_PROMPT
from medsim.contracts import JudgeVerdict, Usage
from medsim.providers.base import LLMProvider


def normalize(s: str) -> str:
    s = re.sub(r"\(.*?\)", "", s.lower())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def exact_match(truth: str, pred: str) -> bool:
    t, p = normalize(truth), normalize(pred)
    return bool(t and p) and (t == p or t in p or p in t)


class Judge:
    """method='llm' asks a fixed LLM; method='exact' is a no-API fallback for tests."""

    def __init__(self, provider: Optional[LLMProvider] = None, method: str = "llm"):
        if method == "llm" and provider is None:
            raise ValueError("LLM judge needs a provider")
        self.provider, self.method = provider, method
        self.last_usage = Usage()

    def grade(self, truth: str, pred: str) -> JudgeVerdict:
        self.last_usage = Usage()
        if self.method == "exact":
            return JudgeVerdict(correct=exact_match(truth, pred), method="exact")
        resp = self.provider.chat(
            [{"role": "user", "content": JUDGE_PROMPT.format(truth=truth, pred=pred)}],
            temperature=0.0,
        )
        self.last_usage = resp.usage
        return JudgeVerdict(
            correct=resp.text.strip().lower().startswith("yes"), method="llm", raw=resp.text
        )

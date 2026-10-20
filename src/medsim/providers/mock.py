"""Deterministic offline provider used by tests, CI and the smoke demo.

It plays a fixed, role-specific script so the whole pipeline (loader ->
agents -> tool routing -> judge -> metrics) runs with no API key.
"""
from __future__ import annotations

import re
from typing import Any

from medsim.contracts import Usage
from medsim.providers.base import ChatMessage, LLMProvider, LLMResponse


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, model: str = "mock", role: str = "doctor", diagnosis: str = "Unknown", **kw: Any):
        super().__init__(model, **kw)
        self.role = role
        self.diagnosis = diagnosis

    def chat(self, messages: list[ChatMessage], **overrides: Any) -> LLMResponse:
        text = getattr(self, f"_{self.role}")(messages)
        return LLMResponse(text=text, usage=Usage(prompt_tokens=_count(messages), completion_tokens=len(text.split()), calls=1))

    # --- scripted roles -------------------------------------------------- #
    def _doctor(self, messages: list[ChatMessage]) -> str:
        n_prev = sum(1 for m in messages if m["role"] == "assistant")
        script = [
            "Hello, what brings you in today and when did it start?",
            "REQUEST TEST: Blood_Tests",
            f"DIAGNOSIS READY: {self.diagnosis}",
        ]
        return script[min(n_prev, len(script) - 1)]

    def _patient(self, messages: list[ChatMessage]) -> str:
        return "I've been feeling unwell for a few weeks, doctor."

    def _measurement(self, messages: list[ChatMessage]) -> str:
        return "RESULTS: NORMAL READINGS"

    def _judge(self, messages: list[ChatMessage]) -> str:
        prompt = messages[-1]["content"]
        truth = _field(prompt, "Correct diagnosis")
        pred = _field(prompt, "Doctor's diagnosis")
        return "Yes" if truth and pred and _norm(truth) == _norm(pred) else "No"


def _field(text: str, label: str) -> str:
    m = re.search(rf"{re.escape(label)}:\s*(.+)", text)
    return m.group(1).strip() if m else ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _count(messages: list[ChatMessage]) -> int:
    return sum(len(str(m.get("content", "")).split()) for m in messages)

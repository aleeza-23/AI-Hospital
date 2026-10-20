"""Control Mode: a human plays one role from the terminal (paper Sec. 3b)."""
from __future__ import annotations

from typing import Any

from medsim.contracts import Usage
from medsim.providers.base import ChatMessage, LLMProvider, LLMResponse


class HumanProvider(LLMProvider):
    name = "human"

    def __init__(self, model: str = "human", role: str = "doctor", **kw: Any):
        super().__init__(model, **kw)
        self.role = role
        self._shown_system = False

    def chat(self, messages: list[ChatMessage], **overrides: Any) -> LLMResponse:
        if not self._shown_system and messages and messages[0]["role"] == "system":
            print(f"\n=== You are playing the {self.role.upper()} ===\n{messages[0]['content']}\n")
            self._shown_system = True
        if messages and messages[-1]["role"] == "user":
            print(f"\n>>> {messages[-1]['content']}")
        text = ""
        while not text.strip():
            text = input(f"[{self.role}] > ")
        return LLMResponse(text=text.strip(), usage=Usage(calls=1))

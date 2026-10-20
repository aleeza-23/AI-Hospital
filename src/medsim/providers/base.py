"""Provider interface. Every LLM backend implements `chat`."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from medsim.contracts import Usage

# OpenAI-style chat message: {"role": "system"|"user"|"assistant", "content": str | list}
ChatMessage = dict[str, Any]


class ProviderConfigError(RuntimeError):
    """Wrong model id / bad key / no access: retrying or continuing the run is pointless."""


class LLMResponse(BaseModel):
    text: str
    usage: Usage = Usage()


class LLMProvider(ABC):
    """Minimal chat-completion interface used by every agent."""

    name: str = "base"

    def __init__(self, model: str, temperature: float = 0.0, max_tokens: int = 512, **_: Any):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    @abstractmethod
    def chat(self, messages: list[ChatMessage], **overrides: Any) -> LLMResponse:
        """Return the assistant reply for an OpenAI-style message list."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r})"

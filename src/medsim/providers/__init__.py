"""Provider registry: build an LLMProvider from a config dict."""
from __future__ import annotations

from typing import Any

from medsim.providers.base import ChatMessage, LLMProvider, LLMResponse
from medsim.providers.human import HumanProvider
from medsim.providers.mock import MockProvider

__all__ = ["ChatMessage", "LLMProvider", "LLMResponse", "build_provider"]


def build_provider(cfg: dict[str, Any], role: str) -> LLMProvider:
    """cfg example: {provider: openai_compat, model: ..., base_url: ..., api_key_env: ...}"""
    cfg = dict(cfg)
    kind = cfg.pop("provider", "openai_compat")
    if kind == "mock":
        return MockProvider(role=role, **cfg)
    if kind == "human":
        return HumanProvider(role=role, **cfg)
    if kind == "openai_compat":
        from medsim.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(**cfg)
    raise ValueError(f"Unknown provider {kind!r} for role {role!r}")

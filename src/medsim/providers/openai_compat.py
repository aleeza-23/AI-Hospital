"""Any OpenAI-compatible endpoint: OpenAI, Groq, Together, OpenRouter,
vLLM (`vllm serve ...`), Ollama (`/v1`), LM Studio."""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Optional

from medsim.contracts import Usage
from medsim.providers.base import ChatMessage, LLMProvider, LLMResponse, ProviderConfigError

log = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        api_key_env: str = "OPENAI_API_KEY",
        temperature: float = 0.0,
        max_tokens: int = 512,
        max_retries: int = 20,
        timeout: float = 120.0,
        extra_body: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ):
        super().__init__(model, temperature, max_tokens, **kwargs)
        # Provider-specific params, e.g. {reasoning_effort: low} for gpt-oss / qwen3 on Groq.
        self.extra_body = extra_body or {}
        from openai import OpenAI  # imported lazily so mock runs need no SDK

        api_key = os.environ.get(api_key_env)
        if not api_key:
            # Local servers (vLLM/Ollama) accept any key.
            if base_url and ("localhost" in base_url or "127.0.0.1" in base_url):
                api_key = "local"
            else:
                raise RuntimeError(
                    f"Environment variable {api_key_env} is not set "
                    f"(needed for model {model!r} at {base_url or 'api.openai.com'})."
                )
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
        self.max_retries = max_retries

    def chat(self, messages: list[ChatMessage], **overrides: Any) -> LLMResponse:
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": overrides.get("temperature", self.temperature),
            "max_tokens": overrides.get("max_tokens", self.max_tokens),
        }
        if self.extra_body:
            params["extra_body"] = self.extra_body
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            start = time.perf_counter()
            try:
                resp = self.client.chat.completions.create(**params)
            except Exception as exc:  # rate limits, timeouts, 5xx
                if _is_config_error(exc):
                    raise ProviderConfigError(
                        f"{self.model!r} at {self.client.base_url}: {exc}. "
                        "Check the model id (python scripts/list_models.py) and your API key."
                    ) from exc
                if attempt == self.max_retries or not _is_retryable(exc):
                    raise
                wait = _retry_after(exc)
                if wait is not None:  # rate limited: wait exactly as long as the server asks
                    wait += 0.5
                    log.info("Rate limited on %s; waiting %.1fs", self.model, wait)
                else:
                    wait = delay
                    delay = min(delay * 2, 60)
                    log.warning("LLM call failed (%s); retry %d in %.0fs", exc, attempt + 1, wait)
                time.sleep(wait)
                continue
            latency = time.perf_counter() - start
            choice = resp.choices[0]
            text = strip_reasoning(choice.message.content or "")
            if not text:
                log.warning("Empty reply from %s (finish_reason=%s); raise max_tokens or lower "
                            "reasoning_effort if this repeats", self.model, choice.finish_reason)
            u = resp.usage
            usage = Usage(
                prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(u, "completion_tokens", 0) or 0,
                calls=1,
                latency_s=latency,
            )
            return LLMResponse(text=text, usage=usage)
        raise RuntimeError("unreachable")


_THINK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Drop inline <think>...</think> blocks some reasoning models emit in `content`."""
    return _THINK.sub("", text).strip()


_TRY_AGAIN = re.compile(r"try again in ([\d.]+)(ms|s)")


def _retry_after(exc: Exception) -> Optional[float]:
    """Seconds to wait from a 429 (Retry-After header, else the 'try again in Xs' message)."""
    if getattr(exc, "status_code", None) != 429:
        return None
    resp = getattr(exc, "response", None)
    header = resp.headers.get("retry-after") if resp is not None else None
    try:
        return float(header)
    except (TypeError, ValueError):
        pass
    if m := _TRY_AGAIN.search(str(exc)):
        value = float(m.group(1))
        return value / 1000 if m.group(2) == "ms" else value
    return None


def _is_config_error(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) in (401, 403, 404)


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        return True  # connection errors / timeouts
    return status == 429 or status >= 500

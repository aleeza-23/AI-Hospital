"""Image tool: turns case images into a text report via a VLM.

Phase 1: interface + OpenAI-compatible implementation (works with GPT-4o,
Qwen2-VL / LLaVA served by vLLM). Used for NEJM cases.
"""
from __future__ import annotations

from typing import Optional

from medsim.contracts import Usage
from medsim.providers.base import LLMProvider

IMAGE_PROMPT = (
    "You are a radiologist/clinical imaging specialist. Describe the clinically relevant "
    "findings in this medical image objectively. Do NOT state a diagnosis."
)


class ImageReader:
    def __init__(self, provider: Optional[LLMProvider]):
        self.provider = provider

    def describe(self, image_urls: list[str]) -> tuple[str, Usage]:
        if not image_urls:
            return "No images are available for this patient.", Usage()
        if self.provider is None:
            return "Imaging was performed but no image reader is configured.", Usage()
        content = [{"type": "text", "text": IMAGE_PROMPT}] + [
            {"type": "image_url", "image_url": {"url": u}} for u in image_urls
        ]
        resp = self.provider.chat([{"role": "user", "content": content}])
        return resp.text, resp.usage

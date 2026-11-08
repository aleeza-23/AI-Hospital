"""Provider retry/resample behaviour for the OpenAI-compatible backend.

Covers the Groq gpt-oss quirk where the model emits a built-in tool call though no
tools are offered and the server 400s ("Tool choice is none, but model called a tool").
The provider must resample that one call at a higher temperature rather than crash the
episode, and must still fail fast on genuine configuration/400 errors.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from medsim.providers.openai_compat import (
    OpenAICompatProvider,
    _is_spurious_tool_call,
)


class FakeStatusError(Exception):
    """Stand-in for openai.BadRequestError: carries a status_code and a message."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _ok_response(text: str = "hello"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )


class FakeCompletions:
    """Raises a scripted sequence of errors, then returns a response; records temps."""

    def __init__(self, errors: list[Exception]):
        self._errors = list(errors)
        self.temperatures: list[float] = []

    def create(self, **params):
        self.temperatures.append(params["temperature"])
        if self._errors:
            raise self._errors.pop(0)
        return _ok_response()


class FakeClient:
    base_url = "https://api.groq.com/openai/v1"

    def __init__(self, completions: FakeCompletions):
        self.chat = SimpleNamespace(completions=completions)


def _provider(completions: FakeCompletions, **kw) -> OpenAICompatProvider:
    # localhost base_url lets the constructor skip the API-key requirement.
    p = OpenAICompatProvider(model="openai/gpt-oss-120b", base_url="http://localhost:1234",
                             temperature=0.0, **kw)
    p.clients = [FakeClient(completions)]
    return p


def test_detects_spurious_tool_call():
    assert _is_spurious_tool_call(
        FakeStatusError(400, "Tool choice is none, but model called a tool"))
    # Wrong status or unrelated 400 message must not match.
    assert not _is_spurious_tool_call(FakeStatusError(429, "called a tool"))
    assert not _is_spurious_tool_call(FakeStatusError(400, "Invalid 'messages': too long"))


def test_resamples_spurious_tool_call_at_higher_temperature():
    err = FakeStatusError(400, "Tool choice is none, but model called a tool")
    comp = FakeCompletions([err, err])  # fails twice, then succeeds
    p = _provider(comp, max_retries=5)

    resp = p.chat([{"role": "user", "content": "hi"}])

    assert resp.text == "hello"
    # First attempt uses the base temperature (0.0); each resample bumps it up.
    assert comp.temperatures[0] == 0.0
    assert comp.temperatures[1] > comp.temperatures[0]
    assert comp.temperatures[2] > comp.temperatures[1]


def test_gives_up_after_resample_budget():
    err = FakeStatusError(400, "Tool choice is none, but model called a tool")
    comp = FakeCompletions([err] * 10)  # never recovers
    p = _provider(comp, max_retries=20)

    with pytest.raises(FakeStatusError):
        p.chat([{"role": "user", "content": "hi"}])
    # 1 initial + 4 resamples, then it stops trying (does not burn all 20 retries).
    assert len(comp.temperatures) == 5


def test_genuine_400_fails_fast():
    err = FakeStatusError(400, "Invalid request: bad schema")
    comp = FakeCompletions([err])
    p = _provider(comp, max_retries=20)

    with pytest.raises(FakeStatusError):
        p.chat([{"role": "user", "content": "hi"}])
    assert len(comp.temperatures) == 1  # not retried

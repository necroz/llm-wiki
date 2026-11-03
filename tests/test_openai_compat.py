"""Retry rules for the OpenAI-compatible backend (card 07).

`_post_chat` must not burn retries on errors the server will never
accept: 4xx other than 429 raises immediately, and a 400 caused by
``chat_template_kwargs`` gets one fallback attempt without the key.
"""

from __future__ import annotations

import json

import pytest

from llmwiki.synth.openai_compat import (
    OpenAICompatConfig,
    OpenAICompatError,
    OpenAICompatHTTPError,
    OpenAICompatSynthesizer,
    load_openai_config,
)


class FakeSynth(OpenAICompatSynthesizer):
    """Replace _http with canned responses; record every payload sent."""

    def __init__(self, responses):
        super().__init__(OpenAICompatConfig(max_retries=3, backoff_base=0))
        self.responses = list(responses)
        self.calls = []

    def _http(self, method, url, payload, *, timeout):
        self.calls.append(payload)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)


def test_401_is_not_retried():
    synth = FakeSynth([OpenAICompatHTTPError(401, "bad api key")])
    with pytest.raises(OpenAICompatError, match="non-retryable HTTP 401"):
        synth._post_chat({"model": "m", "messages": []})
    assert len(synth.calls) == 1


def test_500_is_retried():
    synth = FakeSynth([OpenAICompatHTTPError(500, "boom")] * 3)
    with pytest.raises(OpenAICompatError, match="failed after retries"):
        synth._post_chat({"model": "m", "messages": []})
    assert len(synth.calls) == 3


def test_400_drops_chat_template_kwargs_once():
    synth = FakeSynth(
        [
            OpenAICompatHTTPError(400, "unknown field chat_template_kwargs"),
            {"choices": [{"message": {"content": "ok"}}]},
        ]
    )
    payload = {
        "model": "m",
        "messages": [],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    result = synth._post_chat(payload)
    assert len(synth.calls) == 2
    assert "chat_template_kwargs" in synth.calls[0]
    assert "chat_template_kwargs" not in synth.calls[1]
    assert result == {"choices": [{"message": {"content": "ok"}}]}


def test_429_is_retried_then_succeeds():
    synth = FakeSynth(
        [
            OpenAICompatHTTPError(429, "slow down"),
            {"choices": [{"message": {"content": "ok"}}]},
        ]
    )
    result = synth._post_chat({"model": "m", "messages": []})
    assert len(synth.calls) == 2
    assert result == {"choices": [{"message": {"content": "ok"}}]}


def test_remote_host_logs_privacy_warning(caplog):
    with caplog.at_level("WARNING", logger="llmwiki.synth.openai_compat"):
        config = load_openai_config(
            {"synthesis": {"backend": "openai", "base_url": "http://10.0.0.5:8001/v1"}}
        )
    assert config.is_local is False
    assert "leave this machine" in caplog.text


def test_loopback_host_does_not_warn(caplog):
    with caplog.at_level("WARNING", logger="llmwiki.synth.openai_compat"):
        config = load_openai_config(
            {"synthesis": {"backend": "openai", "base_url": "http://127.0.0.1:8001/v1"}}
        )
    assert config.is_local is True
    assert caplog.text == ""


def test_negative_backoff_is_rejected():
    with pytest.raises(ValueError, match="backoff_base"):
        load_openai_config(
            {"synthesis": {"backend": "openai", "backoff_base": -1}}
        )

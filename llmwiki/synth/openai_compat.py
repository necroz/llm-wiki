"""OpenAI-compatible backend for LLM synthesis.

Talks to any server that exposes the OpenAI ``/v1`` chat-completions
surface: vLLM, SGLang, LM Studio, LiteLLM, or a hosted provider. Stdlib
only — the same ``urllib`` approach as the Ollama backend, no SDK.

Configuration (``examples/sessions_config.json``)::

    "synthesis": {
      "backend": "openai",
      "model": "qwen3.8",
      "base_url": "http://127.0.0.1:8001/v1",
      "api_key": "",
      "timeout": 180,
      "max_retries": 3,
      "max_tokens": 1500,
      "temperature": 0.1,
      "max_concurrency": 10
    }

``base_url`` must include the API prefix (``/v1``). ``api_key`` is
optional; when set it is sent as ``Authorization: Bearer``.

Thinking models (Qwen3) are told to skip the reasoning phase with
``chat_template_kwargs: {"enable_thinking": false}``. The flag can be
disabled with ``"enable_thinking": true`` in config, in which case the
backend falls back to ``reasoning_content`` when ``content`` is empty.
A strict server that rejects ``chat_template_kwargs`` with HTTP 400 is
retried once without the field.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from llmwiki.synth.base import BaseSynthesizer

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8001/v1"
DEFAULT_MODEL = "qwen3.8"
DEFAULT_TIMEOUT = 180
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_MAX_TOKENS = 1500
DEFAULT_TEMPERATURE = 0.1


class OpenAICompatError(RuntimeError):
    """Base class for OpenAI-compatible backend failures."""


class OpenAICompatUnavailableError(OpenAICompatError):
    """Raised when the server is unreachable or the health probe fails."""


class OpenAICompatHTTPError(OpenAICompatError):
    """Raised when the server returns a non-2xx response.

    Retryable statuses (429, 5xx) are retried by ``_post_chat``; other
    statuses reach the caller immediately.
    """

    def __init__(self, status: int, body: str):
        super().__init__(f"OpenAI-compatible server returned HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


@dataclass(frozen=True)
class OpenAICompatConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    timeout: int = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base: float = DEFAULT_BACKOFF_BASE
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE
    enable_thinking: bool = False

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"

    @property
    def is_local(self) -> bool:
        host = urllib.parse.urlsplit(self.base_url).hostname or ""
        return host in {"127.0.0.1", "localhost", "::1"}


def load_openai_config(cfg: dict[str, Any] | None) -> OpenAICompatConfig:
    synth = (cfg or {}).get("synthesis", {}) or {}
    model = synth.get("model") or DEFAULT_MODEL
    base_url = synth.get("base_url") or DEFAULT_BASE_URL
    api_key = str(synth.get("api_key") or "")
    timeout = int(synth["timeout"]) if "timeout" in synth else DEFAULT_TIMEOUT
    max_retries = (
        int(synth["max_retries"]) if "max_retries" in synth else DEFAULT_MAX_RETRIES
    )
    backoff_base = (
        float(synth["backoff_base"])
        if "backoff_base" in synth
        else DEFAULT_BACKOFF_BASE
    )
    max_tokens = (
        int(synth["max_tokens"]) if "max_tokens" in synth else DEFAULT_MAX_TOKENS
    )
    temperature = (
        float(synth["temperature"])
        if "temperature" in synth
        else DEFAULT_TEMPERATURE
    )
    enable_thinking = bool(synth.get("enable_thinking", False))

    if timeout <= 0:
        raise ValueError(f"synthesis.timeout must be positive, got {timeout}")
    if max_retries < 1:
        raise ValueError(
            f"synthesis.max_retries must be >= 1, got {max_retries}"
        )
    if max_tokens < 1:
        raise ValueError(
            f"synthesis.max_tokens must be >= 1, got {max_tokens}"
        )
    if backoff_base < 0:
        raise ValueError(
            f"synthesis.backoff_base must be >= 0, got {backoff_base}"
        )
    resolved = OpenAICompatConfig(
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=timeout,
        max_retries=max_retries,
        backoff_base=backoff_base,
        max_tokens=max_tokens,
        temperature=temperature,
        enable_thinking=enable_thinking,
    )
    if not resolved.is_local:
        logger.warning(
            "OpenAI-compatible backend pointed at non-local host %s — transcript "
            "data will leave this machine. Set synthesis.base_url to a loopback "
            "address to keep transcripts local.",
            resolved.base_url,
        )
    return resolved


def _render_prompt(template: str, *, raw_body: str, meta: dict[str, Any]) -> str:
    meta_dump = json.dumps(meta, indent=2, default=str, sort_keys=True)
    return template.replace("{body}", raw_body).replace("{meta}", meta_dump)


def _truncate_body(body: str, limit: int = 12000) -> str:
    """Keep the opening and the conclusion of a long transcript.

    A pure head slice loses the outcome, which is the most valuable
    part of a session. Two thirds head plus one third tail keeps both
    the problem statement and the final result.
    """
    if not body or len(body) <= limit:
        return body
    head_len = (limit * 2) // 3
    tail_len = limit - head_len
    return (
        body[:head_len]
        + "\n\n...[middle of transcript truncated]...\n\n"
        + body[-tail_len:]
    )


class OpenAICompatSynthesizer(BaseSynthesizer):
    """Synthesize source pages through an OpenAI-compatible ``/v1`` API."""

    def __init__(self, config: OpenAICompatConfig | None = None):
        self.config = config or OpenAICompatConfig()

    @property
    def name(self) -> str:
        return f"OpenAICompatSynthesizer({self.config.model})"

    def is_available(self) -> bool:
        try:
            body = self._http("GET", self.config.models_url, None, timeout=10)
        except Exception:
            return False
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return False
        return isinstance(data, dict) and isinstance(data.get("data"), list)

    def synthesize_source_page(
        self,
        raw_body: str,
        meta: dict[str, Any],
        prompt_template: str,
    ) -> str:
        # Cap the transcript like the Ollama backend: the page is a
        # summary, and a 400 KB session would dominate the context.
        truncated = _truncate_body(raw_body, limit=12000)
        prompt = _render_prompt(prompt_template, raw_body=truncated, meta=meta)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        if not self.config.enable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        data = self._post_chat(payload)
        choices = data.get("choices") or []
        if not choices:
            raise OpenAICompatError("response has no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            # Some servers put the whole answer in reasoning_content
            # when thinking is enabled. Use it rather than losing the
            # generation, but only as a fallback.
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                content = reasoning
        if not isinstance(content, str) or not content.strip():
            raise OpenAICompatError("response content is empty")
        return content.strip()

    # ---- internals -----------------------------------------------

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_exc: Exception | None = None
        fallback_used = False
        attempt = 1
        while attempt <= self.config.max_retries:
            try:
                body = self._http(
                    "POST", self.config.chat_url, payload, timeout=self.config.timeout
                )
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise OpenAICompatError("response is not a JSON object")
                return data
            except OAIConnectionError:
                raise
            except OpenAICompatHTTPError as exc:
                if (
                    exc.status == 400
                    and "chat_template_kwargs" in payload
                    and not fallback_used
                ):
                    # vLLM understands chat_template_kwargs; a strict
                    # OpenAI-compatible server rejects it with 400.
                    # Retry once without the key.
                    payload = {
                        key: value
                        for key, value in payload.items()
                        if key != "chat_template_kwargs"
                    }
                    fallback_used = True
                    continue
                if 400 <= exc.status < 500 and exc.status != 429:
                    raise OpenAICompatError(
                        f"non-retryable HTTP {exc.status}: {exc.body[:200]}"
                    ) from exc
                last_exc = exc
            except (OSError, TimeoutError, json.JSONDecodeError) as exc:
                last_exc = exc

            if attempt < self.config.max_retries:
                logger.warning(
                    "OpenAI-compatible request failed (attempt %d/%d): %s; retrying",
                    attempt,
                    self.config.max_retries,
                    last_exc,
                )
                time.sleep(self.config.backoff_base * (2 ** (attempt - 1)))
            attempt += 1

        raise OpenAICompatError(
            f"chat completion failed after retries: {last_exc}"
        )

    def _http(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        *,
        timeout: int,
    ) -> str:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise OpenAICompatHTTPError(exc.code, body) from exc
        except urllib.error.URLError as exc:
            raise OAIConnectionError(str(exc.reason)) from exc


class OAIConnectionError(OpenAICompatUnavailableError):
    """Raised when the server cannot be reached (no retry)."""
# 07 — Do not retry non-retryable OpenAI errors

- Priority: P2
- Depends on: none
- Size: M (1–2 hours)
- Files: `llmwiki/synth/openai_compat.py`, new `tests/test_openai_compat.py`
- PR title: `fix: retry only transient OpenAI-compatible errors`

## Read first

- `llmwiki/synth/openai_compat.py`, class `OpenAICompatSynthesizer`,
  method `_post_chat` (around line 218)
- The module docstring, especially the `chat_template_kwargs` paragraph
- `llmwiki/synth/ollama.py`, `_call_generate`, for the existing retry
  style

## Problem

`_post_chat` catches every `OpenAICompatHTTPError` and retries it,
including 400, 401, 403, and 404. With the default `max_retries=3` and
`backoff_base=1.0`, a bad API key or an unsupported field costs several
seconds per page before failing.

It also always sends:

```python
payload["chat_template_kwargs"] = {"enable_thinking": False}
```

That field is understood by vLLM. A strict OpenAI-compatible server can
reject it with HTTP 400. The backend then retries the same rejected
payload three times.

## Required change

Rewrite `_post_chat` to follow these rules:

1. `OAIConnectionError` — raise immediately, as today.
2. HTTP 400 with `chat_template_kwargs` in the payload — remove that
   key from the payload and try once more. Do this at most once; keep a
   `fallback_used` flag so a second 400 raises.
3. HTTP 400/401/403/404/422 — raise `OpenAICompatError` immediately
   with the status and the first 200 characters of the body. Do not
   sleep and do not retry.
4. HTTP 429 and 5xx — retry with the existing exponential backoff.
5. `OSError`, `TimeoutError`, `json.JSONDecodeError` — retry with
   backoff.
6. After the last attempt, raise
   `OpenAICompatError("chat completion failed after retries: ...")`.

A `while` loop is easier than the current `for` because the fallback
retry does not consume a normal attempt:

```python
def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
    last_exc: Exception | None = None
    fallback_used = False
    attempt = 1
    while attempt <= self.config.max_retries:
        try:
            body = self._http("POST", self.config.chat_url, payload,
                              timeout=self.config.timeout)
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
            time.sleep(self.config.backoff_base * (2 ** (attempt - 1)))
        attempt += 1

    raise OpenAICompatError(
        f"chat completion failed after retries: {last_exc}"
    )
```

Keep the existing exception classes and `_http` unchanged.

## Tests

Create `tests/test_openai_compat.py`. Use a subclass that replaces
`_http`:

```python
class FakeSynth(OpenAICompatSynthesizer):
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
```

Add these tests:

1. `test_401_is_not_retried` — one `OpenAICompatHTTPError(401, ...)`,
   assert `len(calls) == 1` and `OpenAICompatError` is raised.
2. `test_500_is_retried` — three 500 errors, assert `len(calls) == 3`.
3. `test_400_drops_chat_template_kwargs_once` — first response is
   `OpenAICompatHTTPError(400, "unknown field chat_template_kwargs")`,
   second is a valid `{"choices": [{"message": {"content": "ok"}}]}`.
   Assert `len(calls) == 2`, the first payload has the key, the second
   does not, and the result is `{"choices": ...}`.
4. `test_429_is_retried_then_succeeds` — 429 then a valid response,
   assert `len(calls) == 2`.

Use `backoff_base=0` so the tests do not sleep.

## Verification

```bash
.venv/bin/python -m pytest tests/test_openai_compat.py -q
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m llmwiki synthesize --check
```

Expected: all tests pass, and `--check` still reports `Available: True`
for the configured server.

## Acceptance criteria

- [ ] 4xx other than 429 is not retried.
- [ ] 400 with `chat_template_kwargs` retries once without the key.
- [ ] 429 and 5xx still retry with backoff.
- [ ] Four new tests pass.
- [ ] `llmwiki synthesize --check` still works.
- [ ] `CHANGELOG.md` updated.

## Out of scope

- Changing the Ollama backend.
- Adding streaming.
- Changing model names or prompt text.

## Rollback

Restore the old `for` loop. No config or data changes.

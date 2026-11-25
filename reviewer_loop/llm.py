"""Thin wrappers around the two LLM providers.

Every call goes through the same steps:
  1. disk cache: an identical request (same model + same prompt) is answered from .cache/llm, costing 0 tokens
  2. pacing: stay under the free-tier rate limits (Groq tokens/minute, Gemini requests/minute)
  3. retries: rate-limit (429), server errors (5xx) and connection errors are retried with back-off
  4. accounting: token usage is returned with every result and added to a per-day counter
Logging goes to stderr: inside the MCP server, stdout is reserved for the protocol.
"""
import datetime
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ProviderError(Exception):
    """A provider failed even after retries, or a local budget guard refused the call."""


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def log(msg: str) -> None:
    print(f"[llm] {msg}", file=sys.stderr, flush=True)


# ---------- retries ----------

def retry_delay(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying `exc`, or None if it is not worth retrying (e.g. 400, 401)."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    name = type(exc).__name__
    if status not in RETRYABLE_STATUS and "Connection" not in name and "Timeout" not in name:
        return None
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        return min(float(headers.get("retry-after")), 60.0)
    except (TypeError, ValueError):
        return 5.0 * 2 ** (attempt - 1)  # 5s, 10s, 20s


def with_retries(fn, attempts: int = config.MAX_PROVIDER_RETRIES, sleep=time.sleep):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ProviderError:
            raise
        except Exception as exc:  # provider SDKs raise many exception types; classify them below
            delay = retry_delay(exc, attempt)
            if delay is None or attempt == attempts:
                raise ProviderError(f"{type(exc).__name__}: {str(exc)[:300]}") from exc
            log(f"{type(exc).__name__} (attempt {attempt}/{attempts}), retrying in {delay:.0f}s")
            sleep(delay)


# ---------- cache and daily budget ----------

def _cache_file(model: str, payload) -> Path:
    key = hashlib.sha256(json.dumps([model, payload], sort_keys=True).encode()).hexdigest()[:32]
    return config.CACHE_DIR / "llm" / f"{key}.json"


def _usage_file():
    return config.CACHE_DIR / f"usage_{datetime.date.today().isoformat()}.json"


def tokens_used_today(provider: str) -> int:
    path = _usage_file()
    return json.loads(path.read_text()).get(provider, 0) if path.exists() else 0


def _add_daily_usage(provider: str, tokens: int) -> None:
    path = _usage_file()
    data = json.loads(path.read_text()) if path.exists() else {}
    data[provider] = data.get(provider, 0) + tokens
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _cached_call(model: str, payload, call) -> LLMResult:
    path = _cache_file(model, payload)
    if path.exists():
        return LLMResult(**{**json.loads(path.read_text(encoding="utf-8")), "cached": True})
    result = with_retries(call)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result)), encoding="utf-8")
    return result


# ---------- Groq (generator) ----------

_groq_window: list[tuple[float, int]] = []  # (timestamp, tokens) of calls in the last minute


def _pace_groq(estimated_tokens: int) -> None:
    """Sleep until the last-60s token total leaves room for this call (8k tokens/minute on free tier)."""
    while True:
        now = time.time()
        _groq_window[:] = [(t, n) for t, n in _groq_window if now - t < 60]
        used = sum(n for _, n in _groq_window)
        if not _groq_window or used + estimated_tokens <= config.GROQ_TOKENS_PER_MINUTE:
            return
        wait = 60 - (now - _groq_window[0][0]) + 0.5
        log(f"Groq pacing: {used} tokens used in the last minute, waiting {wait:.0f}s")
        time.sleep(wait)


def call_groq(messages: list[dict], max_tokens: int = 4000) -> LLMResult:
    """Chat completion in JSON mode (the prompt must ask for JSON)."""
    from groq import Groq

    def call() -> LLMResult:
        if tokens_used_today(config.GENERATOR_MODEL) > config.GROQ_DAILY_TOKEN_BUDGET:
            raise ProviderError("Local daily Groq token budget exhausted (protecting the free-tier quota)")
        _pace_groq(len(json.dumps(messages)) // 4 + 1500)
        client = Groq(api_key=config.GROQ_KEY, max_retries=0)  # retries are handled by with_retries()
        response = client.chat.completions.create(
            model=config.GENERATOR_MODEL, messages=messages, response_format={"type": "json_object"},
            reasoning_effort="low", max_completion_tokens=max_tokens, temperature=0.2)
        usage = response.usage
        _groq_window.append((time.time(), usage.total_tokens))
        _add_daily_usage(config.GENERATOR_MODEL, usage.total_tokens)  # Groq counts quota per model
        return LLMResult(response.choices[0].message.content or "", config.GENERATOR_MODEL,
                         usage.prompt_tokens, usage.completion_tokens)

    return _cached_call(config.GENERATOR_MODEL, {"messages": messages, "max_tokens": max_tokens}, call)


# ---------- Gemini (reviewer) ----------

_gemini_last_call = 0.0


def call_gemini(system: str, prompt: str, response_schema) -> LLMResult:
    """JSON output constrained to `response_schema` (a Pydantic class). We still validate it ourselves.

    If the main reviewer model stays unavailable after retries (e.g. 503 "high demand"), the fallback model
    is tried once. Both are Google models, so the reviewer is still from a different provider than the generator.
    """
    from google import genai
    from google.genai import types

    def make_call(model: str):
        def call() -> LLMResult:
            global _gemini_last_call
            wait = config.GEMINI_MIN_SECONDS_BETWEEN_CALLS - (time.time() - _gemini_last_call)
            if wait > 0:
                time.sleep(wait)
            _gemini_last_call = time.time()
            client = genai.Client(api_key=config.GEMINI_KEY)  # keep a reference: the client closes when collected
            response = client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system, temperature=0.2,
                                                   response_mime_type="application/json",
                                                   response_schema=response_schema))
            meta = response.usage_metadata
            completion = (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)
            _add_daily_usage(model, (meta.prompt_token_count or 0) + completion)
            return LLMResult(response.text or "", model, meta.prompt_token_count or 0, completion)
        return call

    payload = {"system": system, "prompt": prompt}
    try:
        return _cached_call(config.REVIEWER_MODEL, payload, make_call(config.REVIEWER_MODEL))
    except ProviderError as exc:
        if not any(code in str(exc) for code in ("429", "500", "503", "UNAVAILABLE")):
            raise
        log(f"{config.REVIEWER_MODEL} unavailable ({str(exc)[:80]}), falling back to {config.REVIEWER_FALLBACK_MODEL}")
        return _cached_call(config.REVIEWER_FALLBACK_MODEL, payload, make_call(config.REVIEWER_FALLBACK_MODEL))

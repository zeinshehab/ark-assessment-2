"""Thin wrappers around the two LLM providers.

Every call goes through the same steps:
  1. disk cache: an identical request (same model + same prompt) is answered from .cache/llm, costing 0 tokens
  2. pacing: stay under the free-tier rate limits (Groq tokens/minute, Gemini requests/minute)
  3. retries: rate-limit (429), server errors (5xx) and connection errors are retried with back-off;
     a timeout is retried once; auth errors and other 4xx are never retried
  4. accounting: token usage is returned with every result and added to a per-day counter
Around that, in the app process: a circuit breaker per provider, the run deadline, and metrics + logs per call.
Logging goes through `logs.py` (stderr, redacted): inside the MCP server stdout is reserved for the protocol.
"""
import datetime
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, metrics
from .breaker import BREAKERS
from .errors import ProviderError, check_deadline, sleep_within_deadline

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


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


log = logging.getLogger("reviewer_loop.llm")


# ---------- retries ----------

def _status(exc: Exception):
    return getattr(exc, "status_code", None) or getattr(exc, "code", None)


def error_type_of(exc: Exception) -> str:
    """Classify a provider SDK exception into one of errors.ERROR_TYPES."""
    status, name = _status(exc), type(exc).__name__
    if status in (401, 403):
        return "provider_auth"
    if status == 429:
        return "rate_limited"
    if "Timeout" in name:
        return "provider_timeout"
    if "json_validate_failed" in str(exc):
        return "malformed_output"
    if isinstance(status, int) and 400 <= status < 500:
        return "provider_rejected"
    return "provider_unavailable"


def retry_delay(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying `exc`, or None if it is not worth retrying (e.g. 400, 401).

    A timeout is retried only once: the provider may have done (and billed) the work, and a second slow answer
    usually means it is overloaded, which the circuit breaker should see sooner rather than later.
    """
    status, name = _status(exc), type(exc).__name__
    if status not in RETRYABLE_STATUS and "Connection" not in name and "Timeout" not in name:
        return None
    if "Timeout" in name and attempt >= 2:
        return None
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        return min(float(headers.get("retry-after")), 60.0)
    except (TypeError, ValueError):
        return 5.0 * 2 ** (attempt - 1)  # 5s, 10s, 20s


def with_retries(fn, attempts: int | None = None, sleep=time.sleep):
    """Call fn(), retrying retryable failures with back-off. Never sleeps past the run deadline."""
    attempts = attempts or config.get_settings().max_provider_retries
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ProviderError:
            raise
        except Exception as exc:  # provider SDKs raise many exception types; classify them below
            delay = retry_delay(exc, attempt)
            if delay is None or attempt == attempts:
                raise ProviderError(f"{type(exc).__name__}: {str(exc)[:300]}", error_type_of(exc)) from exc
            log.warning("provider call failed, retrying", extra={
                "error": type(exc).__name__, "attempt": attempt, "max_attempts": attempts, "retry_in_s": round(delay, 1)})
            sleep_within_deadline(delay, sleep)


# ---------- cache and daily budget ----------

def _cache_file(model: str, payload) -> Path:
    key = hashlib.sha256(json.dumps([model, payload], sort_keys=True).encode()).hexdigest()[:32]
    return config.get_settings().cache_dir / "llm" / f"{key}.json"


def _usage_file():
    return config.get_settings().cache_dir / f"usage_{datetime.date.today().isoformat()}.json"


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
    if not config.get_settings().llm_cache_enabled:
        return with_retries(call)
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
        if not _groq_window or used + estimated_tokens <= config.get_settings().groq_tokens_per_minute:
            return
        wait = 60 - (now - _groq_window[0][0]) + 0.5
        log.info("groq pacing wait", extra={"tokens_last_minute": used, "wait_s": round(wait, 1)})
        sleep_within_deadline(wait)


def call_groq(messages: list[dict], max_tokens: int = 4000) -> LLMResult:
    """Chat completion in JSON mode (the prompt must ask for JSON)."""
    from groq import Groq

    settings = config.get_settings()
    model = settings.generator_model

    def call() -> LLMResult:
        if tokens_used_today(model) > settings.groq_daily_token_budget:
            raise ProviderError("Local daily Groq token budget exhausted (protecting the free-tier quota)",
                                "budget_exceeded")
        _pace_groq(len(json.dumps(messages)) // 4 + 1500)
        client = Groq(api_key=settings.groq_key.get_secret_value(), max_retries=0,  # we retry in with_retries()
                      timeout=settings.provider_timeout_s)
        response = client.chat.completions.create(
            model=model, messages=messages, response_format={"type": "json_object"},
            reasoning_effort="low", max_completion_tokens=max_tokens, temperature=0.2)
        usage = response.usage
        _groq_window.append((time.time(), usage.total_tokens))
        _add_daily_usage(model, usage.total_tokens)  # Groq counts quota per model
        return LLMResult(response.choices[0].message.content or "", model,
                         usage.prompt_tokens, usage.completion_tokens)

    breaker = BREAKERS["groq"]
    breaker.check()       # fail fast while Groq is known to be down
    check_deadline()      # don't start a call the run has no time left for
    start = time.monotonic()
    try:
        result = _cached_call(model, {"messages": messages, "max_tokens": max_tokens}, call)
    except ProviderError as exc:
        breaker.record(exc.error_type)
        metrics.record_provider_call("groq", model, exc.error_type, time.monotonic() - start)
        raise
    breaker.record(None)
    metrics.record_provider_call("groq", model, "cached" if result.cached else "ok", time.monotonic() - start,
                                 result.prompt_tokens, result.completion_tokens)
    return result


# ---------- Gemini (reviewer) ----------

_gemini_last_call = 0.0


def call_gemini(system: str, prompt: str, response_schema) -> LLMResult:
    """JSON output constrained to `response_schema` (a Pydantic class). We still validate it ourselves.

    If the main reviewer model stays unavailable after retries (e.g. 503 "high demand"), the fallback model
    is tried once. Both are Google models, so the reviewer is still from a different provider than the generator.
    """
    from google import genai
    from google.genai import types

    settings = config.get_settings()

    def make_call(model: str):
        def call() -> LLMResult:
            global _gemini_last_call
            wait = settings.gemini_min_seconds_between_calls - (time.time() - _gemini_last_call)
            if wait > 0:
                time.sleep(wait)
            _gemini_last_call = time.time()
            client = genai.Client(api_key=settings.gemini_key.get_secret_value(),  # keep a reference: it closes when collected
                                  http_options=types.HttpOptions(timeout=int(settings.provider_timeout_s * 1000)))
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
        return _cached_call(settings.reviewer_model, payload, make_call(settings.reviewer_model))
    except ProviderError as exc:
        if not any(code in str(exc) for code in ("429", "500", "503", "UNAVAILABLE")):
            raise
        log.warning("reviewer model unavailable, using fallback", extra={
            "model": settings.reviewer_model, "fallback": settings.reviewer_fallback_model, "error": str(exc)[:120]})
        return _cached_call(settings.reviewer_fallback_model, payload, make_call(settings.reviewer_fallback_model))

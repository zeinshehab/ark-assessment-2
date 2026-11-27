"""Prometheus metrics, exposed by the API at /metrics.

They answer the operator's questions directly:
  is it running?            -> /healthz; runs_in_progress, run_queue_depth
  are requests succeeding?  -> http_requests_total{status}, runs_total{status,error_type}
  is a provider failing?    -> provider_calls_total{outcome}, provider_circuit_open
  how long do things take?  -> http_request_duration_seconds, run_duration_seconds, provider_call_duration_seconds
  what type of error?       -> the error_type / outcome labels (one of errors.ERROR_TYPES)

Metrics live in the app process. Gemini is called inside the MCP server process, so the orchestrator records
Gemini metrics from the review_code result instead.
"""
import logging

from prometheus_client import Counter, Gauge, Histogram

log = logging.getLogger("reviewer_loop.metrics")

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["route", "method", "status"])
HTTP_DURATION = Histogram("http_request_duration_seconds", "HTTP request duration", ["route"],
                          buckets=(0.01, 0.05, 0.1, 0.5, 1, 5))

RUNS = Counter("runs_total", "Finished review runs", ["status", "error_type"])
RUN_DURATION = Histogram("run_duration_seconds", "Review run duration",
                         buckets=(30, 60, 120, 240, 480, 900))
RUNS_IN_PROGRESS = Gauge("runs_in_progress", "Review runs currently executing")
QUEUE_DEPTH = Gauge("run_queue_depth", "Review runs waiting for a worker")

PROVIDER_CALLS = Counter("provider_calls_total", "LLM provider calls", ["provider", "model", "outcome"])
PROVIDER_DURATION = Histogram("provider_call_duration_seconds", "LLM provider call duration (incl. retries)",
                              ["provider"], buckets=(1, 2, 5, 10, 20, 40, 80, 160))
PROVIDER_TOKENS = Counter("provider_tokens_total", "Tokens used", ["provider", "kind"])
CIRCUIT_OPEN = Gauge("provider_circuit_open", "1 if the provider's circuit breaker is open", ["provider"])


def record_provider_call(provider: str, model: str, outcome: str, seconds: float,
                         prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """One place that logs and counts a provider call. outcome is "ok", "cached" or an error type."""
    PROVIDER_CALLS.labels(provider, model, outcome).inc()
    if outcome != "cached":
        PROVIDER_DURATION.labels(provider).observe(seconds)
    PROVIDER_TOKENS.labels(provider, "prompt").inc(prompt_tokens)
    PROVIDER_TOKENS.labels(provider, "completion").inc(completion_tokens)
    level = logging.INFO if outcome in ("ok", "cached") else logging.WARNING
    log.log(level, "provider call", extra={"provider": provider, "model": model, "outcome": outcome,
                                           "latency_ms": round(seconds * 1000), "prompt_tokens": prompt_tokens,
                                           "completion_tokens": completion_tokens})

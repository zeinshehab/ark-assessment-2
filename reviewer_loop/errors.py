"""Error types and the per-run deadline.

Every failure ends up as one of a small, fixed set of error types. The same name appears in the run record (what
the user sees), in the logs and in the metrics (what the operator sees), so "what kind of error was it?" always has
a short answer.
"""
import contextvars
import time

ERROR_TYPES = (
    "invalid_input",         # the request was rejected before any work started
    "rate_limited",          # a provider kept answering 429 after our retries
    "provider_timeout",      # a provider did not answer within PROVIDER_TIMEOUT_S
    "provider_unavailable",  # 5xx / connection errors after retries, or the circuit breaker is open
    "provider_auth",         # 401/403: wrong or revoked API key (never retried)
    "provider_rejected",     # any other 4xx from a provider (never retried)
    "malformed_output",      # a model answered twice with output that failed validation
    "budget_exceeded",       # our own daily token budget guard refused the call
    "tool_failure",          # the MCP server crashed, could not start, or the connection broke
    "deadline_exceeded",     # the run used up RUN_DEADLINE_S
    "internal",              # a bug in our code
)

# Provider failures that count towards opening a circuit breaker (an outage, not a bad request of ours)
OUTAGE_TYPES = {"rate_limited", "provider_timeout", "provider_unavailable", "provider_auth"}


class AppError(Exception):
    error_type = "internal"

    def __init__(self, message: str, error_type: str | None = None):
        super().__init__(message)
        if error_type:
            if error_type not in ERROR_TYPES:
                raise ValueError(f"unknown error type {error_type!r}")
            self.error_type = error_type


class ProviderError(AppError):
    """A provider failed even after retries, or a local guard refused the call."""
    error_type = "provider_unavailable"


class DeadlineExceeded(AppError):
    error_type = "deadline_exceeded"


def unwrap(exc: BaseException) -> BaseException:
    """The MCP SDK uses task groups, which wrap failures in ExceptionGroups: look at the first real error."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def classify(exc: BaseException) -> str:
    """Map any exception to one of ERROR_TYPES."""
    exc = unwrap(exc)
    if isinstance(exc, AppError):
        return exc.error_type
    message = str(exc)
    if message.startswith("[") and "]" in message:  # MCP tool errors carry the server-side type: "[type] ..."
        tagged = message[1:message.index("]")]
        if tagged in ERROR_TYPES:
            return tagged
    module = type(exc).__module__ or ""
    if module.startswith(("mcp", "anyio")) or isinstance(exc, (ConnectionError, EOFError, BrokenPipeError)):
        return "tool_failure"
    if type(exc).__name__ == "ValidationError":
        return "malformed_output"
    return "internal"


# ---------- per-run deadline ----------
# Runs execute in a worker thread that cannot be killed, so the run checks its own deadline: before every LLM call,
# instead of sleeping past it, and at each step. Async MCP tool calls are cancelled with asyncio.wait_for instead.

run_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("run_deadline", default=None)


def seconds_left() -> float | None:
    deadline = run_deadline.get()
    return None if deadline is None else deadline - time.monotonic()


def check_deadline() -> None:
    left = seconds_left()
    if left is not None and left <= 0:
        raise DeadlineExceeded("The run exceeded its time budget (RUN_DEADLINE_S)")


def sleep_within_deadline(seconds: float, sleep=time.sleep) -> None:
    """Sleep, unless the sleep would end after the deadline: then fail now instead of waiting for nothing."""
    left = seconds_left()
    if left is not None and seconds >= left:
        raise DeadlineExceeded("The run would exceed its time budget while waiting to retry")
    sleep(seconds)

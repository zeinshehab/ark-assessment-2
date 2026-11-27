"""A circuit breaker per provider, kept in the app process.

When a provider is down, retrying every call of every run wastes minutes and quota. After BREAKER_FAILURES calls
in a row have failed with an outage-type error (after their own retries), the breaker "opens": calls fail
immediately with provider_unavailable for BREAKER_COOLDOWN_S. After the cool-down one trial call is let through
("half-open"): success closes the breaker, failure opens it again.

Why in the app process: the MCP server (where Gemini is called) is started fresh for every run, so a breaker there
would forget everything between runs. The orchestrator feeds Gemini's breaker from the review_code results.
"""
import threading
import time

from . import config, metrics
from .errors import OUTAGE_TYPES, ProviderError


class CircuitBreaker:
    def __init__(self, name: str, clock=time.monotonic):
        self.name = name
        self.clock = clock
        self.failures = 0
        self.opened_at: float | None = None
        self.last_error_type: str | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if self.clock() - self.opened_at < config.get_settings().breaker_cooldown_s:
            return "open"
        return "half_open"

    def check(self) -> None:
        """Raise immediately if the provider is known to be failing."""
        if self.state == "open":
            raise ProviderError(f"{self.name} is failing ({self.failures} failures in a row, last: "
                                f"{self.last_error_type}); not calling it for a while", "provider_unavailable")

    def record(self, error_type: str | None) -> None:
        """Report the outcome of one call: None for success, else its error type."""
        with self._lock:
            if error_type is None:
                self.failures, self.opened_at = 0, None
            elif error_type in OUTAGE_TYPES:
                self.failures += 1
                self.last_error_type = error_type
                if self.failures >= config.get_settings().breaker_failures:
                    self.opened_at = self.clock()


BREAKERS = {"groq": CircuitBreaker("groq"), "gemini": CircuitBreaker("gemini")}

# /metrics reads the live state at scrape time, so the gauge drops to 0 by itself when the cool-down ends
for _name, _breaker in BREAKERS.items():
    metrics.CIRCUIT_OPEN.labels(_name).set_function(lambda b=_breaker: 1 if b.state == "open" else 0)

"""Failure handling added for production: error types, timeouts, circuit breakers, deadlines, crashed MCP servers
and runaway generated code. No real API calls: providers are fakes, the MCP server and sandbox are real."""
import asyncio
import sys
import time

import pytest

from reviewer_loop import errors, generator, llm, mcp_client, orchestrator, sandbox
from reviewer_loop.breaker import BREAKERS, CircuitBreaker
from reviewer_loop.errors import ProviderError
from reviewer_loop.schemas import GeneratorOutput
from tests.conftest import DUMMY_GROQ_KEY

SOLUTION = "def add(a, b):\n    return a + b\n"
TESTS = "from solution import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def _fake_draft(monkeypatch):
    monkeypatch.setattr(generator, "draft",
                        lambda task: (GeneratorOutput(solution_code=SOLUTION, test_code=TESTS), {}))


# ---------- retries and error types ----------

class APITimeoutError(Exception):
    """Same class name as the Groq SDK's timeout error (classification is by name and status code)."""


class HTTPError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_timeouts_are_retried_only_once():
    calls, sleeps = [], []

    def always_times_out():
        calls.append(1)
        raise APITimeoutError("read timed out")

    with pytest.raises(ProviderError) as err:
        llm.with_retries(always_times_out, attempts=4, sleep=sleeps.append)
    assert len(calls) == 2 and len(sleeps) == 1
    assert err.value.error_type == "provider_timeout"


@pytest.mark.parametrize("status,error_type", [(401, "provider_auth"), (403, "provider_auth"),
                                               (400, "provider_rejected"), (429, "rate_limited"),
                                               (503, "provider_unavailable")])
def test_provider_errors_get_a_type(status, error_type):
    def fail():
        raise HTTPError(status)

    with pytest.raises(ProviderError) as err:
        llm.with_retries(fail, attempts=2, sleep=lambda s: None)
    assert err.value.error_type == error_type


# ---------- circuit breaker ----------

def test_breaker_opens_after_repeated_outages_then_recovers():
    now = [0.0]
    breaker = CircuitBreaker("test", clock=lambda: now[0])
    breaker.record("malformed_output")   # a bad answer is not an outage
    breaker.record("provider_unavailable")
    breaker.record("provider_timeout")
    breaker.check()                      # 2 outages: still closed
    breaker.record("rate_limited")
    assert breaker.state == "open"
    with pytest.raises(ProviderError) as err:
        breaker.check()
    assert err.value.error_type == "provider_unavailable"
    now[0] += 61                         # cool-down over: one trial call is allowed
    assert breaker.state == "half_open"
    breaker.check()
    breaker.record(None)                 # the trial call succeeded
    assert breaker.state == "closed" and breaker.failures == 0


def test_open_groq_breaker_fails_fast_without_calling_groq(monkeypatch):
    BREAKERS["groq"].failures, BREAKERS["groq"].opened_at = 3, time.monotonic()
    monkeypatch.setattr(llm, "_cached_call", lambda *a: pytest.fail("Groq must not be called"))
    with pytest.raises(ProviderError, match="groq is failing"):
        llm.call_groq([{"role": "user", "content": "hi"}])


# ---------- deadline ----------

class SlowTools:
    """A fake MCP client whose run_tests never finishes in time."""
    calls: list = []

    async def call(self, name, **args):
        await asyncio.sleep(30)


def test_run_deadline_cancels_a_slow_tool_and_keeps_the_draft(monkeypatch):
    _fake_draft(monkeypatch)
    start = time.monotonic()
    record = asyncio.run(orchestrator.run_task("add numbers", SlowTools(), deadline_s=0.5))
    assert time.monotonic() - start < 5
    assert record["status"] == "error" and record["error_type"] == "deadline_exceeded"
    assert record["final"]["solution_code"] == SOLUTION


def test_retry_wait_that_would_pass_the_deadline_fails_immediately():
    token = errors.run_deadline.set(time.monotonic() + 2)
    try:
        with pytest.raises(errors.DeadlineExceeded):
            errors.sleep_within_deadline(10, sleep=lambda s: pytest.fail("must not sleep"))
    finally:
        errors.run_deadline.reset(token)


# ---------- run records ----------

def test_failure_is_typed_redacted_and_reported_through_progress(monkeypatch):
    _fake_draft(monkeypatch)

    def failing_revise(*a, **k):
        raise ProviderError(f"401 invalid key {DUMMY_GROQ_KEY}", "provider_auth")

    class Tools:
        calls = []

        async def call(self, name, **args):
            if name == "run_tests":
                return {"status": "failed", "passed": 0, "failed": 1, "tests": []}
            if name == "run_static_analysis":
                return {"status": "clean", "blocking_count": 0, "advisory_count": 0, "issues": []}
            return {"review": {"summary": "s", "findings": [], "prior_responses": []}, "standard_sections": [],
                    "usage": {"model": "m", "calls": 1, "cached_calls": 0}}

    monkeypatch.setattr(generator, "revise", failing_revise)
    seen = []
    record = asyncio.run(orchestrator.run_task("t", Tools(), on_progress=lambda r: seen.append(r["status"])))
    assert record["error_type"] == "provider_auth"
    assert DUMMY_GROQ_KEY not in record["error"] and "[REDACTED]" in record["error"]
    assert seen and all(s == "running" for s in seen)       # live progress while running
    assert record["prompt_version"] == orchestrator.PROMPT_VERSION


# ---------- MCP server failures (real stdio subprocess) ----------

def test_mcp_server_crash_mid_run_ends_the_run_cleanly(monkeypatch):
    _fake_draft(monkeypatch)
    monkeypatch.setattr(mcp_client, "SERVER_MODULE", "tests.crashing_mcp_server")
    start = time.monotonic()
    record = asyncio.run(orchestrator.run_with_mcp("add numbers", deadline_s=30))
    assert time.monotonic() - start < 30, "a dead server must not hang the run"
    assert record["status"] == "error" and record["error_type"] == "tool_failure"


def test_mcp_server_that_cannot_start_gives_an_error_record(monkeypatch):
    _fake_draft(monkeypatch)
    monkeypatch.setattr(mcp_client, "SERVER_MODULE", "tests.no_such_module")
    record = asyncio.run(orchestrator.run_with_mcp("add numbers", deadline_s=30))
    assert record["status"] == "error" and record["error_type"] == "tool_failure"


# ---------- runaway generated code ----------

@pytest.mark.skipif(sys.platform == "win32", reason="resource limits are POSIX only (they run in CI and Docker)")
def test_memory_bomb_is_stopped_by_the_sandbox_limit():
    bomb = "def grab():\n    return bytearray(4 * 1024**3)\n"
    tests = "from solution import grab\n\n\ndef test_grab():\n    assert grab()\n"
    result = sandbox.run_tests(bomb, tests)
    assert result["status"] == "failed"
    assert "MemoryError" in result["tests"][0]["message"]

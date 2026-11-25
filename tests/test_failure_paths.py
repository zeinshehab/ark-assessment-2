"""Tests for the orchestrator's failure paths. No LLM calls: the models are replaced by fakes,
while the sandbox and ruff run for real. Run with:  python -m pytest tests -q
"""
import asyncio

import pytest

from reviewer_loop import config, generator, ledger, llm, orchestrator, reviewer, sandbox, static_analysis
from reviewer_loop.llm import LLMResult, ProviderError, with_retries
from reviewer_loop.schemas import FindingDecision, GeneratorOutput

SOLUTION = '''def safe_div(a, b):
    if b == 0:
        raise ValueError("b must not be zero")
    return a / b
'''
TESTS = '''import pytest
from solution import safe_div


def test_divides():
    assert safe_div(6, 3) == 2


def test_zero_rejected():
    with pytest.raises(ValueError):
        safe_div(1, 0)
'''


# ---------- sandbox ----------

def test_hanging_code_is_killed():
    result = sandbox.run_tests("while True:\n    pass\n", TESTS, timeout_s=3)
    assert result["status"] == "timeout"


def test_code_that_does_not_run_is_reported():
    assert sandbox.run_tests("def safe_div(a, b:\n", TESTS)["status"] in ("failed", "error")
    assert static_analysis.run_ruff("def safe_div(a, b:\n", TESTS)["blocking_count"] >= 1


def test_sandbox_blocks_network_subprocess_and_hides_secrets(monkeypatch):
    monkeypatch.setenv("GROQ_KEY", "secret")
    tests = '''import os, socket, subprocess

def test_network():
    socket.create_connection(("example.com", 80), timeout=2)

def test_subprocess():
    subprocess.run(["whoami"])

def test_secret_not_visible():
    assert "GROQ_KEY" not in os.environ

def test_read_env_file():
    open(ENV_PATH).read()

def test_write_outside():
    open(os.path.join(os.path.dirname(ENV_PATH), "pwned.txt"), "w").write("x")
'''.replace("ENV_PATH", repr(str(config.ROOT / ".env")))
    outcomes = {t["name"]: t for t in sandbox.run_tests(SOLUTION, tests)["tests"]}
    for name in ("test_network", "test_subprocess", "test_read_env_file", "test_write_outside"):
        assert "sandbox" in outcomes[name]["message"], name
    assert outcomes["test_secret_not_visible"]["outcome"] == "passed"
    assert not (config.ROOT / "pwned.txt").exists()


# ---------- provider errors ----------

class FakeHTTPError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def test_rate_limit_is_retried_then_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise FakeHTTPError(429)
        return "ok"

    assert with_retries(flaky, attempts=3, sleep=lambda s: None) == "ok"
    assert len(calls) == 3


def test_non_retryable_error_fails_fast():
    calls = []

    def unauthorized():
        calls.append(1)
        raise FakeHTTPError(401)

    with pytest.raises(ProviderError):
        with_retries(unauthorized, attempts=3, sleep=lambda s: None)
    assert len(calls) == 1


# ---------- malformed reviewer output ----------

def _fake_gemini(*answers):
    answers = list(answers)
    return lambda system, prompt, schema: LLMResult(answers.pop(0), "fake", 10, 10)


def test_malformed_review_is_rejected_after_one_retry(monkeypatch):
    monkeypatch.setattr(llm, "call_gemini", _fake_gemini("not json", '{"summary": "x"}'))
    with pytest.raises(ValueError, match="failed validation twice"):
        reviewer.review("task", SOLUTION, TESTS, {"status": "passed"}, {"issues": []}, [])


def test_review_pointing_at_missing_line_is_corrected(monkeypatch):
    bad = ('{"summary": "s", "findings": [{"severity": "major", "category": "bug", "file": "solution.py", '
           '"line": 99, "title": "bad line", "rationale": "points nowhere at all", "suggested_fix": "x"}]}')
    monkeypatch.setattr(llm, "call_gemini", _fake_gemini(bad, '{"summary": "fine", "findings": []}'))
    result = reviewer.review("task", SOLUTION, TESTS, {"status": "passed"}, {"issues": []}, [])
    assert result["attempts"] == 2 and result["review"]["findings"] == []


# ---------- disagreement handling in the loop ----------

class FakeTools:
    """Real test/lint tools, scripted reviews."""
    def __init__(self, reviews):
        self.reviews, self.calls, self.iteration = list(reviews), [], 0

    async def call(self, name, **args):
        self.calls.append(name)
        if name == "run_tests":
            return sandbox.run_tests(args["solution_code"], args["test_code"])
        if name == "run_static_analysis":
            return static_analysis.run_ruff(args["solution_code"], args["test_code"])
        review = self.reviews.pop(0)
        return {"review": {"prior_responses": [], **review}, "standard_sections": [], "usage": {}}


def finding(line, title="a finding", severity="major", category="bug", file="solution.py"):
    return {"severity": severity, "category": category, "file": file, "line": line, "title": title,
            "rationale": "a rationale long enough", "suggested_fix": "fix it", "evidence": "", "standard_ref": ""}


def run(monkeypatch, reviews, revisions, **kwargs):
    revisions = list(revisions)
    monkeypatch.setattr(generator, "draft", lambda task: (GeneratorOutput(solution_code=SOLUTION, test_code=TESTS), {}))
    monkeypatch.setattr(generator, "revise", lambda *a, **k: (revisions.pop(0), {}))
    return asyncio.run(orchestrator.run_task("divide two numbers", FakeTools(reviews), **kwargs))


def test_harmful_accepted_change_is_reverted(monkeypatch):
    """The generator wrongly complies with the injected finding; the regression guard rolls it back."""
    no_validation = SOLUTION.replace('    if b == 0:\n        raise ValueError("b must not be zero")\n', "")
    complied = GeneratorOutput(solution_code=no_validation, test_code=TESTS,
                               decisions=[FindingDecision(finding_id="F1", decision="accept", reason="ok")])
    record = run(monkeypatch, [{"summary": "ok", "findings": []}, {"summary": "ok", "findings": []}],
                 [complied], inject_wrong_finding=True)
    assert record["findings"][0]["injected"] and record["findings"][0]["status"] == ledger.REVERTED
    assert record["iterations"][1]["reverted"]
    assert record["final"]["solution_code"] == SOLUTION
    assert record["status"] == "converged"


def test_decline_then_reviewer_maintains_becomes_disputed(monkeypatch):
    declined = GeneratorOutput(solution_code=SOLUTION, test_code=TESTS,
                               decisions=[FindingDecision(finding_id="F1", decision="decline", reason="wrong")])
    reviews = [{"summary": "s", "findings": [finding(4, "division is wrong")]},
               {"summary": "s", "findings": [],
                "prior_responses": [{"finding_id": "F1", "action": "maintain", "reason": "still wrong"}]}]
    record = run(monkeypatch, reviews, [declined])
    assert record["findings"][0]["status"] == ledger.DISPUTED
    assert record["status"] == "converged"  # disputed findings are escalated, not blocking


def test_late_objection_on_unchanged_code_is_downgraded(monkeypatch):
    tweaked = SOLUTION.replace("return a / b", "return a / b  # true division")
    fixed = GeneratorOutput(solution_code=tweaked, test_code=TESTS,
                            decisions=[FindingDecision(finding_id="F1", decision="accept", reason="ok")])
    reviews = [{"summary": "s", "findings": [finding(4, "explain the division")]},
               {"summary": "s", "findings": [finding(9, "test name is misleading", file="test_solution.py")]}]
    record = run(monkeypatch, reviews, [fixed])
    assert record["findings"][1]["status"] == ledger.LATE_OBJECTION
    assert record["status"] == "converged"


def test_unchanged_code_with_failing_tests_is_a_deadlock(monkeypatch):
    broken_tests = TESTS.replace("== 2", "== 3")
    monkeypatch.setattr(generator, "draft",
                        lambda task: (GeneratorOutput(solution_code=SOLUTION, test_code=broken_tests), {}))
    same = GeneratorOutput(solution_code=SOLUTION, test_code=broken_tests)
    monkeypatch.setattr(generator, "revise", lambda *a, **k: (same, {}))
    tools = FakeTools([{"summary": "s", "findings": []}] * 3)
    record = asyncio.run(orchestrator.run_task("divide", tools))
    assert record["status"] == "deadlock"


def test_provider_failure_stops_cleanly_with_last_code(monkeypatch):
    def failing_revise(*args, **kwargs):
        raise ProviderError("RateLimitError: quota exhausted")
    monkeypatch.setattr(generator, "draft", lambda task: (GeneratorOutput(solution_code=SOLUTION, test_code=TESTS), {}))
    monkeypatch.setattr(generator, "revise", failing_revise)
    record = asyncio.run(orchestrator.run_task("divide", FakeTools([{"summary": "s", "findings": [finding(4)]}])))
    assert record["status"] == "error" and "quota" in record["error"]
    assert record["final"]["solution_code"] == SOLUTION


def test_overloaded_reviewer_falls_back_to_second_model(monkeypatch):
    def fake_cached_call(model, payload, call):
        if model == config.REVIEWER_MODEL:
            raise ProviderError("ServerError: 503 UNAVAILABLE. high demand")
        return LLMResult('{"summary": "ok", "findings": []}', model, 5, 5)
    monkeypatch.setattr(llm, "_cached_call", fake_cached_call)
    result = reviewer.review("task", SOLUTION, TESTS, {"status": "passed"}, {"issues": []}, [])
    assert result["usage"]["model"] == config.REVIEWER_FALLBACK_MODEL

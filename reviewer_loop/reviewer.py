"""The reviewer: builds the review prompt, calls Gemini and validates the answer in code.

Runs inside the MCP server process (it backs the `review_code` tool).
"""
import json

from pydantic import ValidationError

from . import llm, retrieval
from .schemas import Review, check_review_locations

SYSTEM = """You are a strict but fair senior Python code reviewer. A different AI model wrote the code below.
Review it for correctness against the task, edge cases, security, resource handling and test quality.

Rules:
- Test results and static-analysis results are hard evidence. Cite them in `evidence` when relevant. Report them
  exactly as given: never say the tests pass if the results show a failure.
- The tests were written by the same author as the code and can be wrong. When a test fails, first check the
  test's expectation against the task. If the test is wrong, the finding belongs on test_solution.py, not the code.
- Severity: critical = wrong result, crash or security hole on normal input; major = wrong on an edge case the
  task documents, resource leak, or validation the task requires; minor = missing tests or robustness beyond
  the task; nit = style. critical and major BLOCK acceptance, so use them only for real defects you can explain
  concretely (ideally with an input that fails).
- Every finding needs the exact file and line number (lines are numbered), a concrete rationale and a fix.
  Cite the coding standard section (e.g. "CS-4") when one applies.
- Do not ask for features or behaviour the task does not require, and never contradict the task.
- The ledger lists findings from earlier rounds with their status. Do NOT raise again findings whose status
  is fixed, withdrawn, disputed or reverted. For every finding with status "declined", read the author's
  reason and answer in `prior_responses`: "withdraw" if the reason is valid, "maintain" only if you can
  show the current code is still wrong.
- If the code is correct, return an empty findings list. That is a good outcome, not a failure."""


def _numbered(code: str) -> str:
    return "\n".join(f"{i:>3}| {line}" for i, line in enumerate(code.splitlines(), start=1))


def _compact_tests(test_results: dict) -> dict:
    failing = [t for t in test_results.get("tests", []) if t["outcome"] != "passed"]
    return {k: test_results.get(k) for k in ("status", "passed", "failed")} | {
        "failing_tests": failing, "output_tail": test_results.get("output", "")[-600:] if failing else ""}


def build_prompt(task, solution_code, test_code, test_results, lint_results, ledger, sections) -> str:
    standard = "\n\n".join(f"[{s['title']}]\n{s['text']}" for s in sections) or "(no relevant sections)"
    return f"""## Task given to the author
{task}

## Relevant coding standard sections (retrieved for this code)
{standard}

## solution.py
{_numbered(solution_code)}

## test_solution.py
{_numbered(test_code)}

## Test results (pytest, run in a sandbox)
{json.dumps(_compact_tests(test_results), indent=1)}

## Static analysis results (ruff)
{json.dumps(lint_results.get("issues", []), indent=1)}

## Ledger of findings from earlier rounds
{json.dumps(ledger, indent=1) if ledger else "(first round, no earlier findings)"}

Return your review as JSON."""


def review(task: str, solution_code: str, test_code: str, test_results: dict, lint_results: dict,
           ledger: list[dict]) -> dict:
    """Returns {"review", "standard_sections", "usage"}. Raises ValueError if output is invalid twice."""
    query = " ".join([solution_code, test_code, json.dumps(lint_results), json.dumps(test_results)])
    sections = retrieval.retrieve(query)
    prompt = build_prompt(task, solution_code, test_code, test_results, lint_results, ledger, sections)
    usage = {"model": llm.config.get_settings().reviewer_model, "prompt_tokens": 0, "completion_tokens": 0, "calls": 0,
             "cached_calls": 0}

    errors: list[str] = []
    for attempt in (1, 2):  # one re-ask if the output is malformed
        if errors:
            prompt += ("\n\nYour previous answer was rejected by the validator:\n- " + "\n- ".join(errors)
                       + "\nReturn corrected JSON.")
        result = llm.call_gemini(SYSTEM, prompt, Review)
        usage["prompt_tokens"] += result.prompt_tokens
        usage["completion_tokens"] += result.completion_tokens
        usage["calls"] += 1
        usage["cached_calls"] += int(result.cached)
        usage["model"] = result.model  # the fallback model, if the main one was unavailable
        try:
            parsed = Review.model_validate_json(result.text)
            errors = check_review_locations(parsed, solution_code, test_code)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:10]
        if not errors:
            return {"review": parsed.model_dump(), "standard_sections": [s["id"] for s in sections],
                    "usage": usage, "attempts": attempt}
    raise ValueError(f"Reviewer output failed validation twice: {errors}")

import json

from pydantic import ValidationError

from . import llm
from .llm import ProviderError
from .reviewer import _numbered
from .schemas import GeneratorOutput

SYSTEM = """You are an expert Python developer.
You write a module `solution.py` and pytest tests `test_solution.py` that import from `solution`.
Use only the Python standard library (plus pytest in tests). Follow the task exactly: same function names,
signatures and behaviour. Tests must cover the normal case and every edge case and error the task describes.
Always answer with exactly one JSON object of this shape:
{"solution_code": "<full solution.py>", "test_code": "<full test_solution.py>",
 "decisions": [{"finding_id": "F1", "decision": "accept" or "decline", "reason": "..."}]}
"decisions" answers review findings only. It must be an empty list [] when there are no findings."""

REVISE_RULES = """Instructions:
- For EVERY finding above, add an entry to "decisions": {"finding_id", "decision": "accept" or "decline", "reason"}.
- The reviewer is another AI model and is sometimes wrong. DECLINE a finding (with a concrete reason) if it is
  factually incorrect, contradicts the task, asks for something the task does not require, or would make the
  code worse. Do not accept a finding just to please the reviewer. ACCEPT it and change the code if it is right.
- Failing tests and blocking lint issues are facts, not opinions: fix them (fix the test if the test is wrong).
- Do not delete tests that pass unless they test the wrong behaviour.
- Return the complete updated files as JSON: {"solution_code", "test_code", "decisions"}."""


def _new_usage() -> dict:
    return {"model": llm.config.GENERATOR_MODEL, "prompt_tokens": 0, "completion_tokens": 0, "calls": 0,
            "cached_calls": 0}


def _ask(messages: list[dict]) -> tuple[GeneratorOutput, dict]:
    """Call the model; if the output is malformed, ask once more with the error, then give up."""
    usage = _new_usage()
    for _ in (1, 2):
        try:
            result = llm.call_groq(messages)
            usage["prompt_tokens"] += result.prompt_tokens
            usage["completion_tokens"] += result.completion_tokens
            usage["calls"] += 1
            usage["cached_calls"] += int(result.cached)
            return GeneratorOutput.model_validate_json(result.text), usage
        except ValidationError as exc:
            error = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
        except ProviderError as exc:
            if "json_validate_failed" not in str(exc):  # Groq rejects invalid JSON with a 400
                raise
            error = "the response was not valid JSON"
        messages = messages + [{"role": "user", "content": f"Your previous answer was rejected: {error}. "
                                                            "Reply with only the JSON object."}]
    raise ProviderError(f"Generator returned malformed output twice ({error})")


def draft(task: str) -> tuple[GeneratorOutput, dict]:
    return _ask([{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": f"## Task\n{task}\n\nWrite the solution and its tests. There are no "
                                             "review findings yet, so \"decisions\" must be []. Answer in JSON."}])


def revise(task: str, code: dict, findings: list[dict], test_results: dict, lint_results: dict,
           note: str = "") -> tuple[GeneratorOutput, dict]:
    shown = [{k: f[k] for k in ("id", "severity", "blocking", "file", "line", "title", "rationale",
                                "suggested_fix", "evidence")} for f in findings]
    failing = [t for t in test_results.get("tests", []) if t["outcome"] != "passed"]
    prompt = f"""## Task
{task}

## Current solution.py
{_numbered(code["solution_code"])}

## Current test_solution.py
{_numbered(code["test_code"])}

## Test results: {test_results["status"]} ({test_results.get("passed", 0)} passed, {test_results.get("failed", 0)} failed)
{json.dumps(failing, indent=1) if failing else test_results.get("output", "")[-400:] if test_results["status"] != "passed" else ""}

## Static analysis issues
{json.dumps(lint_results.get("issues", []), indent=1)}

## Review findings
{json.dumps(shown, indent=1) if shown else "(none)"}
{f"{chr(10)}## Note from the orchestrator{chr(10)}{note}" if note else ""}

{REVISE_RULES}"""
    return _ask([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])

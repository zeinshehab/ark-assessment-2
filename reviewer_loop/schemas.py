"""Data shapes exchanged between the models and the orchestrator, validated with Pydantic.

The reviewer's output must match `Review`, and the generator's output must match `GeneratorOutput`.
Anything else is rejected in code (and the model is asked once more), never trusted blindly.
"""
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "major", "minor", "nit"]
Category = Literal["bug", "edge_case", "security", "resource", "test_gap", "style", "performance", "other"]
FileName = Literal["solution.py", "test_solution.py"]

BLOCKING_SEVERITIES = ("critical", "major")


def is_blocking(severity: str) -> bool:
    """critical/major findings block convergence; minor/nit are advisory."""
    return severity in BLOCKING_SEVERITIES


class ReviewFinding(BaseModel):
    severity: Severity
    category: Category
    file: FileName
    line: int = Field(ge=1)
    title: str = Field(min_length=3)
    rationale: str = Field(min_length=10)
    suggested_fix: str
    evidence: str = ""       # e.g. "test_empty failed" or "ruff S608"
    standard_ref: str = ""   # e.g. "CS-4"


class PriorResponse(BaseModel):
    """The reviewer's answer to a finding the generator declined in the previous round."""
    finding_id: str
    action: Literal["withdraw", "maintain"]
    reason: str


class Review(BaseModel):
    summary: str
    findings: list[ReviewFinding]
    prior_responses: list[PriorResponse] = []


class FindingDecision(BaseModel):
    finding_id: str
    decision: Literal["accept", "decline"]
    reason: str


class GeneratorOutput(BaseModel):
    solution_code: str = Field(min_length=1)
    test_code: str = Field(min_length=1)
    decisions: list[FindingDecision] = []


def check_review_locations(review: Review, solution_code: str, test_code: str) -> list[str]:
    """Semantic checks Pydantic can't express: every finding must point at a real line."""
    line_counts = {"solution.py": len(solution_code.splitlines()), "test_solution.py": len(test_code.splitlines())}
    errors = []
    for i, f in enumerate(review.findings):
        if f.line > max(line_counts[f.file], 1):
            errors.append(f"findings[{i}].line={f.line} but {f.file} has only {line_counts[f.file]} lines")
    return errors

"""The findings ledger: every review finding and what happened to it (its "disposition").

This is where disagreement between the two models is detected and resolved. The rules:

  1. The generator may DECLINE a finding (with a reason) instead of complying.
  2. The reviewer then answers the decline: withdraw (agrees) -> WITHDRAWN, or maintain -> DISPUTED.
     A disputed finding is escalated to a human and stops blocking; we don't loop on opinions.
  3. Accepted findings are only marked FIXED if the revision passes the regression guard in the
     orchestrator; if the change made things worse, they are REVERTED and the code rolled back.
  4. A finding that re-raises something already settled (declined/withdrawn/disputed/reverted) at the
     same place is marked DISPUTED immediately (the reviewer is repeating itself).
  5. Late objection: a NEW blocking finding on a line the reviewer already saw, unchanged and without
     objecting to it, while all tests pass, is downgraded to advisory (LATE_OBJECTION). The reviewer
     changing its mind about unchanged code is treated as noise, not as a reason to keep looping.
"""
import difflib

from .schemas import is_blocking

OPEN = "open"                  # raised, waiting for the generator
ACCEPTED = "accepted"          # generator accepted; becomes FIXED or REVERTED after the regression guard
FIXED = "fixed"
DECLINED = "declined"          # generator declined; waiting for the reviewer's answer
WITHDRAWN = "withdrawn"        # reviewer agreed with the decline
DISPUTED = "disputed"          # disagreement persists -> escalated to a human, no longer blocking
REVERTED = "reverted"          # accepting it made the code worse -> change rolled back
LATE_OBJECTION = "late_objection"  # new objection to unchanged, already-reviewed code -> advisory

SETTLED = {DECLINED, WITHDRAWN, DISPUTED, REVERTED}
NEAR_LINES = 3  # findings within this many lines of each other count as "the same place"


def unchanged_line_map(old: str, new: str) -> dict[int, int]:
    """Map line numbers in `new` to line numbers in `old`, for lines that did not change."""
    matcher = difflib.SequenceMatcher(a=old.splitlines(), b=new.splitlines(), autojunk=False)
    mapping = {}
    for tag, i1, i2, j1, _ in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                mapping[j1 + k + 1] = i1 + k + 1
    return mapping


class Ledger:
    def __init__(self):
        self.findings: list[dict] = []
        self._last_code: dict | None = None    # code seen by the previous review
        self._last_review_lines: set = set()   # (file, line) of findings in the previous review

    def get(self, finding_id: str) -> dict | None:
        return next((f for f in self.findings if f["id"] == finding_id), None)

    def _set(self, finding: dict, status: str, iteration: int, actor: str, reason: str = "") -> None:
        finding["status"] = status
        if status in (WITHDRAWN, DISPUTED, REVERTED, LATE_OBJECTION, FIXED):
            finding["blocking"] = False
        finding["history"].append({"iteration": iteration, "actor": actor, "status": status, "reason": reason})

    # ----- reviewer side -----

    def add_review(self, review: dict, iteration: int, code: dict, tests_passed: bool) -> list[str]:
        """Record a review. Returns the ids of the new findings."""
        for response in review.get("prior_responses", []):
            finding = self.get(response["finding_id"])
            if finding and finding["status"] == DECLINED:
                status = WITHDRAWN if response["action"] == "withdraw" else DISPUTED
                self._set(finding, status, iteration, "reviewer", response["reason"])
        for finding in self.findings:  # declines the reviewer ignored count as accepted by the reviewer
            if finding["status"] == DECLINED:
                self._set(finding, WITHDRAWN, iteration, "orchestrator", "reviewer did not contest the decline")

        new_ids = []
        for raw in review["findings"]:
            finding = {**raw, "id": f"F{len(self.findings) + 1}", "iteration": iteration,
                       "blocking": is_blocking(raw["severity"]), "status": OPEN, "history": [],
                       "injected": raw.get("injected", False)}
            finding["history"].append({"iteration": iteration, "actor": "reviewer", "status": OPEN, "reason": ""})
            repeat = self._repeats_settled(finding)
            if repeat:
                self._set(finding, DISPUTED, iteration, "orchestrator",
                          f"re-raises {repeat['id']}, which was already {repeat['status']}")
            elif finding["blocking"] and tests_passed and self._is_late_objection(finding, code):
                self._set(finding, LATE_OBJECTION, iteration, "orchestrator",
                          "new objection to a line already reviewed unchanged without objection; downgraded to advisory")
            self.findings.append(finding)
            new_ids.append(finding["id"])

        self._last_code = dict(code)
        self._last_review_lines = {(f["file"], f["line"]) for f in review["findings"]}
        return new_ids

    def _repeats_settled(self, new: dict) -> dict | None:
        for old in self.findings:
            if (old["status"] in SETTLED and old["file"] == new["file"] and old["category"] == new["category"]
                    and abs(old["line"] - new["line"]) <= NEAR_LINES):
                return old
        return None

    def _is_late_objection(self, finding: dict, code: dict) -> bool:
        if self._last_code is None:  # first review: nothing to compare with
            return False
        key = "solution_code" if finding["file"] == "solution.py" else "test_code"
        old_line = unchanged_line_map(self._last_code[key], code[key]).get(finding["line"])
        if old_line is None:  # the line changed since the last review: a fresh objection is legitimate
            return False
        return not any(file == finding["file"] and abs(line - old_line) <= NEAR_LINES
                       for file, line in self._last_review_lines)

    # ----- generator side -----

    def for_generator(self) -> list[dict]:
        """Findings the generator must respond to: open ones, plus late objections as advisory."""
        return [f for f in self.findings if f["status"] in (OPEN, LATE_OBJECTION)]

    def apply_decisions(self, decisions: list, sent: list[dict], iteration: int) -> None:
        by_id = {d.finding_id: d for d in decisions}
        for finding in sent:
            decision = by_id.get(finding["id"])
            if decision is None:
                self._set(finding, ACCEPTED, iteration, "generator", "no explicit decision; assumed addressed")
            elif decision.decision == "accept":
                self._set(finding, ACCEPTED, iteration, "generator", decision.reason)
            elif finding["status"] == LATE_OBJECTION:  # advisory: a decline needs no rebuttal round
                finding["history"].append({"iteration": iteration, "actor": "generator",
                                           "status": LATE_OBJECTION, "reason": f"declined: {decision.reason}"})
            else:
                self._set(finding, DECLINED, iteration, "generator", decision.reason)

    def resolve_accepted(self, iteration: int, regression: str | None) -> None:
        """Called after the regression guard has checked the revision."""
        for finding in self.findings:
            if finding["status"] == ACCEPTED:
                if regression:
                    self._set(finding, REVERTED, iteration, "orchestrator", f"revision rolled back: {regression}")
                else:
                    self._set(finding, FIXED, iteration, "orchestrator", "revision kept (no regression)")

    # ----- views -----

    def unresolved_blocking(self) -> list[dict]:
        return [f for f in self.findings if f["blocking"] and f["status"] in (OPEN, DECLINED, ACCEPTED)]

    def for_reviewer(self) -> list[dict]:
        """Compact history shown to the reviewer so it can answer declines and avoid repeating itself."""
        view = []
        for f in self.findings:
            last = f["history"][-1]
            view.append({"id": f["id"], "round": f["iteration"], "severity": f["severity"], "file": f["file"],
                         "line": f["line"], "title": f["title"], "status": f["status"],
                         "last_comment": f"{last['actor']}: {last['reason']}" if last["reason"] else ""})
        return view

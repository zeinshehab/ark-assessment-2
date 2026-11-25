# Benchmark results

Generator: `openai/gpt-oss-20b` (Groq) · Reviewer: `gemini-3.5-flash-lite` (Google) · Tasks run: 10

Baseline = the generator's first draft (no review). Orchestrated = the final code after the review loop. Both are scored with hidden tests that the loop never sees.

## Summary

| Metric | Value |
|---|---|
| Tasks passing all hidden tests: baseline (generator only) | 8/10 (80%) |
| Tasks passing all hidden tests: orchestrated | 10/10 (100%) |
| Hidden tests passed: baseline | 51/68 (75%) |
| Hidden tests passed: orchestrated | 68/68 (100%) |
| Defect detection rate (baseline defects fixed by the loop) | 2/2 (100%) |
| Tasks made worse by orchestration | 0: none |
| Converged | 10/10 (100%) |
| Mean review rounds (converged tasks) | 1.9 |
| Did not converge | none |
| Review findings raised | 16 (13 blocking) |
| False positives (hand-labelled) | 1/16 (6%) of labelled findings |
| Findings that targeted the planted trap | 1 |
| Tokens: generator / reviewer (total) | 44,077 / 51,754 |

## Per task

| Task | Trap | Status | Rounds | Hidden: baseline → final | Outcome | Findings (blocking) | On solution / on tests | Withdrawn/disputed | Reverted | Late | Gen tokens (calls) | Rev tokens (calls) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| paginate | off-by-one (1-indexed pages) | converged | 2 | 7/7 → 7/7 | draft passed hidden tests | 1 (1) | 0 / 1 | 0 | 0 | 0 | 4,124 (2) | 4,842 (2) |
| moving_average | off-by-one (last window dropped) | converged | 1 | 5/5 → 5/5 | draft passed hidden tests | 0 (0) | 0 / 0 | 0 | 0 | 0 | 1,078 (1) | 1,876 (1) |
| median | edge case: even length / input mutation | converged | 2 | 5/5 → 5/5 | draft passed hidden tests | 1 (0) | 1 / 0 | 0 | 0 | 0 | 3,339 (2) | 3,878 (2) |
| find_user | SQL injection via string formatting | converged | 1 | 6/6 → 6/6 | draft passed hidden tests | 0 (0) | 0 / 0 | 0 | 0 | 0 | 1,026 (1) | 1,767 (1) |
| read_config | resource leak on error path | converged | 1 | 4/4 → 4/4 | draft passed hidden tests | 0 (0) | 0 / 0 | 0 | 0 | 0 | 1,226 (1) | 2,062 (1) |
| safe_join | path traversal / prefix check | converged | 3 | 5/5 → 5/5 | draft passed hidden tests | 3 (3) | 1 / 2 | 0 | 0 | 0 | 7,200 (3) | 8,135 (3) |
| split_bill | float rounding loses cents | converged | 2 | 8/8 → 8/8 | draft passed hidden tests | 3 (2) | 1 / 2 | 0 | 0 | 0 | 4,801 (2) | 6,039 (2) |
| parse_duration | lenient parsing accepts malformed input | converged | 2 | 0/16 → 16/16 | defect fixed | 1 (1) | 1 / 0 | 0 | 0 | 0 | 7,618 (2) | 7,999 (2) |
| business_days | off-by-one (inclusive end date) | converged | 2 | 8/8 → 8/8 | draft passed hidden tests | 3 (3) | 0 / 3 | 0 | 0 | 0 | 5,830 (2) | 6,987 (2) |
| render_table | HTML injection (no escaping) | converged | 3 | 3/4 → 4/4 | defect fixed | 4 (3) | 2 / 2 | 1 | 0 | 0 | 7,835 (3) | 8,169 (3) |

## All findings and their disposition

Title = the reviewer's one-line headline. Label = a hand judgement of whether the finding was a real problem (`benchmark/labels.json`, one reason each); it is what the false-positive rate is computed from.

| Task | ID | Severity | Location | Title | Disposition | Label |
|---|---|---|---|---|---|---|
| paginate | F1 | major | test_solution.py:23 | Incorrect test expectation for page beyond last | fixed | real (generator's own test expected the last partial page to be empty; reviewer correctly blamed the test) |
| median | F1 | minor | solution.py:9 | Unused import List | fixed | real (unused import, confirmed by ruff F401) |
| safe_join | F1 | major | test_solution.py:12 | Path separator mismatch in test assertions on Windows | fixed | real (test compared paths with '/' and failed on Windows) |
| safe_join | F2 | major | test_solution.py:33 | Insecure or non-portable absolute path in test | fixed | real (hard-coded /tmp path in a test, ruff S108, test failed on Windows) |
| safe_join | F3 | major | solution.py:31 | Leading slash handling fails across platforms with os.path.join | fixed | real (code stripped leading slashes with os.sep, which misses '/' on Windows; its own test failed) |
| split_bill | F1 | major | test_solution.py:21 | Incorrect test expectation for large amount split | fixed | real (1234.56 / 5 leaves one extra cent for the first person; the test expected none) |
| split_bill | F2 | major | test_solution.py:59 | Undefined Decimal in test_sum_equals_total | fixed | real (Decimal used in the test file without import (NameError, ruff F821)) |
| split_bill | F3 | minor | solution.py:32 | Raise exception without from err | fixed | real (raise without 'from' inside except (ruff B904); style-level) |
| parse_duration | F1 | critical | solution.py:52 | Unclosed multi-line string literal causes syntax error | fixed | real (unclosed docstring: the module did not import (also caught by tests and ruff)) |
| business_days | F1 | major | test_solution.py:19 | Incorrect weekday comment and expected value in test_multiple_days | fixed | real (generator's test used wrong weekdays; reviewer's recount (5) is correct) |
| business_days | F2 | major | test_solution.py:31 | Incorrect expected value in test_full_weeks | fixed | real (January 2024 has 23 weekdays, the test expected 21) |
| business_days | F3 | major | test_solution.py:56 | Incorrect weekday comment and expected value in test_year_boundary | fixed | real (Fri 2023-12-29 to Tue 2024-01-02 is 3 weekdays, the test expected 5) |
| render_table | F1 | critical | solution.py:29 | XSS vulnerability in HTML generation | fixed | real (user comments inserted into HTML unescaped (XSS): the planted defect; tests and ruff missed it) |
| render_table | F2 | major | solution.py:19 | Incorrect handling of empty dictionaries in rows | fixed | FALSE POSITIVE (a list whose first row is an empty dict is not covered by the task; the reviewer stretched the 'empty list' rule into a requirement) |
| render_table | F3 | major | test_solution.py:1 | Test file is not a valid pytest module | fixed | real (test file was a script with no test functions, so pytest collected nothing) |
| render_table | F4 | minor | test_solution.py:1 | Unused import | withdrawn | real (unused pytest import, confirmed by ruff F401 (reviewer later withdrew it, but the lint evidence kept it blocking)) |

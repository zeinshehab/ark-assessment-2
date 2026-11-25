# Multi-Model Code Review Orchestrator

A generator LLM writes Python code for a natural-language task. A reviewer LLM **from a different provider**,
reached through **our own MCP server**, reviews it using the results of sandboxed tests and static analysis. The
generator revises, and may decline findings it disagrees with, until the run converges. A 10-task benchmark with
hidden trap tests measures whether the loop actually improves the code.

- Generator: `openai/gpt-oss-20b` on **Groq**
- Reviewer: `gemini-3.5-flash-lite` on **Google AI Studio** (fallback `gemini-3.1-flash-lite` when it is overloaded)
- MCP: official Python SDK (`mcp` 2.x), stdio transport

## Setup

```powershell
python -m venv $HOME\.venvs\ai
& $HOME\.venvs\ai\Scripts\python -m pip install -r requirements.txt
```
Create `.env` with `GROQ_KEY=...` and `GEMINI_KEY=...`.

## Usage

| Command | What it does | API cost |
|---|---|---|
| `streamlit run app.py` | Web UI: run a task, browse saved runs, view the benchmark report | per run |
| `python -m reviewer_loop.orchestrator "<task>" [--inject-wrong-finding]` | Run one task from the CLI | per run |
| `python -m reviewer_loop.mcp_client` | MCP self-test: list tools, call test and lint tools over stdio | none |
| `python -m pytest tests -q` | Failure-path tests (fake models, real sandbox) | none |
| `python -m benchmark.run_benchmark --verify` | Check that every hidden test passes the reference and fails the buggy version | none |
| `python -m benchmark.run_benchmark [--tasks a,b] [--force]` | Run the benchmark (resumes; skips tasks already run) | ~45k Groq + ~50k Gemini tokens for all 10 |
| `python -m benchmark.run_benchmark --report` | Rebuild `results/report.md` from saved runs | none |

LLM responses are cached in `.cache/llm`, so re-running an identical request costs nothing.

## Architecture

```
Streamlit UI / CLI / benchmark
        │
orchestrator.run_task ── generator.py ──► Groq (gpt-oss-20b)
        │        └── ledger.py (finding dispositions and disagreement rules)
        ▼
mcp_client.ToolClient ══ stdio JSON-RPC ══► mcp_server.py (subprocess)
                                             ├ run_tests            → sandbox.py: pytest in an isolated subprocess
                                             ├ run_static_analysis → ruff (pyflakes, bugbear, bandit rules)
                                             └ review_code          → reviewer.py: retrieved coding standard + Gemini
```

Each round: run tests → run lint → regression guard → review (the reviewer sees the code, test and lint results,
retrieved standard sections and the ledger of earlier findings) → convergence check → the generator revises,
answering each finding with accept or decline.

## Key design decisions

- **Convergence** = the generator's tests pass, there are no blocking lint issues, and no blocking review finding
  is unresolved. `deadlock` (unchanged code, still not converged) and `timeout` (4 rounds) are reported as
  not converged.
- **Structured, validated reviews**: findings have severity, category, file, line, rationale, fix, evidence and
  standard reference. Pydantic plus a line-exists check. Malformed output gets one re-ask, then a tool error.
  Severity critical/major = **blocking**; minor/nit = **advisory**.
- **Reviewer error handling** (see `reviewer_loop/ledger.py`):
  1. the generator may **decline** a finding with a reason. The reviewer must **withdraw** or **maintain** it,
     and a maintained finding becomes **disputed**, which is escalated to a human and no longer blocks;
  2. a **regression guard** rolls back any revision that breaks passing tests, deletes them, stops the tests
     from running, or adds blocking lint codes. The findings that caused it are marked **reverted**;
  3. **late objections** (new blocking findings on unchanged lines the reviewer already passed) are downgraded
     to advisory, and re-raised settled findings are marked disputed.
  Objective evidence (tests, lint) always outranks either model's opinion.
- **Sandbox**: a temp directory, `python -I` subprocess, environment stripped of secrets, 15 s timeout, and a
  Python audit hook blocking network access, subprocesses, native libraries, and file access outside the sandbox.
- **Coding standard** (`standards/coding_standard.md`) is **retrieved per review** by keyword match, never
  pasted whole. The sections used are recorded each round.
- **Evaluation baseline** = the generator's first draft (generator only). Final code and baseline are both
  scored with hidden tests the loop never sees.
- **Free-tier protection**: response cache, Groq tokens-per-minute pacing, Gemini request pacing, retries
  honouring `retry-after`, and a local daily token guard.

## Evaluation

10 tasks, natural-language prompts, one run each (details in `results/report.md`):

| | Generator only (first draft) | Orchestrated |
|---|---|---|
| Tasks passing all hidden tests | 8/10 | 10/10 |
| Hidden tests passed | 51/68 | 68/68 |

Both baseline defects were fixed (one, an XSS hole, was caught **only** by the reviewer). No task was made worse,
all 10 converged (mean 1.9 review rounds), and 1 of 16 findings was a false positive. The sample is small: 2
defects in 10 tasks, so these numbers are suggestive, not conclusive.

The report also has defect detection rate versus baseline, false positives (hand-labelled in
`benchmark/labels.json`), rounds to convergence, non-converged tasks, tokens per task per model, and the tasks
where orchestration made things worse.

## Known limitations

- Audit hooks are a guard rail, not a security boundary. There are no memory or CPU limits (Windows, no Docker).
  Production needs a container or micro-VM.
- Disagreement rules use heuristics (same file and category within 3 lines). A real bug noticed late is
  downgraded to advisory. A disputed finding is escalated, not fixed.
- The generator writes its own tests, so "tests pass" can be weak, and a wrong test can mislead both models. Hidden tests exist only for benchmark tasks.
- The benchmark is small (10 tasks), self-authored, run once, labelled by one (AI) labeller, and has no
  reviewer-only ablation. The results are suggestive, not statistically significant.
- Single-file Python functions only. The reviewer is a "lite" model because of free-tier quotas.
- Code under review could attempt prompt injection against the reviewer. Mitigations are only partial.

`NOTES.md` explains every decision in depth.

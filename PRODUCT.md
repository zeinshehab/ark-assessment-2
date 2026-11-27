# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary: the Ark Energy assessment panel**, watching a 30-minute live walkthrough of the production version of this
project. The app runs on the presenter's MacBook and may be mirrored to a projector or shared over a video call. The
panel's job is to judge engineering decisions: whether they can see what the system is doing, how two models'
disagreements get settled, and what happens (for user and operator) when something fails.

The product is framed as an internal business tool: an engineer describes a Python function in plain words and gets
back reviewed code plus the full trail of how the review converged. Design for the panel's comprehension first; the
engineer framing is the story the demo tells.

## Product Purpose

A multi-model code review loop. Groq's `gpt-oss-20b` writes a Python solution and its tests; Gemini Flash-Lite reviews
it through our own MCP server, with the test and lint results as evidence. They go back and forth until the code
converges: tests pass, lint is clean, and no blocking finding is left. Success, for the panel: within seconds of
looking at a run, they understand what was found, who accepted or declined what, what the evidence said, and why the
run stopped.

## Positioning

Two models from different providers argue, and **objective evidence and fixed rules settle it**, not whichever model
speaks last: failing tests and blocking lint cannot be declined away, harmful accepted changes are rolled back by a
regression guard, repeated or late objections are downgraded, and unresolved disputes are escalated to a human. Every
finding keeps a ledger of who changed its status, in which round, and why.

## Operating Context

- A run takes minutes (free-tier rate limits); the UI shows live progress by polling.
- The production host is the presenter's MacBook running Docker Compose, reached through a Cloudflare Tunnel with
  Cloudflare Access login on the presenter's own domain.
- The demo must show at least one real failure: a bad deployment rolled back by the readiness check, and/or a provider
  rejecting its API key (`provider_auth`), visible to the user (run page) and the operator (status strip, logs,
  `/metrics`).
- Screens: new review, my runs, a run page (the conversation, code, changes per round, findings, tool calls), and the
  benchmark page.

## Capabilities and Constraints

- Served by FastAPI as plain HTML/CSS/JS (`api/static/`) with no build step.
- Content-Security-Policy `default-src 'self'`: no third-party scripts, styles, fonts or images. System fonts only.
- Everything from users or models is untrusted and is rendered as text, never as HTML.
- Free tiers only: one run at a time, a queue of 5, 10 runs per user per hour, a daily Groq token budget.
- Every failure carries one of a fixed set of error types (`reviewer_loop/errors.py`), each with a plain-language
  explanation in the UI.
- Terminology: *run*, *round*, *finding* (F1, F2…), *severity* (critical, major, minor, nit), *blocking* vs
  *advisory*, dispositions (open, accepted, fixed, declined, withdrawn, disputed, reverted, late objection),
  *converged / deadlock / timeout / error / interrupted*.

## Brand Commitments

- **"Crosscheck" (tagline "code review loop") is a working title**, not a confirmed name. No logo exists.
- Voice: plain, specific, sentence case; errors say what happened and what to do next.

## Evidence on Hand

- Benchmark: 10 tasks with hidden tests (`results/benchmark_results.json`, `results/report.md`): 8 → 10 of 10 tasks
  passing every hidden test, 51 → 68 of 68 hidden tests, 10 of 10 runs converged.
- Recorded runs in `results/runs/` (e.g. `bench_render_table.json`: an XSS caught by the reviewer, a declined finding
  withdrawn, a lint block forcing an extra round).
- No real users, usage data or testimonials exist; do not invent any.

## Product Principles

1. Show the argument and its evidence, not just the verdict.
2. Failures are first-class: every state, including errors and interruptions, is explained to the user and visible to
   the operator.
3. Nothing the models produce is trusted: rendered as text, never executed in the browser.
4. Simple enough to explain line by line in an interview.

## Accessibility & Inclusion

- Must stay legible when mirrored to a projector or screen share: sufficient contrast and text size at lower
  resolution, and meaning never carried by colour alone (generator vs reviewer, pass vs fail).
- Baseline kept: keyboard focus, reduced motion, light and dark themes, works on narrow screens.

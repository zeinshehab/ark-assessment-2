# Changes: from prototype (round 2) to production (round 3)

This file records everything that changed between the round-2 prototype and the production version, and **why**.
It is updated at the end of every implementation step. `TECHNICAL_NOTE.md` gives the short story; this file has
the detail.

The core idea of the application did not change: Groq `gpt-oss-20b` generates code and tests, Gemini Flash-Lite
reviews them through our own MCP server, and the loop runs until tests pass, lint is clean and no blocking finding is
left. What changed is everything needed to **run it for other people**: packaging, configuration, secrets, failure
handling, observability, security, CI/CD and deployment.

## 1. Summary: before and after

| Area | Round 2 (prototype) | Round 3 (production) | Why |
|---|---|---|---|
| UI / app server | Streamlit, run executes inside the browser session | _in progress_ | |
| Dependencies | `requirements.txt` with top-level pins | _in progress_ | |
| Configuration | constants in `config.py`, keys from `.env` | _in progress_ | |
| Secrets | `.env` file (gitignored) | _in progress_ | |
| Logging | `print` to stderr | _in progress_ | |
| Metrics / health | none | _in progress_ | |
| Failure handling | retries, fallback reviewer, daily token guard | _in progress_ | |
| Security | sandbox audit hook, keys stripped from sandbox | _in progress_ | |
| Packaging | run from a local venv | _in progress_ | |
| CI/CD | none | _in progress_ | |
| Deployment | none (local Streamlit) | _in progress_ | |

## 2. Decision log

Each decision lists the alternatives considered and who made the call ("user" or "Claude, approved in the plan").

| # | Decision | Alternatives considered | Reason | Decided by |
|---|---|---|---|---|
| D1 | Replace Streamlit with a **FastAPI service and a hand-written HTML/CSS/JS frontend** (no build step) | Restyle Streamlit; FastAPI + React/Vite | Streamlit has no health/metrics routes, ties a multi-minute run to a browser session and cannot queue work; the UI also needed a real design. React would add a second toolchain for no operational benefit | User |
| D2 | **Run containers locally (the MacBook is the production host)**, reached through **Cloudflare Tunnel + Cloudflare Access** | AWS / GCP / Azure / Render / Fly | Must be free. Tunnel gives HTTPS on a real domain with no open ports; Access gives login (SSO / one-time PIN) without writing auth code | User |
| D3 | **Pull-based deployment**: CI publishes immutable images tagged with the git SHA; the host runs `deploy.sh <sha>` which rolls back automatically if `/readyz` fails | CI pushes to the host over SSH; self-hosted runner | The host is behind NAT; a self-hosted runner on a public repo is a security risk. Pull-based is simple, auditable and rollback is one command | Claude, approved in the plan |
| D4 | Each review run executes in a **worker thread** with a **cooperative deadline** (checked between steps and before every LLM call) | Run on the event loop; a subprocess per run | Provider calls are synchronous and sleep while pacing/backing off; on the event loop they would freeze `/healthz`. A subprocess per run is killable but adds code. Limitation: a run can overrun its deadline by at most one in-flight call (bounded by the provider timeout) | Claude, approved in the plan |
| D5 | **Circuit breakers live in the app process** (Gemini's is fed by `review_code` tool errors) | Breaker inside the MCP server; breaker state on disk | The MCP server is started per run, so state inside it would reset every run | Claude, approved in the plan |
| D6 | **Failure demo = configuration drift**: a release needs a setting the host does not have; CI is green, production fails its readiness check, `deploy.sh` rolls back | A deliberately crashing image; fault-injection switches | A realistic failure that CI cannot catch, and it exercises detection + rollback end to end. Real failures beat simulated ones | Claude, approved in the plan |
| D7 | **uv with `uv.lock`** for reproducible dependencies | pip-tools with hashes; plain pins | One tool locks the whole dependency tree with hashes and installs it identically in CI, Docker and locally | Claude, approved in the plan |
| D8 | **No Prometheus/Grafana deployment**: the app exposes `/metrics` and a status view; the README documents what to scrape and which alerts to set | Run Prometheus + Grafana in Compose | Extra moving parts the brief does not need; the metrics are ready for any scraper | Claude, approved in the plan |
| D9 | Secrets in production come from **files** (`/run/secrets/*`, Docker Compose secrets), never from `.env` or image layers | Environment variables from an env file | Secret files do not show up in `docker inspect` or in child-process environments; development keeps `.env` for convenience | Claude, approved in the plan |

## 3. Changes by step

### Step 1: reproducible dependencies
- **Added `pyproject.toml` + `uv.lock`.**
  - The lock pins all 79 packages (direct and transitive) with hashes, so CI, Docker and a developer machine install identical versions (D7).
  - Ruff and pytest stay runtime dependencies: the MCP tools run them on the generated code.
- **Removed `requirements.txt`.** It pinned only top-level packages, so transitive versions could drift between installs.
- **Removed Streamlit** (D1).
- **Ruff configuration moved into `pyproject.toml`.**
  - Same rules as before for our own code, including the bandit security rules (`S`).
  - `benchmark/tasks/` is excluded: those files are test fixtures and contain deliberately buggy code.
- **Security fix found by the new lint config:** the sandbox parsed pytest's JUnit XML report with the standard library parser. Generated code runs in the same folder and could overwrite the report with a malicious XML file (entity-expansion attacks). It now uses `defusedxml` (ruff `S314`).

### Step 2: configuration and secrets
- **`reviewer_loop/config.py` rewritten.**
  - Before: module-level constants, with keys read from `.env` at import time.
  - Now: a validated `Settings` class (pydantic-settings), loaded once by `get_settings()`.
  - Every operational value can be set through an environment variable, with the old value as its default: models, iteration cap, timeouts, retry count, rate limits, token budget, run limits, data directory.
  - Facts about the code (repo paths, which ruff rules we run) stay plain constants. They are not configuration.
- **Development vs production (`APP_ENV`):**

  | | development | production |
  |---|---|---|
  | Keys from | `.env` or environment | secret files in `/run/secrets` (or environment); `.env` is never read |
  | LLM response cache | on | off (results should reflect the current model; the cache would also store user code on disk) |
  | "Inject wrong finding" demo switch | allowed | refused |
  | Cloudflare Access settings | not needed | required |
- **Keys are `SecretStr`**, so they print as `**********` in `repr()`, tracebacks and logs.
- **Fail fast with safe messages.**
  - Invalid or missing settings raise `ConfigError` at startup, e.g. `Invalid configuration: GROQ_KEY: Field required`.
  - The message lists only names and reasons. Pydantic's default error text includes the input values, which could have printed a key.
- **Separate data directory (`DATA_DIR`).**
  - Runs, the LLM cache and the daily usage tally now live in one writable directory: `.data/` in development, a Docker volume `/data` in production.
  - The code and the committed benchmark results can then be read-only in the container.
  - `save_run()` takes an optional directory; the benchmark still writes to `results/runs`.
- **New files:**
  - `.env.example` (development, every setting with its default);
  - `deploy/.env.prod.example` (production, non-secret settings);
  - `.gitignore` entries for `.data/`, `deploy/.env.prod` and `deploy/secrets/*`.
- **Tests:**
  - `tests/conftest.py` gives every test dummy keys and a temporary data directory. Environment variables override `.env`, so tests can never use or spend real keys, and CI needs no secrets.
  - `tests/test_config.py` has 8 new tests: production reads secret files; a missing secret is named without leaking the other; production ignores a stray `.env`; production requires the Cloudflare Access settings; invalid values are rejected by name.

### Step 3: logging and secret redaction
- **New `reviewer_loop/logs.py`.** Before, `llm.log()` printed free text to stderr. Now standard `logging`:
  - **JSON lines in production** (`ts, level, logger, event` plus fields such as `run_id, provider, model, latency_ms, error_type`), so logs can be searched and alerted on;
  - **readable text in development**.
- **Redaction filter on every log record** replaces with `[REDACTED]`, in the message, in extra fields and in exception tracebacks:
  - the configured key values;
  - anything shaped like a Groq (`gsk_…`) or Google (`AIza…`) key, which covers keys a provider echoes back in an error message.
- **Run id on every line.** It comes from a context variable, so each line from a run carries its `run_id`, including the lines from the MCP server process (the client passes `RUN_ID`).
- **What is deliberately not logged:** task text and generated code, which may contain business data. We log sizes and outcomes instead.
- **MCP server environment fix (`mcp_client._server_env`).**
  - The MCP SDK starts the server with only a few safe variables (PATH, HOME, …).
  - That worked in round 2 only because the server re-read `.env`. In production it would have started in development mode without its keys.
  - The client now passes exactly the application's settings variables, not the whole environment.
- **Retries, Groq pacing and the reviewer fallback** now log structured events instead of strings.
- **Tests (`tests/test_logging.py`, 4 new):** secrets masked in the message, extras and traceback; unknown key-shaped strings masked; production output is JSON with the run id; the MCP server receives our settings and the run id but not unrelated variables.

### Step 4: reliability and failure handling
**Every failure now has a type (`reviewer_loop/errors.py`).**
- Before, a failed run had `status: error` and a free-text message.
- Now it also has one of 11 error types: `rate_limited, provider_timeout, provider_unavailable, provider_auth, provider_rejected, malformed_output, budget_exceeded, tool_failure, deadline_exceeded, invalid_input, internal`.
- The same name appears in the run record (user), the logs and the metrics (operator).
- Provider SDK exceptions are classified by status code and exception name.
- MCP tool errors carry their type across the process boundary as a `[type]` prefix on the message.
- MCP SDK exception groups are unwrapped, so the real cause is reported.

**Retries: what is retried and why.**

| Failure | Retried? | Why |
|---|---|---|
| 429, 5xx, connection errors | Yes: up to `MAX_PROVIDER_RETRIES` (4) attempts, honouring `retry-after` or backing off 5/10/20 s | Usually temporary on a shared free tier |
| Timeout | **Once** (new) | The provider may already have done and billed the work, and a second slow answer usually means overload, which the circuit breaker should see quickly |
| 401/403, other 4xx | Never | Retrying a wrong key or a bad request can't succeed and only burns time |
| Malformed model output | One re-ask with the validation error (unchanged) | The model often fixes its format when told what was wrong |

**Provider timeouts (new).** Every Groq and Gemini HTTP call has a timeout (`PROVIDER_TIMEOUT_S`, default 60 s). Before, a hung provider could hold a run forever.

**Circuit breakers (new, `reviewer_loop/breaker.py`, D5).**
- After 3 outage-type failures in a row, calls to that provider fail immediately for 60 s, then one trial call is allowed through.
- Groq's breaker wraps `llm.call_groq`. Gemini's is driven by `review_code` results in the orchestrator, because the MCP server process is restarted every run and would forget the state.
- Malformed output does not count as an outage.

**Run deadline (new, D4).**
- Every run has a wall-clock budget (`RUN_DEADLINE_S`, default 600 s).
- MCP tool calls are async, so they are cancelled with `asyncio.wait_for`.
- Synchronous provider calls check the deadline before starting. A retry wait that would end after the deadline fails at once instead of sleeping for nothing.

**Crash containment.**
- `run_task` used to catch only provider and tool errors. Any other exception (a bug, a broken MCP connection) would have propagated and, in a web server, killed the worker. Now every exception ends the run cleanly, with a type and the last good code.
- Bugs (`internal`) log a full traceback for the operator; the user sees only the error type and a short message.
- `run_with_mcp` returns an error record even if the MCP server cannot start.

**Secrets kept out of run records.** Error messages stored in run records (which users see) pass through the same redaction as the logs.

**Live progress.** `run_task(on_progress=...)` reports the record after each step; the API uses it for the live UI.

**Traceability (an MLOps addition).** Every run record stores `prompt_version` (a hash of the generator and reviewer system prompts) and its `duration_s`. When results change, you can tell whether the prompts changed before blaming the model.

**Metrics hooks (new `reviewer_loop/metrics.py`).**
- Prometheus counters and histograms for provider calls (outcome, latency, tokens) and breaker state.
- Each provider call is also logged as one structured event.
- *Deviation from the plan:* this lives in the core package, not in `api/`, because the core records provider calls.

**Sandbox resource limits (new).**
- On Linux, which includes the production container, the sandboxed test process gets hard limits: 1 GB memory, 10 MB file size, 30 s CPU. A memory bomb now gets a `MemoryError` instead of exhausting the container.
- The limits are set inside the child (in its conftest), not with `subprocess`'s `preexec_fn`, which is unsafe in a threaded parent like the API.

**Tests (`tests/test_reliability.py`, 14 new):**
- timeouts retried exactly once;
- 401/403/400/429/503 each get the right type;
- breaker opens, fails fast, half-opens and closes (fake clock), and an open Groq breaker never calls Groq;
- a deadline cancels a slow tool, keeps the draft and finishes in under 5 s;
- a retry wait past the deadline fails without sleeping;
- failure records are typed, redacted and reported through progress;
- **a real MCP server process that dies mid-call** (`tests/crashing_mcp_server.py`, `os._exit`) ends the run with `tool_failure` instead of hanging;
- a server that cannot start gives an error record;
- a memory bomb is stopped by the sandbox limit (Linux only; runs in CI/Docker).

### Step 5: the API service (replaces Streamlit's server role, D1)
**New package `api/`:** `main.py` (routes, middleware), `runner.py` (queue + storage), `auth.py` (Cloudflare Access), `__main__.py` (entry point: `python -m api`).

**Runs no longer execute inside a web request.**
- `POST /api/runs` validates the request, checks the limits, saves a `queued` record and answers `202` with an id at once.
- A background worker takes runs from an in-process queue and executes each in its own thread (D4), saving the record after every step.
- The UI polls `GET /api/runs/{id}` for live progress.
- Before, Streamlit ran the whole loop inside the browser session: closing the tab lost the run, and nothing stopped many runs at once.

**Limits (cost and abuse protection), each answering `429` with `Retry-After`:**
- `MAX_CONCURRENT_RUNS` (1: the free-tier quotas are shared);
- `MAX_QUEUED_RUNS` (5);
- `RUNS_PER_USER_PER_HOUR` (10).

**Input validation:**
- task 10–`MAX_TASK_CHARS` characters (4000);
- `max_iterations` 1–`MAX_ITERATIONS`;
- unknown fields and wrong types rejected with `422` before any work starts;
- the wrong-finding demo switch is refused when `ALLOW_DEMO_INJECTION` is off (production default).

**Authentication (production).**
- Every API request must carry a valid Cloudflare Access JWT.
- The app checks the signature against Cloudflare's public keys, plus the audience and the issuer. It does not trust the plain email header, which anything that bypassed Access could forge.
- Development uses a fixed `dev@local` user.
- Users see only their own runs; another user's run id returns `404`.

**Storage.**
- One JSON file per run in `DATA_DIR/runs`, written atomically (temp file + rename), so a poll never reads half a file.
- Run ids are validated against a strict pattern before they touch the filesystem (no path traversal). Benchmark task ids go through an allow-list.

**Restarts.** At startup, runs left `queued`/`running` are marked `interrupted` with a message asking the user to resubmit. Before, a crash simply lost them.

**Operator endpoints.**
- `GET /healthz`: liveness, the process answers. Used by Docker's `HEALTHCHECK`.
- `GET /readyz`: config loaded, data directory writable, and the **real MCP server starts and lists its 3 tools**. No LLM calls, so readiness never depends on quota. Cached 30 s. Used by `deploy.sh` to decide whether to roll back.
- `GET /api/status`: circuit breaker state per provider, queue, tokens used today against the budget, running version (feeds the UI's status strip).
- `GET /metrics`: Prometheus format, adding HTTP requests, durations, runs by status/error type, run duration, queue depth and runs in progress.

**Request middleware:**
- a request id (also returned as `X-Request-ID`) and a structured access log line with route, status and latency;
- unhandled errors become a JSON `500` with the request id, the traceback goes to the logs only;
- security headers: CSP `default-src 'self'`, `nosniff`, `no-referrer`, no framing.

**Startup.** `python -m api` loads the configuration **before** starting uvicorn. A bad config exits with code 2 and one JSON log line naming the problem, e.g. `GROQ_KEY: must not be empty`. Uvicorn's own logs go through our JSON and redaction handler.

**`APP_VERSION` setting:** set to the git SHA at image build time, and shown in `/api/status`, `/readyz` and the run records, so you can always tell which build is running (important during a rollback).

**Example tasks.** The API returns only each benchmark task's prompt, never its hidden tests or reference solution.

**Tests (`tests/test_api.py`, 19 new):**
- normal lifecycle (queued → converged, listed);
- a failed run keeps its error type and is counted in `/metrics`;
- 6 kinds of invalid input rejected with no work started;
- demo switch refused;
- path-traversal ids → 404;
- examples never expose hidden tests;
- full queue → 429;
- per-user hourly limit;
- interrupted runs after a restart;
- `/healthz` + a **real `/readyz`** (spawns the MCP server) + `/api/status` + security headers;
- production auth: missing token, wrong audience, wrong issuer, wrong signing key → 401, valid → 200, health checks need no login;
- users can't see each other's runs.

**Total so far: 57 tests passing (1 Linux-only test skipped on Windows).**

### Step 6: the web UI (replaces Streamlit's UI, D1)
- **Design chosen by the user from three mockups: "Two voices".**
  - Each round of a run is shown as a conversation in three columns: the generator (left), the reviewer (right) and, between them, the **evidence and rulings**: test results, lint results and the orchestrator's decisions (fix kept, rolled back, disputed, late objection).
  - This makes the core idea (two models disagree; objective evidence and fixed rules settle it) visible at a glance. The Streamlit version showed the same data as separate tables.
  - The conversation is built from each finding's history, which already records who changed its status in which round and why.
- **Files:** `api/static/index.html`, `app.css`, `app.js`. Plain HTML/CSS/JS served by FastAPI, with no framework and no build step. System fonts only, nothing loaded from other sites (which the strict Content-Security-Policy enforces).
- **Pages:**
  - new review: task box, example tasks, round limit, and the demo switch only where it's allowed;
  - my runs;
  - a run page with live progress (polls every 2 s while queued or running), the verdict, the conversation, and tabs for final code, changes per round, all findings with their full trail, and MCP tool calls;
  - benchmark (headline numbers + table, each task opening its recorded run).
- **The user sees failures clearly.** Each error type has a plain-language explanation and what to do next, e.g. `provider_auth`: "A model provider rejected our API key. This needs an operator." Technical details and the run id are one click away for support. Disputed findings show as "Needs a human decision".
- **The operator's view is in every page header:** provider health (green/amber/red from the circuit breakers), queue, tokens used today against the budget, and the running version.
- **Security:** everything from users or models is inserted with `textContent`, never parsed as HTML. A review finding containing `<script>` is displayed, not executed. This matters because this app shows LLM output, which is untrusted.
- **Quality floor:** light and dark mode, stacks to one column on phones, visible keyboard focus, reduced-motion respected.
- **Removed `app.py`** (Streamlit).
- **Test:** the UI is served with the strict CSP header.

### Step 7: containerisation
**`Dockerfile` (multi-stage).**
- *Build stage:* installs exactly `uv.lock` (`uv sync --frozen --no-dev`) into a virtualenv.
- *Runtime stage:* copies only that virtualenv and the source. No uv, compilers or dev tools.
- **Base images pinned by digest.** These are multi-arch indexes, so the same pin works on the Windows PC (amd64) and the MacBook (arm64): `python:3.13-slim`, plus `uv` for the build stage.
- **Non-root:** runs as uid 10001. The source is owned by root, so the app can read its code but not modify it.
- **Image defaults are production:** `APP_ENV=production`, `DATA_DIR=/data` (a volume). `APP_VERSION` is a build argument, set to the git SHA.
- **`HEALTHCHECK` on `/healthz`.** The deploy script checks `/readyz` separately.

**`.dockerignore` is an allow-list.** Only `pyproject.toml`, `uv.lock`, the code, the benchmark tasks, the coding standard and the committed benchmark results are sent to the build. `.env`, secrets, `.data/`, PDFs, docs, tests and `.git` can't end up in the image, even by accident. Verified: the built image has no `.env`.

**`deploy/compose.yaml` (production, one host).**
- **app:**
  - image pinned to a tag (the git SHA) from GitHub's container registry;
  - non-secret settings from `.env.prod`;
  - secrets as Compose secret files under `/run/secrets`, never environment variables;
  - **read-only filesystem**, with only `/tmp` (tmpfs, 256 MB) and the `/data` volume writable;
  - **all Linux capabilities dropped**, `no-new-privileges`;
  - **1 GB memory, 1 CPU, 256 processes** (fork-bomb protection);
  - port published on **127.0.0.1 only**;
  - `restart: unless-stopped`;
  - log rotation.
- **cloudflared:** the Cloudflare Tunnel connector, pinned by digest, with its token as a secret file (`TUNNEL_TOKEN_FILE`), hardened the same way, and starting only once the app is healthy. It sits in a `tunnel` profile so the app can be started alone for local checks.
- **`deploy/compose.local.yaml`** builds the image from the checkout instead of pulling it. **`deploy/secrets/README.md`** documents each secret, where to get it, permissions and rotation.

**Verified locally (Docker Desktop, production mode, dummy keys, no LLM calls):**
- the container becomes healthy and `/readyz` reports the MCP server starting inside the read-only container;
- the API refuses unauthenticated requests (401);
- writing to `/app` fails (read-only) while `/data` is writable;
- the process has zero capabilities;
- the MCP self-test runs the sandbox and ruff inside the container;
- **the Linux-only sandbox tests pass, including the memory-bomb limit**;
- logs are JSON with no key material;
- **with an empty `groq_key` the container exits with code 2 and one line naming `GROQ_KEY`**, and keeps restarting unhealthy. `deploy.sh` will detect that and roll back.

**Small fix:** uvicorn adds a colour-coded duplicate of each message (`color_message`) to its log records; it is now dropped from our JSON logs.

## 4. Removed or deliberately not built

_Filled in as the work progresses._

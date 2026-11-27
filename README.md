# Crosscheck: multi-model code review orchestrator

A generator LLM writes Python code and its tests for a natural-language task. A reviewer LLM **from a different
provider**, reached through **our own MCP server**, reviews it with the results of sandboxed tests and static analysis
in hand. The generator revises, and may decline findings it disagrees with, until the run converges.

- Generator: `openai/gpt-oss-20b` on **Groq**
- Reviewer: `gemini-3.5-flash-lite` on **Google AI Studio** (fallback `gemini-3.1-flash-lite` when it is overloaded)
- MCP: official Python SDK (`mcp` 2.x), stdio transport
- Service: FastAPI (JSON API + web UI) in Docker, deployed behind Cloudflare Tunnel + Access

This is the **production version** (round 3). **[CHANGES.md](CHANGES.md)** records every change from the round-2
prototype and why, and **[TECHNICAL_NOTE.md](TECHNICAL_NOTE.md)** answers the risk questions (what changed, what was
left out, what worries us most).

## Contents
[Architecture](#architecture) · [Setup](#setup) · [How the review loop works](#how-the-review-loop-works) ·
[CI/CD](#cicd-from-repository-to-production) · [Deployment](#deployment) · [Rollback](#rollback) ·
[Security](#security) · [Monitoring](#monitoring) · [Failure handling](#failure-handling) ·
[Evaluation](#evaluation) · [Known limitations](#known-limitations)

## Architecture

![Production architecture: GitHub Actions builds and tests every change and publishes an image per commit to GitHub Container Registry; the operator deploys a commit with deploy.sh, which checks readiness and rolls back; users reach the app through Cloudflare's login and an outbound tunnel; the app calls Groq and runs our MCP server, which runs a sandbox and calls Gemini.](assets/architecture.svg)

| Component | What it does | Code |
|---|---|---|
| API + UI | Validates requests, queues runs, serves the web UI and the operator endpoints | `api/main.py`, `api/static/` |
| Job runner | Runs each review in a background thread (provider calls block); saves progress after every step | `api/runner.py` |
| Auth | Verifies the Cloudflare Access JWT (signature, audience, issuer) in production | `api/auth.py` |
| Orchestrator | The loop: draft → tests → lint → review → revise, until convergence | `reviewer_loop/orchestrator.py` |
| Ledger | Each finding's status and history; all disagreement rules | `reviewer_loop/ledger.py` |
| MCP server | Our own server with three tools: `run_tests`, `run_static_analysis`, `review_code` | `reviewer_loop/mcp_server.py` |
| Sandbox | Runs generated tests in a subprocess: no secrets, audit hook, memory/CPU/file limits, timeout | `reviewer_loop/sandbox.py` |
| Settings | Every operational value from environment variables, validated at startup | `reviewer_loop/config.py` |
| Reliability | Error types, run deadline, circuit breakers, metrics, redacted JSON logs | `reviewer_loop/errors.py`, `breaker.py`, `metrics.py`, `logs.py` |

**Major design decisions** (full log in [CHANGES.md](CHANGES.md#2-decision-log)):
- **FastAPI with a hand-written UI replaces Streamlit.** It gives health and metrics endpoints, a run queue, input validation and a UI we control. Streamlit ran each multi-minute review inside a browser session.
- **One host with Docker Compose, reached through Cloudflare Tunnel + Access.** It's free, gives HTTPS on a real domain with no open ports, and provides login without writing authentication code.
- **Pull-based deployment.** CI publishes an immutable image per commit; the host runs `deploy.sh <sha>`. The host is behind NAT, and a self-hosted runner on a public repo would let pull requests execute code on it.
- **A worker thread per run with a cooperative deadline.** Provider calls block while they pace and back off; on the event loop they would freeze `/healthz`.
- **Circuit breakers in the app process.** The MCP server is restarted for every run, so breaker state kept there would reset each time.

## Setup

### Development
Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).
```bash
uv sync                          # installs exactly what uv.lock pins (incl. dev tools)
cp .env.example .env             # then put your GROQ_KEY and GEMINI_KEY in .env
uv run python -m api             # http://127.0.0.1:8000 (development mode: no login, LLM cache on)
```

| Command | What it does | API cost |
|---|---|---|
| `uv run pytest -q` | All tests: fake providers, real sandbox, real MCP server | none |
| `uv run ruff check .` | Lint and security rules | none |
| `uv run python -m reviewer_loop.mcp_client` | MCP self-test: list the tools, run the test and lint tools over stdio | none |
| `uv run python -m reviewer_loop.orchestrator "<task>"` | One review from the command line | one run |
| `uv run python -m benchmark.run_benchmark --verify` | Hidden tests must pass the reference solutions and fail the buggy ones | none |
| `uv run python -m benchmark.run_benchmark [--tasks a,b]` | Run the benchmark (results in `results/`) | ~45k Groq + ~50k Gemini tokens |
| `APP_TAG=local docker compose -f deploy/compose.yaml -f deploy/compose.local.yaml up --build` | The production container, built from this checkout | none until you run a review |

### Configuration
All settings are environment variables with safe defaults. `.env.example` lists every one.

| | `APP_ENV=development` | `APP_ENV=production` (the image default) |
|---|---|---|
| Keys from | `.env` or the environment | **secret files** in `/run/secrets`; `.env` is never read |
| Login | none (`dev@local`) | Cloudflare Access JWT required (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`) |
| Logs | readable text | JSON lines |
| LLM response cache | on | off |
| Required | `GROQ_KEY`, `GEMINI_KEY` | also `CF_ACCESS_*` and `OPERATOR_CONTACT` |

A missing or invalid setting stops startup with one line naming it (never its value), e.g.
`Invalid configuration: GROQ_KEY: Value error, must not be empty`. The process exits with code 2.

## How the review loop works

Each round:
1. run the generator's tests in the sandbox;
2. run ruff;
3. **regression guard**: roll back a revision that broke passing tests or added blocking lint;
4. **review**: the reviewer sees the code, the test and lint results, the retrieved sections of the coding standard, and the ledger of earlier findings;
5. **convergence check**;
6. the generator revises, answering each finding with *accept* or *decline*.

- **Convergence:** the tests pass, there is no blocking lint, and no blocking finding is unresolved. Other outcomes: `deadlock` (unchanged code) and `timeout` (round limit), plus `error` and `interrupted`.
- **Disagreement rules** (`ledger.py`):
  - The generator may decline a finding. The reviewer must then withdraw it or maintain it; a maintained finding becomes `disputed`, is escalated to a human, and stops blocking.
  - Revisions that make the objective evidence worse are rolled back, and their findings are marked `reverted`.
  - New blocking findings on unchanged lines the reviewer already passed are downgraded as late objections.
  - **Objective evidence (tests, lint) outranks either model's opinion.**
- **Structured, validated reviews.** Pydantic plus a check that the cited line exists. One re-ask on malformed output. Severity critical/major = blocking.

## CI/CD: from repository to production

```
branch → pull request → CI (checks + image) → review & merge → CI publishes ghcr.io/…:<sha> → ./deploy.sh <sha> on the host
```

`.github/workflows/ci.yml`:

| Job | When | Steps |
|---|---|---|
| `checks` | every push and PR | `uv sync --frozen` → `ruff check` → `pytest` (no real keys, no quota) → `pip-audit` on the locked runtime dependencies |
| `image` | after `checks` | `docker build` → `scripts/smoke.sh` → `scripts/test_deploy.sh` |
| `publish` | pushes to `main` and `v*` tags | build **amd64 + arm64**, push to GitHub Container Registry tagged with the short commit SHA (+ `main`, version), with provenance and SBOM |

- **`scripts/smoke.sh`** runs the container as production does (read-only, no capabilities, secret files). It must become ready, refuse API calls without a login, keep keys out of its logs, and **refuse to start with an empty key**.
- **`scripts/test_deploy.sh`** runs the real `deploy.sh`, with the tunnel, on a good release and then a broken one, which must be rolled back.

Actions are pinned to commit SHAs, and Dependabot proposes weekly updates for Python packages, base images, cloudflared and actions. Every release in production is an image that passed all of the above. Promotion is deliberate: a human runs `deploy.sh` with the SHA printed in the publish run's summary (continuous *delivery*, not continuous *deployment*).

## Deployment

One host running Docker Compose (`deploy/compose.yaml`):
- **`app`:** image pinned to a SHA, non-secret settings from `deploy/.env.prod`, secrets as files.
  - **Read-only filesystem** (writable: `/tmp` tmpfs and the `/data` volume);
  - **all Linux capabilities dropped**, `no-new-privileges`;
  - **1 GB memory, 1 CPU, 256 processes**;
  - port bound to **127.0.0.1 only**;
  - `restart: unless-stopped`, log rotation.
- **`cloudflared`:** the Cloudflare Tunnel connector, pinned by digest, with its token as a secret file. It opens an **outbound** connection to Cloudflare, so the host needs no open ports. Cloudflare Access puts an email login in front of the whole hostname.

**First-time host setup.**
1. **Cloudflare:**
   - create a tunnel and give it a public hostname whose service is `http://app:8000`;
   - create an Access application for that hostname with an email allow-list;
   - note the team domain and the application's AUD tag.
2. **On the host:**
   ```bash
   git clone https://github.com/zeinshehab/ark-assessment-2.git && cd ark-assessment-2/deploy
   cp .env.prod.example .env.prod        # set CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD, OPERATOR_CONTACT
   # one file per secret, value only: secrets/groq_key, secrets/gemini_key, secrets/tunnel_token
   chmod 600 secrets/groq_key secrets/gemini_key secrets/tunnel_token
   ./deploy.sh <sha>                     # the SHA from CI's publish summary
   ```
3. **Routine deploys:** `git pull` (to get the deploy files), then `./deploy.sh <sha>`.

**What `deploy.sh` does:**
1. Pull the image. If that fails, nothing has changed.
2. Replace only the app container.
3. Wait for `/readyz` to answer 200 **and report the new version**. Give up early if the container is crash-looping.
4. **Ready:** start the tunnel and record the version.
5. **Not ready:** print the app's last log lines and **roll back** to the previous version.

Exit codes: `0` deployed · `1` failed, rolled back · `2` could not recover. Health checks: Docker's `HEALTHCHECK` uses `/healthz` (liveness); deployments use `/readyz` (readiness: config loaded, data volume writable, MCP server starts, no LLM calls).

## Rollback

- **Automatic:** a release that doesn't become ready is rolled back by `deploy.sh`. A broken release is detected and rolled back in about 15 s.
- **Manual:** `./deploy/rollback.sh` redeploys the previous good version, for a release that passed readiness but misbehaves in use.
- Images are immutable and tagged by commit, so rolling back is exact. Run records live on a volume and survive deploys. A run that was in progress during a restart is marked `interrupted` on startup.

## Security

| Risk | What we do |
|---|---|
| **Secrets** | Never in Git (gitignored), in the image (`.dockerignore` is an allow-list) or in environment variables in production (secret files). `SecretStr` in code. CI uses only dummy keys. |
| **Secrets in logs** | A redaction filter masks the configured keys and anything key-shaped in messages, extra fields and tracebacks. Error messages in run records are redacted too. Task text and generated code are never logged. |
| **Who can use it** | Cloudflare Access login. The app verifies Access's signed JWT, so a request that bypassed Cloudflare is refused. Users see only their own runs. |
| **Excessive requests and cost** | One run at a time, a queue of 5, 10 runs per user per hour (429 + `Retry-After`), a run deadline, a daily token budget, circuit breakers. |
| **Input validation** | Pydantic limits on every field (task 10–4000 characters, round limit). Run ids are validated before touching the filesystem; benchmark ids use an allow-list. |
| **Container** | Non-root (uid 10001), read-only filesystem, no capabilities, `no-new-privileges`, resource limits, loopback-only port, base images pinned by digest. |
| **Dependencies** | Hash-locked `uv.lock`, `pip-audit` in CI, Dependabot, actions pinned to SHAs. |
| **Untrusted generated code** | Runs only in the sandbox subprocess: no keys in its environment, an audit hook (no network, subprocesses, native code or files outside its folder), memory/CPU/file-size limits, a timeout. The JUnit report it can tamper with is parsed with `defusedxml`. |
| **Prompt injection** | The models have **no tools**: the orchestrator decides every tool call. Model output is schema-validated. Convergence requires passing tests and clean lint, not just a reviewer's approval. The UI renders model output as text, never HTML. |
| **Browser** | CSP `default-src 'self'`, `nosniff`, `no-referrer`, no framing. No third-party scripts, styles or fonts. |

The biggest remaining risk is that the sandbox shares a container with the keys; see [TECHNICAL_NOTE.md](TECHNICAL_NOTE.md).

## Monitoring

An operator can answer each question from these sources:

| Question | Where |
|---|---|
| Is it running? | `GET /healthz`, Docker health status, `docker compose ps` |
| Is it ready to serve this version? | `GET /readyz` (version, config, data volume, MCP server) |
| Are requests and runs succeeding? | `http_requests_total{status}`, `runs_total{status,error_type}`, the `request` and `run finished` log lines |
| Is a provider failing? | `provider_calls_total{outcome}`, `provider_circuit_open`, the UI status strip (`/api/status`) |
| How long do things take? | `http_request_duration_seconds`, `run_duration_seconds`, `provider_call_duration_seconds` |
| What type of error occurred? | the `error_type` label and log field, one of 11 fixed types (`reviewer_loop/errors.py`) |

- **Logs:** JSON lines on stdout/stderr, collected by Docker (`docker compose -f deploy/compose.yaml logs app`). They carry `run_id`, `request_id`, provider, model, latency and error type.
- **Metrics:** Prometheus format at `/metrics`. No Prometheus server is deployed (one host, kept simple). These are the alerts we'd configure:

| Alert | Rule (PromQL-style) | Why |
|---|---|---|
| App not ready | `/readyz` fails for 2 min (external check) | Down, or a deploy failed |
| Provider key rejected | `increase(provider_calls_total{outcome="provider_auth"}[10m]) > 0` | A revoked or expired key needs a human now |
| Provider outage | `provider_circuit_open == 1` for 5 min | Runs will fail until it recovers |
| Runs failing | `rate(runs_total{status="error"}[30m]) / rate(runs_total[30m]) > 0.3` | Something systematic is wrong |
| Backlog | `run_queue_depth >= 5` for 10 min | Users are being turned away (429) |

## Failure handling

| Failure | Handling |
|---|---|
| Provider 429 / 5xx / connection error | Retried up to 4 attempts with back-off (5, 10, 20 s, or the provider's `retry-after`) |
| Provider timeout | Every call has a 60 s timeout; **retried once only** (the provider may have done, and billed, the work) |
| Wrong or revoked key (401/403), other 4xx | **Never retried**; the run fails with `provider_auth` / `provider_rejected` |
| Provider down for a while | **Circuit breaker:** after 3 outage-type failures in a row, calls fail fast for 60 s, then one trial call is allowed |
| Reviewer model overloaded | Falls back to `gemini-3.1-flash-lite` |
| Malformed model output | One re-ask with the validation error, then `malformed_output` |
| MCP server crash or failed start | The run ends with `tool_failure` and keeps its last good code; it never hangs |
| Run takes too long | `RUN_DEADLINE_S` (600 s): tool calls are cancelled, provider calls check the deadline, retry waits never sleep past it |
| Generated code hangs or explodes | 15 s timeout; 1 GB memory, 30 s CPU and 10 MB file-size limits |
| App crash | Docker restarts it; runs in flight are marked `interrupted` |
| Bad deployment | `deploy.sh` rolls back automatically |

**What is retried, and how runaway cost is avoided.** Only transient failures are retried, with a bounded number of attempts.
- **Per run:**
  - at most 8 generator calls (a draft and up to 3 revisions, each with one re-ask);
  - at most 16 reviewer calls (4 reviews × one re-ask × one fallback);
  - in practice about 4–5k Groq tokens (the benchmark averaged 4.4k per task).
- **Above that:**
  - a 600 s deadline per run;
  - one run at a time;
  - 10 runs per user per hour;
  - a daily per-model Groq token budget (190k);
  - circuit breakers that stop calls to a provider that keeps failing.

## Evaluation

10 benchmark tasks with natural-language prompts and **hidden tests the loop never sees**. The baseline is the generator's first draft; one run each (details in `results/report.md`):

| | Generator only (first draft) | Orchestrated |
|---|---|---|
| Tasks passing all hidden tests | 8/10 | 10/10 |
| Hidden tests passed | 51/68 | 68/68 |

- Both baseline defects were fixed; one, an XSS hole, was caught **only** by the reviewer.
- No task was made worse. All 10 converged (mean 1.9 rounds). 1 of 16 findings was a false positive.
- The sample is small, so these numbers are suggestive, not conclusive.
- **Operationally, the benchmark is the evaluation to rerun before changing a model or prompt.** Each run record stores `prompt_version` (a hash of the system prompts) and the model names.

## Known limitations

- **The sandbox is not a security boundary.** Generated code runs in a subprocess of the same container that holds the API keys (as files). The audit hook and resource limits are guard rails; a determined escape could reach the keys or the network. The fix is a separate, network-less, secret-less sandbox container (gVisor or similar).
- **Single instance.** The queue, rate limits and circuit breakers live in memory; run records are JSON files on one volume. Scaling out needs a shared store and a database.
- **A failed deploy causes a short outage** (about 15 s) because the new container replaces the old one before it is ready. Blue/green deploys would avoid that.
- **No staging environment**, so configuration drift is caught by the readiness check in production rather than earlier.
- **No metrics server or alerting is deployed.** The metrics and alert rules exist, but someone has to look.
- **Free tiers.** Low rate limits and "lite" models; quota exhaustion is a real failure mode.
- **Review quality limits carried over from round 2.**
  - The generator writes its own tests, so "tests pass" can be weak.
  - The disagreement rules are heuristics.
  - The benchmark is small and self-labelled.
  - Single-file Python functions only.

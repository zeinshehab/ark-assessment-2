# Technical note: taking the review orchestrator to production

Short answers; [CHANGES.md](CHANGES.md) has the full record and [README.md](README.md) the operating details.

## 1. What were the biggest production risks in the original application?

1. **Anyone with the URL could spend our quota, and runs lived in a browser tab.**
   - The Streamlit app had no login and no limits.
   - Each multi-minute review ran inside the user's browser session, so closing the tab lost it, and nothing stopped ten people starting runs at once against shared free-tier limits.
2. **Untrusted, model-written code ran with weak isolation.**
   - An audit hook and a timeout, but no memory or CPU limits.
   - The JUnit report it could overwrite was parsed with an XML parser that is unsafe on untrusted input.
3. **Failures could hang or crash the run.**
   - Provider calls had no timeout, so a hung provider blocked forever.
   - Only provider and tool errors were caught; any other exception, including an MCP server dying, would propagate and, in a web server, take the worker down with it.
4. **Configuration and secrets were prototype-grade.**
   - Constants were hard-coded and keys were read from `.env` at import.
   - Development and production weren't separated.
   - We found the MCP server only got its configuration by re-reading `.env`. In a container it would have started without its keys.
5. **No way to operate it.**
   - `print` to stderr, no health checks, no metrics, no error categories.
   - No CI, top-level-only dependency pins, no container, no deployment or rollback.

## 2. What did you change?

- **Configuration:**
  - validated settings from environment variables, with development/production modes;
  - secrets as files in production, `SecretStr` in code;
  - fail-fast startup that names the bad setting but never its value.
- **Observability:**
  - JSON logs with run and request ids and secret redaction (including tracebacks);
  - Prometheus metrics;
  - `/healthz`, `/readyz` (starts the real MCP server, no LLM calls) and `/api/status`;
  - a status strip in the UI.
- **Reliability:**
  - 11 typed errors shared by the UI, logs and metrics;
  - provider timeouts; timeouts retried once, auth errors never;
  - per-provider circuit breakers, a run deadline;
  - crash containment (every failure ends the run cleanly, keeping its last good code);
  - memory, CPU and file-size limits in the sandbox; safe XML parsing.
- **Service:**
  - FastAPI with a queued, threaded job runner;
  - input validation; per-user and queue limits;
  - Cloudflare Access JWT verification, users isolated from each other;
  - a new UI that shows the two models' argument round by round, and renders model output as text, never HTML.
- **Delivery:**
  - hash-locked dependencies (uv);
  - a non-root, read-only, capability-free container with resource limits;
  - CI covering lint, tests, a dependency audit, the image build, a container smoke test, a deploy-and-rollback test, and a multi-arch publish;
  - `deploy.sh`, which rolls back automatically when a release isn't ready;
  - Cloudflare Tunnel + Access in front, with no open ports.
- **One production incident during the work, and how it was fixed and tested.**
  - The first real bad-deploy rehearsal *didn't* roll back. With the tunnel enabled, `docker compose up` itself failed, and the script stopped before reaching the rollback.
  - The fix restructured the deploy. CI now runs the deploy script the way production does; that test fails against the old script.

## 3. What did you deliberately leave out?

- **Cloud infrastructure** (Kubernetes, Terraform, managed platforms). One host with Compose is enough for a handful of internal users, and it's free.
- **A running metrics and alerting stack.** The metrics and alert rules are defined (README), but no Prometheus/Grafana is deployed.
- **A database, a shared queue, and multiple instances.** JSON files and in-memory limits are correct for exactly one instance.
- **Staging, blue/green deploys and continuous deployment.** Releases are promoted by a person running `deploy.sh`.
- **A separate sandbox service and a secrets manager.** See question 5.
- **Image signing.** Provenance and an SBOM are attached, but images aren't signed.
- **Load testing, SSO groups and audit trails.**

Each would add moving parts the brief explicitly doesn't need, and that we couldn't operate well on free tiers.

## 4. What would you improve given another week?

1. **Isolate the sandbox:** run tests in a separate container (gVisor or a micro-VM) with **no network and no secrets**, reached over MCP's HTTP transport.
2. **Safer releases:**
   - a staging environment configured like production;
   - a pre-flight config check in `deploy.sh` before the running version is touched;
   - blue/green, so even a bad release causes no blip.
3. **Alerting that wakes someone:**
   - a hosted metrics stack (e.g. Grafana Cloud's free tier) with the README's alert rules;
   - an external uptime check;
   - a counter for sandbox policy violations.
4. **State for more than one instance:** Postgres for runs, Redis for the queue and rate limits. Then two app instances.
5. **An evaluation gate (MLOps):** run the benchmark automatically when a prompt or model changes, compare against the last results, and grow it beyond 10 tasks.

## 5. What is the biggest remaining production risk?

**Model-written code running in the same container as the API keys.** The code is untrusted by definition, and a user's task can steer it (prompt injection).

**Mitigations today:**
- the sandbox subprocess gets no keys in its environment;
- an audit hook blocks network, subprocesses, native code and file access outside its folder;
- resource limits; the container is non-root, read-only and capability-free;
- keys are files, not environment variables.

**Why it's still a risk:** an audit hook is a guard rail, not a security boundary. A determined escape running as the same user could read the key files or reach the network.

## Final question: if this were deployed tomorrow and I operated it, what would worry me most?

**The sandbox:** the same risk as question 5, because it combines high impact with weak detection.
- *Impact:* stolen provider keys can be abused, run up bills and get the accounts banned.
- *Detection:* nothing today would alert on an escape. A blocked attempt looks like an ordinary failing test.

The more *likely* problem is provider dependence: free-tier quota or an outage. That one is already **visible** (circuit-breaker metric, typed errors, status strip) and **contained** (fail fast, budgets).

**How I'd address it:**
1. Move test execution to an isolated, network-less, secret-less sandbox (the first item of question 4).
2. Until then, reduce the impact: per-key spending limits at the providers, and keys rotated regularly.

**How I'd monitor it in the meantime:**
- log and count every sandbox policy violation (blocked network, file or subprocess attempt) as a metric, and alert on any;
- alert on unusual provider usage per key;
- watch for unexpected outbound connections from the container.

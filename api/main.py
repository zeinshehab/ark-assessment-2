"""The web service: a JSON API, the static UI, and the operator endpoints.

  POST /api/runs            start a review run (202 + id); 422 invalid input; 429 over a limit
  GET  /api/runs            the caller's recent runs
  GET  /api/runs/{id}       one run, including live progress while it runs
  GET  /api/examples        example tasks (benchmark prompts only, never their hidden tests)
  GET  /api/benchmark       the committed benchmark results; /api/benchmark/{task} one benchmark run
  GET  /api/status          provider health, queue, token use today, version (the UI's status strip)
  GET  /healthz             liveness: the process answers (Docker HEALTHCHECK)
  GET  /readyz              readiness: config loaded, data dir writable, MCP server starts (deploy gate)
  GET  /metrics             Prometheus metrics
  /                         the UI (api/static)
"""
import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field, field_validator

from api.auth import current_user
from api.runner import LimitExceeded, Runner, RunStore
from reviewer_loop import config, llm, metrics
from reviewer_loop.breaker import BREAKERS
from reviewer_loop.mcp_client import ToolClient

log = logging.getLogger("api")
STATIC_DIR = Path(__file__).with_name("static")
QUIET_ROUTES = {"/healthz", "/readyz", "/metrics", "/api/status"}  # polled constantly: don't log every hit


class RunRequest(BaseModel):
    task: str = Field(min_length=10)
    max_iterations: int | None = Field(None, ge=1)
    inject_wrong_finding: bool = False

    @field_validator("task")
    @classmethod
    def _task_size(cls, value: str) -> str:
        value = value.strip()
        limit = config.get_settings().max_task_chars
        if len(value) < 10:
            raise ValueError("describe the task in at least 10 characters")
        if len(value) > limit:
            raise ValueError(f"the task is too long ({len(value)} characters, limit {limit})")
        return value

    @field_validator("max_iterations")
    @classmethod
    def _iterations(cls, value: int | None) -> int | None:
        limit = config.get_settings().max_iterations
        if value is not None and value > limit:
            raise ValueError(f"at most {limit} rounds")
        return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = config.get_settings()
    store = RunStore(settings.runs_dir)
    interrupted = store.mark_interrupted()
    app.state.runner = Runner(store)
    app.state.runner.start()
    app.state.ready_cache = (0.0, None)
    log.info("app started", extra={"version": settings.app_version, "env": settings.app_env,
                                   "interrupted_runs": interrupted, "workers": settings.max_concurrent_runs})
    yield
    await app.state.runner.stop()
    log.info("app stopped")


app = FastAPI(title="Code Review Orchestrator", version="3", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def observe(request: Request, call_next):
    """Request id, access log, HTTP metrics and security headers for every request."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error", extra={"request_id": request_id, "path": request.url.path})
        response = JSONResponse({"error": "internal", "detail": "Unexpected error", "request_id": request_id},
                                status_code=500)
    seconds = time.monotonic() - start
    route = getattr(request.scope.get("route"), "path", "unmatched")  # the template, e.g. /api/runs/{run_id}
    metrics.HTTP_REQUESTS.labels(route, request.method, response.status_code).inc()
    metrics.HTTP_DURATION.labels(route).observe(seconds)
    if route not in QUIET_ROUTES or response.status_code >= 400:
        log.info("request", extra={"request_id": request_id, "method": request.method, "route": route,
                                   "status": response.status_code, "latency_ms": round(seconds * 1000)})
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; "
                                                   "style-src 'self'; frame-ancestors 'none'")
    return response


@app.exception_handler(LimitExceeded)
async def _limit(request: Request, exc: LimitExceeded):
    return JSONResponse({"error": "rate_limited", "detail": str(exc)}, status_code=429,
                        headers={"Retry-After": str(exc.retry_after_s)})


# ---------- runs ----------

@app.post("/api/runs", status_code=202)
async def create_run(body: RunRequest, request: Request, user: str = Depends(current_user)):
    if body.inject_wrong_finding and not config.get_settings().allow_demo_injection:
        raise HTTPException(422, "The wrong-finding demo switch is disabled in this environment")
    record = request.app.state.runner.submit(user, body.task, body.max_iterations, body.inject_wrong_finding)
    return {"id": record["id"], "status": record["status"]}


@app.get("/api/runs")
async def list_runs(request: Request, user: str = Depends(current_user)):
    return request.app.state.runner.store.list(user)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str, request: Request, user: str = Depends(current_user)):
    record = request.app.state.runner.store.get(run_id)
    if record is None or record.get("user") != user:  # other people's runs are invisible, not forbidden
        raise HTTPException(404, "No such run")
    return record


# ---------- reference data ----------

@app.get("/api/examples")
async def examples(user: str = Depends(current_user)):
    index = json.loads((config.TASKS_DIR / "index.json").read_text(encoding="utf-8"))
    return [{"id": e["id"], "trap": e["trap"],
             "prompt": (config.TASKS_DIR / e["id"] / "prompt.md").read_text(encoding="utf-8")} for e in index]


@app.get("/api/benchmark")
async def benchmark(user: str = Depends(current_user)):
    return json.loads((config.RESULTS_DIR / "benchmark_results.json").read_text(encoding="utf-8"))


@app.get("/api/benchmark/{task_id}")
async def benchmark_run(task_id: str, user: str = Depends(current_user)):
    known = {e["id"] for e in json.loads((config.TASKS_DIR / "index.json").read_text(encoding="utf-8"))}
    if task_id not in known:  # an allow-list, so task_id can never be used as a path
        raise HTTPException(404, "No such benchmark task")
    return json.loads((config.RESULTS_DIR / "runs" / f"bench_{task_id}.json").read_text(encoding="utf-8"))


# ---------- operator endpoints ----------

@app.get("/api/status")
async def status(request: Request, user: str = Depends(current_user)):
    settings = config.get_settings()
    runner = request.app.state.runner
    return {
        "version": settings.app_version, "env": settings.app_env, "demo_injection": settings.allow_demo_injection,
        "operator_contact": settings.operator_contact,
        "max_iterations": settings.max_iterations, "max_task_chars": settings.max_task_chars,
        "providers": {name: {"state": b.state, "consecutive_failures": b.failures, "last_error_type": b.last_error_type}
                      for name, b in BREAKERS.items()},
        "queue": {"waiting": runner.queue.qsize(), "running": runner.running,
                  "max_concurrent": settings.max_concurrent_runs, "max_queued": settings.max_queued_runs},
        "tokens_today": {"generator": llm.tokens_used_today(settings.generator_model),
                         "generator_budget": settings.groq_daily_token_budget,
                         "reviewer": llm.tokens_used_today(settings.reviewer_model)},
    }


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


async def _check_ready() -> dict:
    checks = {}
    settings = config.get_settings()
    probe = settings.runs_dir / ".ready-probe"
    try:
        probe.write_text("ok")
        probe.unlink()
        checks["data_dir"] = "ok"
    except OSError as exc:
        checks["data_dir"] = f"not writable ({type(exc).__name__})"
    try:  # starts a real MCP server process and lists its tools: no LLM calls, so no quota is used
        async with asyncio.timeout(20):
            async with ToolClient() as tools:
                found = set(await tools.list_tools())
        missing = {"run_tests", "run_static_analysis", "review_code"} - found
        checks["mcp_server"] = "ok" if not missing else f"missing tools {sorted(missing)}"
    except Exception as exc:
        checks["mcp_server"] = f"failed ({type(exc).__name__})"
    return checks


@app.get("/readyz")
async def readyz(request: Request):
    cached_at, checks = request.app.state.ready_cache
    if checks is None or time.monotonic() - cached_at > 30:  # spawning the MCP server costs ~1 s: cache it
        checks = await _check_ready()
        request.app.state.ready_cache = (time.monotonic(), checks)
    ok = all(value == "ok" for value in checks.values())
    return JSONResponse({"status": "ready" if ok else "not ready", "checks": checks,
                         "version": config.get_settings().app_version}, status_code=200 if ok else 503)


@app.get("/metrics")
async def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

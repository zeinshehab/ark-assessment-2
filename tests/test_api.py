"""The HTTP layer: input validation, limits, auth, run lifecycle and operator endpoints.

The review loop is replaced by a fake (no LLM calls); everything else (FastAPI, the runner thread, storage,
the real MCP server for /readyz) is real.
"""
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from api import auth
from reviewer_loop import config, orchestrator

TASK = "Write a function add(a, b) that returns the sum of two numbers."


def fake_run(status="converged", error_type=None):
    async def run_with_mcp(task, task_id, on_progress=None, **kwargs):
        record = {"task_id": task_id, "task": task, "status": "running", "iterations": []}
        if on_progress:
            on_progress(record)
        return {**record, "status": status, "error_type": error_type, "final": {"solution_code": "x = 1"}}
    return run_with_mcp


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(orchestrator, "run_with_mcp", fake_run())
    from api.main import app
    with TestClient(app) as c:
        yield c


def wait_for(client, run_id, headers=None):
    for _ in range(100):
        record = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if record["status"] not in ("queued", "running"):
            return record
        time.sleep(0.05)
    pytest.fail("run did not finish")


# ---------- normal behaviour ----------

def test_a_run_is_queued_executed_and_saved(client):
    response = client.post("/api/runs", json={"task": TASK})
    assert response.status_code == 202
    record = wait_for(client, response.json()["id"])
    assert record["status"] == "converged" and record["user"] == "dev@local"
    assert [r["id"] for r in client.get("/api/runs").json()] == [record["id"]]


def test_failed_run_is_reported_with_its_error_type_and_counted(client, monkeypatch):
    monkeypatch.setattr(orchestrator, "run_with_mcp", fake_run("error", "provider_auth"))
    record = wait_for(client, client.post("/api/runs", json={"task": TASK}).json()["id"])
    assert record["status"] == "error" and record["error_type"] == "provider_auth"
    assert 'runs_total{error_type="provider_auth",status="error"} 1.0' in client.get("/metrics").text


# ---------- invalid input ----------

@pytest.mark.parametrize("body", [{"task": "too short"}, {"task": "x" * 5000}, {"task": TASK, "max_iterations": 0},
                                  {"task": TASK, "max_iterations": 99}, {"tsak": TASK}, {"task": ["a list"]}])
def test_invalid_input_is_rejected_before_any_work(client, monkeypatch, body):
    monkeypatch.setattr(orchestrator, "run_with_mcp", lambda *a, **k: pytest.fail("must not run"))
    response = client.post("/api/runs", json=body)
    assert response.status_code == 422
    assert client.get("/api/runs").json() == []


def test_demo_injection_is_refused_when_disabled(client, monkeypatch):
    monkeypatch.setenv("ALLOW_DEMO_INJECTION", "false")
    config.get_settings.cache_clear()
    assert client.post("/api/runs", json={"task": TASK, "inject_wrong_finding": True}).status_code == 422


@pytest.mark.parametrize("path", ["/api/runs/..%2F..%2Fsecrets", "/api/runs/1234567890-zzzzzz",
                                  "/api/benchmark/..%2Findex"])
def test_unknown_or_malicious_ids_are_not_found(client, path):
    assert client.get(path).status_code == 404


def test_examples_never_expose_hidden_tests(client):
    examples = client.get("/api/examples").json()
    assert len(examples) == 10 and set(examples[0]) == {"id", "trap", "prompt"}


# ---------- excessive requests ----------

def test_full_queue_answers_429(client, monkeypatch):
    monkeypatch.setenv("MAX_QUEUED_RUNS", "0")
    config.get_settings.cache_clear()
    response = client.post("/api/runs", json={"task": TASK})
    assert response.status_code == 429 and response.headers["Retry-After"]


def test_per_user_hourly_limit(client, monkeypatch):
    monkeypatch.setenv("RUNS_PER_USER_PER_HOUR", "2")
    config.get_settings.cache_clear()
    codes = [client.post("/api/runs", json={"task": TASK}).status_code for _ in range(3)]
    assert codes == [202, 202, 429]


# ---------- restarts ----------

def test_runs_in_flight_during_a_restart_are_marked_interrupted(monkeypatch, test_settings):
    runs = test_settings.runs_dir
    runs.mkdir(parents=True)
    (runs / "1700000000-abcdef.json").write_text(json.dumps(
        {"id": "1700000000-abcdef", "user": "dev@local", "task": TASK, "status": "running"}))
    from api.main import app
    with TestClient(app) as c:
        record = c.get("/api/runs/1700000000-abcdef").json()
    assert record["status"] == "interrupted"


# ---------- operator endpoints ----------

def test_health_readiness_status_and_security_headers(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")   # starts the real MCP server (no LLM calls)
    assert ready.status_code == 200, ready.json()
    assert ready.json()["checks"] == {"data_dir": "ok", "mcp_server": "ok"}
    status = client.get("/api/status").json()
    assert status["providers"]["groq"]["state"] == "closed" and status["queue"]["waiting"] == 0
    headers = client.get("/healthz").headers
    assert headers["X-Content-Type-Options"] == "nosniff" and "default-src 'self'" in headers["Content-Security-Policy"]


def test_ui_is_served_with_a_strict_content_security_policy(client):
    page = client.get("/")
    assert page.status_code == 200 and "<title>Crosscheck</title>" in page.text
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]   # no third-party scripts or styles
    assert client.get("/app.js").status_code == 200


# ---------- authentication (production) ----------

@pytest.fixture
def production(monkeypatch):
    """Production settings with a locally generated signing key standing in for Cloudflare's."""
    for name, value in {"APP_ENV": "production", "CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
                        "CF_ACCESS_AUD": "our-aud", "SECRETS_DIR": "/nonexistent",
                        "OPERATOR_CONTACT": "ops@example.com"}.items():
        monkeypatch.setenv(name, value)
    config.get_settings.cache_clear()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJwks:
        def get_signing_key_from_jwt(self, token):
            return type("SigningKey", (), {"key": key.public_key()})

    monkeypatch.setattr(auth, "_jwks_client", lambda domain: FakeJwks())

    def token(email="alice@example.com", aud="our-aud", issuer="https://team.cloudflareaccess.com", signer=key):
        claims = {"email": email, "aud": aud, "iss": issuer, "exp": int(time.time()) + 600}
        return {"Cf-Access-Jwt-Assertion": jwt.encode(claims, signer, algorithm="RS256")}
    return token


def test_production_requires_a_valid_access_token(client, production):
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/runs", headers=production(aud="someone-elses-app")).status_code == 401
    assert client.get("/api/runs", headers=production(issuer="https://evil.example")).status_code == 401
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert client.get("/api/runs", headers=production(signer=other_key)).status_code == 401
    assert client.get("/api/runs", headers=production()).status_code == 200
    assert client.get("/healthz").status_code == 200   # health checks need no login


def test_users_cannot_see_each_others_runs(client, production):
    run_id = client.post("/api/runs", json={"task": TASK}, headers=production("alice@example.com")).json()["id"]
    assert wait_for(client, run_id, production("alice@example.com"))["user"] == "alice@example.com"
    assert client.get(f"/api/runs/{run_id}", headers=production("bob@example.com")).status_code == 404
    assert client.get("/api/runs", headers=production("bob@example.com")).json() == []

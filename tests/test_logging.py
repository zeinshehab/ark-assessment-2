"""Secrets must never reach the logs, and logs must be machine-readable in production."""
import io
import json
import logging

from reviewer_loop import config, logs, mcp_client
from tests.conftest import DUMMY_GEMINI_KEY, DUMMY_GROQ_KEY


def _capture(monkeypatch, production=True):
    if production:
        monkeypatch.setattr(config.Settings, "production", property(lambda self: True))
    stream = io.StringIO()
    logs.setup_logging(config.get_settings(), stream=stream)
    return stream


def test_secrets_are_redacted_from_message_extras_and_tracebacks(monkeypatch):
    stream = _capture(monkeypatch)
    log = logging.getLogger("test")
    log.warning(f"calling with key {DUMMY_GROQ_KEY}", extra={"detail": f"key={DUMMY_GEMINI_KEY}"})
    try:
        raise RuntimeError(f"401 Unauthorized for key {DUMMY_GEMINI_KEY}")
    except RuntimeError:
        log.exception("provider failed")
    output = stream.getvalue()
    assert DUMMY_GROQ_KEY not in output and DUMMY_GEMINI_KEY not in output
    assert output.count(logs.REDACTED) >= 3


def test_unknown_key_shaped_strings_are_redacted(monkeypatch):
    """A key we were never configured with (e.g. pasted into an error by a provider) is still masked."""
    stream = _capture(monkeypatch)
    logging.getLogger("test").error("bad key gsk_SomeOtherKey1234567890 and AIzaSyAnotherOne_1234567890")
    output = stream.getvalue()
    assert "gsk_SomeOtherKey" not in output and "AIzaSyAnother" not in output


def test_production_logs_are_json_with_run_id(monkeypatch):
    stream = _capture(monkeypatch)
    token = logs.current_run_id.set("run-123")
    try:
        logging.getLogger("test").info("provider call", extra={"provider": "groq", "latency_ms": 812})
    finally:
        logs.current_run_id.reset(token)
    entry = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert entry["event"] == "provider call"
    assert entry["run_id"] == "run-123" and entry["provider"] == "groq" and entry["latency_ms"] == 812


def test_mcp_server_gets_our_settings_and_run_id(monkeypatch):
    """The SDK starts the server with only PATH/HOME-like variables; without ours it would run as development."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("UNRELATED_SECRET", "should-not-be-passed")
    token = logs.current_run_id.set("run-9")
    try:
        env = mcp_client._server_env()
    finally:
        logs.current_run_id.reset(token)
    assert env["APP_ENV"] == "production" and env["RUN_ID"] == "run-9"
    assert env["GEMINI_KEY"] == DUMMY_GEMINI_KEY
    assert "UNRELATED_SECRET" not in env

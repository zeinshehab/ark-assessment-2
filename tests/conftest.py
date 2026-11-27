"""Shared test setup: every test runs with dummy keys and a throw-away data directory.

Environment variables take priority over the developer's `.env`, so tests can never use (or spend) real keys.
"""
import logging

import pytest

from reviewer_loop import config
from reviewer_loop.breaker import BREAKERS

DUMMY_GROQ_KEY = "gsk_test_dummy_groq_key_0123456789"
DUMMY_GEMINI_KEY = "AIzaTestDummyGeminiKey0123456789"


@pytest.fixture(autouse=True)
def test_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("GROQ_KEY", DUMMY_GROQ_KEY)
    monkeypatch.setenv("GEMINI_KEY", DUMMY_GEMINI_KEY)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_CACHE_ENABLED", "false")
    config.get_settings.cache_clear()
    for breaker in BREAKERS.values():  # breakers are process-wide; start every test with them closed
        breaker.failures, breaker.opened_at, breaker.last_error_type = 0, None, None
    yield config.get_settings()
    config.get_settings.cache_clear()
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_ours", False)]:  # undo setup_logging()
        root.removeHandler(handler)

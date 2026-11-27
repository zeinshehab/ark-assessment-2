"""Logging for the app and the MCP server: one place that decides format and redaction.

  * production: one JSON object per line (easy to search and to turn into alerts)
  * development: readable text lines
  * every record passes a redaction filter: the configured key values and anything shaped like a Groq or
    Google key are replaced with [REDACTED], in the message, in extra fields and in exception tracebacks
  * the current run id is added automatically (a context variable set by the job runner)

Everything goes to stderr. Inside the MCP server stdout is the protocol channel, so stderr is the only safe
choice there, and using it everywhere keeps one rule. Docker collects both streams.

What we log: events, ids, providers, models, latencies, outcomes, error types and sizes.
What we never log: API keys, the user's task text, generated code (they may contain business data).
"""
import contextvars
import json
import logging
import re
import sys
import time

current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)

_KEY_SHAPES = re.compile(r"gsk_[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_\-]{16,}")
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}
REDACTED = "[REDACTED]"


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 8]

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, REDACTED)
        return _KEY_SHAPES.sub(REDACTED, text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = self.redact(record.getMessage()), None
        if record.exc_info:
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        for key, value in list(vars(record).items()):
            if key not in _STANDARD_ATTRS and isinstance(value, str):
                setattr(record, key, self.redact(value))
        if getattr(record, "run_id", None) is None and current_run_id.get():
            record.run_id = current_run_id.get()
        return True


def _extras(record: logging.LogRecord) -> dict:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS and not k.startswith("_")}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
                 "level": record.levelname, "logger": record.name, "event": record.getMessage(), **_extras(record)}
        if record.exc_text:
            entry["exc"] = record.exc_text
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(f"{k}={v}" for k, v in _extras(record).items())
        line = f"{time.strftime('%H:%M:%S')} {record.levelname:<7} {record.name}: {record.getMessage()} {extras}"
        return line.rstrip() + (f"\n{record.exc_text}" if record.exc_text else "")


def setup_logging(settings, stream=None) -> logging.Handler:
    """Configure the root logger. Safe to call more than once (it replaces our handler)."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if settings.production else TextFormatter())
    handler.addFilter(RedactingFilter(settings.secret_values()))
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, "_ours", False)]:
        root.removeHandler(old)
    handler._ours = True
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    for noisy in ("httpx", "httpcore", "google_genai", "mcp", "uvicorn.access"):  # per-request chatter
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return handler


def redact(text: str) -> str:
    """Redact secrets from text that leaves the process another way (e.g. an error message in a run record)."""
    from . import config
    return RedactingFilter(config.get_settings().secret_values()).redact(text)

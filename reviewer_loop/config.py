"""Application configuration.

Two kinds of values live here:
  * Settings: everything an operator may need to change (models, limits, timeouts, paths, secrets). They come
    from environment variables, validated by pydantic-settings, with safe defaults.
  * Constants: facts about the code itself (repository paths, which lint rules we run). Not configuration.

Environments (APP_ENV):
  * development (default): also reads the repository's `.env` file; text logs; no login; LLM cache on.
  * production: never reads `.env`. Secrets come from files in SECRETS_DIR (Docker secrets, /run/secrets),
    JSON logs, Cloudflare Access login required, LLM cache off unless enabled explicitly.

Invalid or missing configuration raises ConfigError at startup. Its message names the variables but never
contains their values, so a typo in a key can't leak the key into logs.
"""
import functools
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------- constants (code, not configuration) ----------

ROOT = Path(__file__).resolve().parent.parent
STANDARD_PATH = ROOT / "standards" / "coding_standard.md"
TASKS_DIR = ROOT / "benchmark" / "tasks"
RESULTS_DIR = ROOT / "results"  # committed benchmark results (read-only in the container)

# Ruff rules: pyflakes (F), syntax (E9), common style errors (E4, E7), bugbear (B), bandit security (S).
RUFF_RULES = "E4,E7,E9,F,B,S"
# Lint codes with these prefixes are treated as blocking; anything else is advisory.
BLOCKING_LINT_PREFIXES = ("E9", "F", "B", "S")


class ConfigError(Exception):
    """The configuration is missing or invalid. The message never includes secret values."""


# ---------- settings ----------

class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["development", "production"] = "development"
    app_version: str = "dev"          # set to the git SHA at image build time; shown in /api/status and logs
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Secrets (SecretStr prints as '**********', so they can't leak through repr() or logs)
    groq_key: SecretStr
    gemini_key: SecretStr

    # Models
    generator_model: str = "openai/gpt-oss-20b"
    reviewer_model: str = "gemini-3.5-flash-lite"
    reviewer_fallback_model: str = "gemini-3.1-flash-lite"  # used if the main reviewer is overloaded

    # Review loop
    max_iterations: int = Field(4, ge=1, le=6)           # max review rounds per task
    test_timeout_s: int = Field(15, ge=1, le=120)        # generated tests are killed after this

    # Providers: timeouts, retries, free-tier protection
    provider_timeout_s: float = Field(60, gt=0, le=300)  # one HTTP call to a provider
    max_provider_retries: int = Field(4, ge=1, le=8)     # attempts per call; waits 5s, 10s, 20s between them
    groq_tokens_per_minute: int = Field(8_000, gt=0)
    groq_daily_token_budget: int = Field(190_000, gt=0)  # per model: refuse Groq calls above this
    gemini_min_seconds_between_calls: float = Field(4.5, ge=0)  # 15 requests/minute
    breaker_failures: int = Field(3, ge=1)               # consecutive failures that open a provider's breaker
    breaker_cooldown_s: float = Field(60, gt=0)          # how long an open breaker fails fast
    llm_cache_enabled: bool | None = None                # default: on in development, off in production

    # Runs and abuse limits (the API)
    run_deadline_s: float = Field(600, gt=0)             # wall-clock budget for one review run
    max_concurrent_runs: int = Field(1, ge=1, le=4)      # free-tier limits are shared, so 1 by default
    max_queued_runs: int = Field(5, ge=0)
    runs_per_user_per_hour: int = Field(10, ge=1)
    max_task_chars: int = Field(4000, ge=100)
    allow_demo_injection: bool | None = None             # the "inject a wrong finding" demo switch

    # Storage: runs, LLM cache and daily usage tally (a Docker volume in production)
    data_dir: Path = ROOT / ".data"

    # Cloudflare Access (production login)
    cf_access_team_domain: str = ""   # e.g. myteam.cloudflareaccess.com
    cf_access_aud: str = ""           # the Access application's audience tag

    # Who users should contact when a run fails (shown in the UI's error panel). Required in production.
    operator_contact: str = ""

    @field_validator("groq_key", "gemini_key")
    @classmethod
    def _not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def _environment_defaults(self):
        production = self.app_env == "production"
        if self.llm_cache_enabled is None:
            self.llm_cache_enabled = not production
        if self.allow_demo_injection is None:
            self.allow_demo_injection = not production
        if production and not (self.cf_access_team_domain and self.cf_access_aud):
            raise ValueError("CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD are required in production")
        if production and not self.operator_contact.strip():
            raise ValueError("OPERATOR_CONTACT is required in production (who users contact when a run fails)")
        return self

    @property
    def production(self) -> bool:
        return self.app_env == "production"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    def secret_values(self) -> list[str]:
        """Used by the log redaction filter."""
        return [s.get_secret_value() for s in (self.groq_key, self.gemini_key)]


def load_settings() -> Settings:
    """Build Settings for the current APP_ENV, turning validation errors into a ConfigError without values."""
    env = os.environ.get("APP_ENV", "development")
    if env == "production":
        secrets_dir = Path(os.environ.get("SECRETS_DIR", "/run/secrets"))
        sources = {"_env_file": None, "_secrets_dir": secrets_dir if secrets_dir.is_dir() else None}
    else:
        sources = {"_env_file": ROOT / ".env"}
    try:
        return Settings(**sources)
    except ValidationError as exc:
        # Only the location and the message: pydantic's default text would include the input values.
        problems = [f"{'.'.join(map(str, e['loc'])).upper() or 'SETTINGS'}: {e['msg']}" for e in exc.errors()]
        raise ConfigError("Invalid configuration: " + "; ".join(problems)) from None


@functools.cache
def get_settings() -> Settings:
    """The process-wide settings, loaded once. Tests call get_settings.cache_clear() after changing env vars."""
    return load_settings()

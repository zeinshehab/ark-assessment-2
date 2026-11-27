"""Configuration errors: the app must refuse to start with bad config, and say why without leaking secrets."""
import pytest

from reviewer_loop import config
from tests.conftest import DUMMY_GEMINI_KEY, DUMMY_GROQ_KEY


def _production(monkeypatch, tmp_path, **files):
    """Switch to production with secrets read from files in a temp SECRETS_DIR."""
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name, value in files.items():
        (secrets / name).write_text(value)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRETS_DIR", str(secrets))
    monkeypatch.delenv("GROQ_KEY")
    monkeypatch.delenv("GEMINI_KEY")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "team.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "aud-tag")
    monkeypatch.setenv("OPERATOR_CONTACT", "ops@example.com")


def test_production_reads_secret_files_and_switches_defaults(monkeypatch, tmp_path):
    _production(monkeypatch, tmp_path, groq_key=DUMMY_GROQ_KEY, gemini_key=DUMMY_GEMINI_KEY)
    monkeypatch.delenv("LLM_CACHE_ENABLED")
    settings = config.load_settings()
    assert settings.production
    assert settings.groq_key.get_secret_value() == DUMMY_GROQ_KEY
    assert settings.llm_cache_enabled is False       # production default
    assert settings.allow_demo_injection is False    # production default
    assert DUMMY_GROQ_KEY not in repr(settings)      # SecretStr hides values


def test_missing_secret_in_production_names_it_without_leaking_the_other(monkeypatch, tmp_path):
    _production(monkeypatch, tmp_path, gemini_key=DUMMY_GEMINI_KEY)   # groq_key file missing
    with pytest.raises(config.ConfigError) as err:
        config.load_settings()
    assert "GROQ_KEY" in str(err.value)
    assert DUMMY_GEMINI_KEY not in str(err.value)


def test_production_ignores_dotenv(monkeypatch, tmp_path):
    """Even if a .env file is lying around, production must not read keys from it."""
    _production(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(f"GROQ_KEY={DUMMY_GROQ_KEY}\nGEMINI_KEY={DUMMY_GEMINI_KEY}\n")
    with pytest.raises(config.ConfigError, match="GROQ_KEY"):
        config.load_settings()


def test_production_requires_cloudflare_access_settings(monkeypatch, tmp_path):
    _production(monkeypatch, tmp_path, groq_key=DUMMY_GROQ_KEY, gemini_key=DUMMY_GEMINI_KEY)
    monkeypatch.delenv("CF_ACCESS_AUD")
    with pytest.raises(config.ConfigError, match="CF_ACCESS"):
        config.load_settings()


@pytest.mark.parametrize("name,value", [("MAX_ITERATIONS", "0"), ("PROVIDER_TIMEOUT_S", "-1"),
                                        ("APP_ENV", "staging"), ("GROQ_KEY", "   ")])
def test_invalid_values_are_rejected_by_name(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(config.ConfigError) as err:
        config.load_settings()
    assert name in str(err.value)
    assert DUMMY_GEMINI_KEY not in str(err.value)


def test_production_requires_an_operator_contact(monkeypatch, tmp_path):
    """Added in a release after the first deployment: hosts without it must refuse to start (the drift demo)."""
    _production(monkeypatch, tmp_path, groq_key=DUMMY_GROQ_KEY, gemini_key=DUMMY_GEMINI_KEY)
    monkeypatch.delenv("OPERATOR_CONTACT")
    with pytest.raises(config.ConfigError, match="OPERATOR_CONTACT"):
        config.load_settings()

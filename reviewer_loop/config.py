import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GROQ_KEY = os.getenv("GROQ_KEY", "")
GEMINI_KEY = os.getenv("GEMINI_KEY", "")

GENERATOR_MODEL = "openai/gpt-oss-20b"
REVIEWER_MODEL = "gemini-3.5-flash-lite"
REVIEWER_FALLBACK_MODEL = "gemini-3.1-flash-lite"  # used if the main reviewer is overloaded 

MAX_ITERATIONS = 4          # max review rounds per task
TEST_TIMEOUT_S = 15         # generated tests are killed after this many seconds

# Free-tier protection
GROQ_TOKENS_PER_MINUTE = 8_000
GROQ_DAILY_TOKEN_BUDGET = 190_000   # per model: refuse Groq calls above this
GEMINI_MIN_SECONDS_BETWEEN_CALLS = 4.5  # 15 requests/minute
MAX_PROVIDER_RETRIES = 4         # waits 5s, 10s, 20s between attempts

CACHE_DIR = ROOT / ".cache"
RESULTS_DIR = ROOT / "results"
STANDARD_PATH = ROOT / "standards" / "coding_standard.md"
TASKS_DIR = ROOT / "benchmark" / "tasks"

# Ruff rules: pyflakes (F), syntax (E9), common style errors (E4, E7), bugbear (B), bandit security (S).
RUFF_RULES = "E4,E7,E9,F,B,S"
# Lint codes with these prefixes are treated as blocking; anything else is advisory.
BLOCKING_LINT_PREFIXES = ("E9", "F", "B", "S")

"""Static analysis with ruff. Ruff only parses the code and never executes it, so no sandbox is needed."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config


def run_ruff(solution_code: str, test_code: str) -> dict:
    workdir = tempfile.mkdtemp(prefix="ruff_")
    try:
        Path(workdir, "solution.py").write_text(solution_code, encoding="utf-8")
        Path(workdir, "test_solution.py").write_text(test_code, encoding="utf-8")
        cmd = [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", "--output-format=json",
               f"--select={config.RUFF_RULES}",
               "--per-file-ignores=test_solution.py:S101",  # `assert` is how pytest tests are written
               "solution.py", "test_solution.py"]
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=30, check=False)
        raw = json.loads(proc.stdout or "[]")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    issues = []
    for item in raw:
        code = item["code"] or "invalid-syntax"  # ruff reports syntax errors without a rule code
        issues.append({
            "code": code,
            "message": item["message"],
            "file": Path(item["filename"]).name,
            "line": item["location"]["row"],
            "blocking": code == "invalid-syntax" or code.startswith(config.BLOCKING_LINT_PREFIXES),
        })
    blocking = sum(i["blocking"] for i in issues)
    return {"status": "clean" if blocking == 0 else "issues", "blocking_count": blocking,
            "advisory_count": len(issues) - blocking, "issues": issues}

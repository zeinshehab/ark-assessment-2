"""Runs untrusted, model-written code in a constrained subprocess and returns structured results.

Constraints (simple, explainable, no Docker needed):
  * a fresh temporary directory per run, deleted afterwards
  * a separate `python -I` process (isolated mode: ignores PYTHON* env vars and user site-packages)
  * a minimal environment: API keys and other secrets are NOT passed to the child
  * a hard timeout: hanging code is killed and reported as status "timeout"
  * an audit hook (sandbox_conftest.py) blocking network, subprocesses and writes outside the dir
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config

_CONFTEST = Path(__file__).with_name("sandbox_conftest.py")


def _minimal_env(workdir: str) -> dict:
    env = {"TEMP": workdir, "TMP": workdir, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    for key in ("SYSTEMROOT", "PATH"):  # Windows needs SYSTEMROOT to start Python at all
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _parse_junit(report: Path) -> list[dict]:
    tests = []
    for case in ET.parse(report).getroot().iter("testcase"):
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            node = case.find(tag)
            if node is not None:
                outcome = {"failure": "failed"}.get(tag, tag)
                message = (node.get("message") or node.text or "")[:500]
        tests.append({"name": case.get("name"), "outcome": outcome, "message": message})
    return tests


def run_tests(solution_code: str, test_code: str, test_filename: str = "test_solution.py",
              timeout_s: int = config.TEST_TIMEOUT_S) -> dict:
    """Run pytest on solution.py + a test file. Returns a JSON-serialisable result dict."""
    workdir = tempfile.mkdtemp(prefix="sandbox_")
    try:
        Path(workdir, "solution.py").write_text(solution_code, encoding="utf-8")
        Path(workdir, test_filename).write_text(test_code, encoding="utf-8")
        shutil.copy(_CONFTEST, Path(workdir, "conftest.py"))
        # An ini file here stops pytest from searching parent directories for configuration.
        Path(workdir, "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        cmd = [sys.executable, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider",
               "--junitxml=report.xml", test_filename]
        try:
            proc = subprocess.run(cmd, cwd=workdir, env=_minimal_env(workdir), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "passed": 0, "failed": 0, "tests": [],
                    "output": f"Killed after {timeout_s}s (infinite loop or blocking call?)"}

        # Drop timings ("in 0.08s") so identical code gives identical output: keeps the LLM cache effective.
        output = re.sub(r" in \d+\.\d+s", "", proc.stdout + proc.stderr)[-2000:]
        report = Path(workdir, "report.xml")
        tests = _parse_junit(report) if report.exists() else []
        passed = sum(t["outcome"] == "passed" for t in tests)
        failed = sum(t["outcome"] in ("failed", "error") for t in tests)
        if proc.returncode == 0 and tests:
            status = "passed"
        elif failed:
            status = "failed"
        else:  # nothing ran: syntax error, import error, no tests collected...
            status = "error"
        return {"status": status, "passed": passed, "failed": failed, "tests": tests, "output": output}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

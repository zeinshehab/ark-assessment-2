"""Benchmark runner and report builder.

  python -m benchmark.run_benchmark --verify            # hidden tests: reference passes, buggy fails (0 tokens)
  python -m benchmark.run_benchmark                     # run every task not yet run (uses API quota)
  python -m benchmark.run_benchmark --tasks median,paginate [--force]
  python -m benchmark.run_benchmark --report            # rebuild results/report.md from saved runs (0 tokens)
  python -m benchmark.run_benchmark --rescore           # re-run hidden tests on saved runs, then report (0 tokens)

Baseline = the generator's first draft (generator only, no review). The orchestrated result = the final code.
Both are scored with HIDDEN tests that the loop never sees.
"""
import argparse
import asyncio
import json
import time

from reviewer_loop import config, llm, orchestrator, sandbox, static_analysis
from reviewer_loop.mcp_client import ToolClient

RUNS_DIR = config.RESULTS_DIR / "runs"
LABELS_PATH = config.ROOT / "benchmark" / "labels.json"


def load_tasks() -> list[dict]:
    tasks = []
    for entry in json.loads((config.TASKS_DIR / "index.json").read_text(encoding="utf-8")):
        folder = config.TASKS_DIR / entry["id"]
        tasks.append({**entry, **{name: (folder / file).read_text(encoding="utf-8") for name, file in (
            ("prompt", "prompt.md"), ("hidden_tests", "test_hidden.py"), ("reference", "reference.py"),
            ("buggy", "buggy.py"))}})
    return tasks


def hidden_score(solution_code: str | None, hidden_tests: str, expected_total: int | None = None) -> dict | None:
    """Score code with the hidden tests. `expected_total` (the reference solution's test count) keeps the total
    honest when the code doesn't even import: that is 0 of N tests, not 0 of 1 collection error."""
    if solution_code is None:
        return None
    result = sandbox.run_tests(solution_code, hidden_tests, test_filename="test_hidden.py")
    total = max(result["passed"] + result["failed"], expected_total or 0)
    return {"all_passed": result["status"] == "passed", "passed": result["passed"], "total": total,
            "failing": [t["name"] for t in result["tests"] if t["outcome"] != "passed"]}


def evaluate(task: dict, record: dict) -> dict:
    """Score the baseline (first draft) and the final code against the hidden tests, plus blocking lint counts."""
    expected = hidden_score(task["reference"], task["hidden_tests"])["total"]
    baseline, final = record.get("baseline"), record.get("final")
    return {
        "baseline": hidden_score(baseline and baseline["solution_code"], task["hidden_tests"], expected),
        "final": hidden_score(final and final["solution_code"], task["hidden_tests"], expected),
        "baseline_lint_blocking": static_analysis.run_ruff(**baseline)["blocking_count"] if baseline else None,
        "final_lint_blocking": static_analysis.run_ruff(**final)["blocking_count"] if final else None,
    }


def rescore(tasks: list[dict]) -> None:
    """Re-run the hidden tests on saved runs (no API calls)."""
    for task in tasks:
        path = RUNS_DIR / f"bench_{task['id']}.json"
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            record["evaluation"] = evaluate(task, record)
            orchestrator.save_run(record, f"bench_{task['id']}", RUNS_DIR)


def verify(tasks: list[dict]) -> None:
    for task in tasks:
        ref, bug = hidden_score(task["reference"], task["hidden_tests"]), hidden_score(task["buggy"], task["hidden_tests"])
        ok = ref["all_passed"] and not bug["all_passed"]
        print(f"{'OK ' if ok else 'BAD'} {task['id']:15} reference {ref['passed']}/{ref['total']}  "
              f"buggy {bug['passed']}/{bug['total']}  (buggy fails: {bug['failing']})")


async def run_tasks(tasks: list[dict], force: bool) -> None:
    for task in tasks:
        path = RUNS_DIR / f"bench_{task['id']}.json"
        if path.exists() and not force:
            print(f"skip {task['id']} (already run)")
            continue
        start = time.time()
        async with ToolClient() as tools:
            record = await orchestrator.run_task(task["prompt"], tools, task_id=task["id"])
        record["evaluation"] = evaluate(task, record)
        orchestrator.save_run(record, f"bench_{task['id']}", RUNS_DIR)
        ev = record["evaluation"]
        print(f"{task['id']:15} {record['status']:10} rounds={len(record['iterations'])} "
              f"baseline={ev['baseline'] and ev['baseline']['passed']}/{ev['baseline'] and ev['baseline']['total']} "
              f"final={ev['final'] and ev['final']['passed']}/{ev['final'] and ev['final']['total']} "
              f"({time.time() - start:.0f}s, {config.get_settings().generator_model} today: {llm.tokens_used_today(config.get_settings().generator_model)})", flush=True)


# ---------------- report ----------------

def _outcome(ev: dict) -> str:
    base, final = ev["baseline"], ev["final"]
    if base is None or final is None:
        return "n/a (error)"
    if final["passed"] < base["passed"] or (ev["final_lint_blocking"] or 0) > (ev["baseline_lint_blocking"] or 0):
        return "MADE WORSE"
    if not base["all_passed"] and final["all_passed"]:
        return "defect fixed"
    if not base["all_passed"]:
        return "improved" if final["passed"] > base["passed"] else "defect missed"
    return "draft passed hidden tests"


def build_report(tasks: list[dict]) -> str:
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8")) if LABELS_PATH.exists() else {}
    traps = {t["id"]: t["trap"] for t in tasks}
    rows, all_findings = [], []
    for task in tasks:
        path = RUNS_DIR / f"bench_{task['id']}.json"
        if not path.exists():
            continue
        run = json.loads(path.read_text(encoding="utf-8"))
        ev = run["evaluation"]
        statuses = [f["status"] for f in run["findings"]]
        for f in run["findings"]:
            all_findings.append({**f, "task": task["id"], "label": labels.get(f"{task['id']}:{f['id']}")})
        usage = run["usage"]
        rows.append({
            "task": task["id"], "trap": traps[task["id"]], "status": run["status"], "rounds": len(run["iterations"]),
            "baseline": ev["baseline"], "final": ev["final"], "outcome": _outcome(ev),
            "findings": len(statuses), "blocking": sum(f["severity"] in ("critical", "major") for f in run["findings"]),
            "on_solution": sum(f["file"] == "solution.py" for f in run["findings"]),
            "declined_or_disputed": sum(s in ("withdrawn", "disputed") for s in statuses),
            "reverted": statuses.count("reverted"), "late": statuses.count("late_objection"),
            "gen_tokens": usage["generator"].get("prompt_tokens", 0) + usage["generator"].get("completion_tokens", 0),
            "rev_tokens": usage["reviewer"].get("prompt_tokens", 0) + usage["reviewer"].get("completion_tokens", 0),
            "gen_calls": usage["generator"].get("calls", 0), "rev_calls": usage["reviewer"].get("calls", 0),
        })

    scored = [r for r in rows if r["baseline"] and r["final"]]
    base_defects = [r for r in scored if not r["baseline"]["all_passed"]]
    fixed = [r for r in base_defects if r["final"]["all_passed"]]
    final_defects = [r for r in scored if not r["final"]["all_passed"]]
    worse = [r for r in rows if r["outcome"] == "MADE WORSE"]
    converged = [r for r in rows if r["status"] == "converged"]
    labelled = [f for f in all_findings if f["label"]]
    false_pos = [f for f in labelled if not f["label"]["real"]]
    trap_flagged = [f for f in labelled if f["label"].get("targets_trap")]

    def pct(a, b):
        return f"{a}/{b} ({100 * a / b:.0f}%)" if b else f"{a}/{b}"

    lines = [
        "# Benchmark results", "",
        (f"Generator: `{config.get_settings().generator_model}` (Groq) · Reviewer: `{config.get_settings().reviewer_model}` (Google) · "
         f"Tasks run: {len(rows)}"), "",
        ("Baseline = the generator's first draft (no review). Orchestrated = the final code after the review loop. "
         "Both are scored with hidden tests that the loop never sees."), "",
        "## Summary", "",
        "| Metric | Value |", "|---|---|",
        f"| Tasks passing all hidden tests: baseline (generator only) | {pct(len(scored) - len(base_defects), len(scored))} |",
        f"| Tasks passing all hidden tests: orchestrated | {pct(len(scored) - len(final_defects), len(scored))} |",
        f"| Hidden tests passed: baseline | {pct(sum(r['baseline']['passed'] for r in scored), sum(r['baseline']['total'] for r in scored))} |",
        f"| Hidden tests passed: orchestrated | {pct(sum(r['final']['passed'] for r in scored), sum(r['final']['total'] for r in scored))} |",
        f"| Defect detection rate (baseline defects fixed by the loop) | {pct(len(fixed), len(base_defects))} |",
        f"| Tasks made worse by orchestration | {len(worse)}: {', '.join(r['task'] for r in worse) or 'none'} |",
        f"| Converged | {pct(len(converged), len(rows))} |",
        f"| Mean review rounds (converged tasks) | {sum(r['rounds'] for r in converged) / len(converged):.1f} |" if converged else "| Mean review rounds | n/a |",
        f"| Did not converge | {', '.join(f'{r['task']} ({r['status']})' for r in rows if r['status'] != 'converged') or 'none'} |",
        f"| Review findings raised | {len(all_findings)} ({sum(f['severity'] in ('critical', 'major') for f in all_findings)} blocking) |",
        f"| False positives (hand-labelled) | {pct(len(false_pos), len(labelled))} of labelled findings |",
        f"| Findings that targeted the planted trap | {len(trap_flagged)} |",
        f"| Tokens: generator / reviewer (total) | {sum(r['gen_tokens'] for r in rows):,} / {sum(r['rev_tokens'] for r in rows):,} |",
        "", "## Per task", "",
        "How to read this table:",
        "- **Rounds**: review rounds. 1 = the first draft was accepted as is.",
        "- **Hidden: baseline → final**: hidden tests passed by the first draft → by the final code.",
        ("- **Outcome**: compares *only the solution's behaviour* with the hidden tests. \"Draft passed hidden tests\" "
        "means extra rounds, if any, were spent on other problems (usually the generator's own tests, or lint)."),
        "- **Findings (blocking)**: all review findings (critical/major ones block convergence until resolved).",
        "- **On solution / on tests**: whether findings targeted solution.py or the generator's test_solution.py.",
        "- **Withdrawn/disputed, Reverted, Late**: how often each disagreement mechanism fired (see README, Key design decisions).",
        "",
        "| Task | Trap | Status | Rounds | Hidden: baseline → final | Outcome | Findings (blocking) | On solution / on tests | Withdrawn/disputed | Reverted | Late | Gen tokens (calls) | Rev tokens (calls) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        b, f = r["baseline"], r["final"]
        hidden = f"{b['passed']}/{b['total']} → {f['passed']}/{f['total']}" if b and f else "n/a"
        lines.append(f"| {r['task']} | {r['trap']} | {r['status']} | {r['rounds']} | {hidden} | {r['outcome']} | "
                     f"{r['findings']} ({r['blocking']}) | {r['on_solution']} / {r['findings'] - r['on_solution']} | {r['declined_or_disputed']} | {r['reverted']} | {r['late']} | "
                     f"{r['gen_tokens']:,} ({r['gen_calls']}) | {r['rev_tokens']:,} ({r['rev_calls']}) |")
    lines += ["", "## All findings and their disposition", "",
              ("Title = the reviewer's one-line headline. Label = a hand judgement of whether the finding was a real "
              "problem (`benchmark/labels.json`, one reason each); it is what the false-positive rate is computed from."), "",
              "| Task | ID | Severity | Location | Title | Disposition | Label |", "|---|---|---|---|---|---|---|"]
    for f in all_findings:
        label = ("real" if f["label"]["real"] else "FALSE POSITIVE") + (f" ({f['label'].get('note', '')})" if f["label"].get("note") else "") if f["label"] else "unlabelled"
        title = f["title"].replace("|", "/")
        lines.append(f"| {f['task']} | {f['id']} | {f['severity']} | {f['file']}:{f['line']} | {title} | {f['status']} | {label} |")

    report = "\n".join(lines) + "\n"
    (config.RESULTS_DIR / "report.md").write_text(report, encoding="utf-8")
    (config.RESULTS_DIR / "benchmark_results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--rescore", action="store_true", help="re-score saved runs with the hidden tests (no API calls)")
    parser.add_argument("--tasks", help="comma-separated task ids (default: all)")
    parser.add_argument("--force", action="store_true", help="re-run tasks that already have results")
    args = parser.parse_args()
    all_tasks = load_tasks()
    selected = [t for t in all_tasks if not args.tasks or t["id"] in args.tasks.split(",")]
    if args.verify:
        verify(selected)
    elif args.rescore:
        rescore(selected)
        build_report(all_tasks)
        print("rescored; report written to results/report.md")
    elif args.report:
        build_report(all_tasks)
        print("report written to results/report.md")
    else:
        asyncio.run(run_tasks(selected, args.force))
        build_report(all_tasks)
        print("report written to results/report.md")

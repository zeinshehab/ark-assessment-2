"""Plain Streamlit UI.  Run with:  streamlit run app.py

Shows the task, each iteration's diff, the review findings and their disposition, the MCP tool calls and the
final result. Runs execute to completion and are then displayed (streaming is out of scope).
"""
import asyncio
import json
import time

import pandas as pd
import streamlit as st

from benchmark.run_benchmark import load_tasks
from reviewer_loop import config, orchestrator

st.set_page_config(page_title="Multi-model code review", layout="wide")
RUNS_DIR = config.RESULTS_DIR / "runs"


def show_run(run: dict) -> None:
    st.subheader(f"Status: {run['status'].upper()}")
    if run.get("error"):
        st.error(run["error"])
    st.markdown(f"**Task** (`{run['task_id']}`)")
    st.info(run["task"])
    gen, rev = run["usage"]["generator"], run["usage"]["reviewer"]
    cols = st.columns(4)
    cols[0].metric("Review rounds", len(run["iterations"]))
    cols[1].metric("Findings", len(run["findings"]))
    cols[2].metric(f"Generator tokens ({run['generator_model']})", gen.get("prompt_tokens", 0) + gen.get("completion_tokens", 0))
    cols[3].metric(f"Reviewer tokens ({run['reviewer_model']})", rev.get("prompt_tokens", 0) + rev.get("completion_tokens", 0))
    if run.get("escalated_to_human"):
        st.warning(f"Disputed findings escalated to a human (the models still disagree): {', '.join(run['escalated_to_human'])}")
    if run.get("inject_wrong_finding"):
        st.warning("A deliberately wrong finding was injected in round 1 (marked [INJECTED]) to show the system declining it.")

    tab_iter, tab_find, tab_tools, tab_final = st.tabs(["Iterations", "Findings & disposition", "MCP tool calls", "Final result"])
    with tab_iter:
        for step in run["iterations"]:
            tests, lint = step["test_results"], step["lint_results"]
            title = (f"Round {step['iteration']}: tests {tests['status']} ({tests['passed']} passed, {tests['failed']} failed), "
                     f"lint {lint['status']}, new findings {len(step['new_findings'])}")
            with st.expander(title, expanded=True):
                if step["reverted"]:
                    st.error(f"Revision rejected by the regression guard and rolled back: {step['revert_reason']}")
                    st.code(orchestrator.make_diff(step["code"], step["rejected_code"]) or "(no diff)", language="diff")
                if step["iteration"] == 1:
                    st.markdown("**Generator's first draft (baseline)**")
                    st.code(step["code"]["solution_code"], language="python")
                    with st.expander("Draft tests"):
                        st.code(step["code"]["test_code"], language="python")
                elif step["diff"]:
                    st.markdown("**Diff vs. the previous round**")
                    st.code(step["diff"], language="diff")
                else:
                    st.markdown("_Code unchanged since the previous round._")
                if tests["failed"]:
                    st.markdown("**Failing tests** · from the `run_tests` MCP tool (pytest in the sandbox)")
                    st.dataframe(pd.DataFrame([t for t in tests["tests"] if t["outcome"] != "passed"]), hide_index=True)
                if lint["issues"]:
                    st.markdown("**Static analysis issues** · from the `run_static_analysis` MCP tool (ruff)")
                    st.dataframe(pd.DataFrame(lint["issues"]), hide_index=True)
                st.markdown(f"**Reviewer's summary** · from the `review_code` MCP tool (Gemini): {step['review_summary']}")
                st.caption(f"Coding-standard sections retrieved for this review: {', '.join(step['standard_sections']) or 'none'}")
                if step.get("generator_decisions"):
                    st.markdown("**Generator's accept/decline decisions** · from the generator (Groq)")
                    st.dataframe(pd.DataFrame(step["generator_decisions"]), hide_index=True)
    with tab_find:
        st.caption("type: critical/major findings are blocking (they must be resolved to converge), minor/nit are "
                   "advisory · disposition: fixed = accepted and kept · withdrawn = generator declined and the reviewer "
                   "agreed · disputed = still disagreed, escalated to a human · reverted = accepting it made the code "
                   "worse, rolled back · late_objection = new objection to unchanged code, downgraded to advisory · "
                   "[INJECTED] = the deliberately wrong demo finding")
        rows = [{"id": f["id"], "round": f["iteration"], "severity": f["severity"],
                 "type": "blocking" if f["severity"] in ("critical", "major") else "advisory",
                 "where": f"{f['file']}:{f['line']}", "finding": f["title"], "disposition": f["status"],
                 "last reason": f["history"][-1]["reason"]} for f in run["findings"]]
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            for f in run["findings"]:
                with st.expander(f"{f['id']}: {f['title']}"):
                    st.write(f"**Rationale:** {f['rationale']}")
                    st.write(f"**Suggested fix:** {f['suggested_fix']}")
                    st.write(f"**Evidence:** {f.get('evidence') or '-'} · **Standard:** {f.get('standard_ref') or '-'}")
                    st.dataframe(pd.DataFrame(f["history"]), hide_index=True)
        else:
            st.write("No findings.")
    with tab_tools:
        st.dataframe(pd.DataFrame(run["tool_calls"]), hide_index=True, use_container_width=True)
    with tab_final:
        if run.get("evaluation"):
            ev = run["evaluation"]
            st.write(f"Hidden tests: baseline {ev['baseline']['passed']}/{ev['baseline']['total']} → "
                     f"final {ev['final']['passed']}/{ev['final']['total']}")
        if run.get("final"):
            st.code(run["final"]["solution_code"], language="python")
            with st.expander("Final tests"):
                st.code(run["final"]["test_code"], language="python")


st.title("Multi-model code review orchestrator")
st.caption(f"Generator: {config.GENERATOR_MODEL} (Groq) · Reviewer: {config.REVIEWER_MODEL} (Google), via our MCP server")
page = st.sidebar.radio("Page", ["Run a task", "Saved runs", "Benchmark report"])

if page == "Run a task":
    tasks = {t["id"]: t["prompt"] for t in load_tasks()}
    choice = st.selectbox("Benchmark task (or write your own below)", ["(custom)"] + list(tasks))
    task_text = st.text_area("Task", value=tasks.get(choice, ""), height=150)
    max_rounds = st.slider("Max review rounds (timeout)", 1, 6, config.MAX_ITERATIONS)
    inject = st.checkbox("Inject a deliberately wrong review finding (demo of declining a finding)")
    if st.button("Run", type="primary", disabled=not task_text.strip()):
        with st.spinner("Running the generate → test → lint → review loop (rate-limited free tiers, may take a few minutes)..."):
            record = asyncio.run(orchestrator.run_with_mcp(task_text, task_id=choice if choice != "(custom)" else "custom",
                                                           max_iterations=max_rounds, inject_wrong_finding=inject))
        st.caption(f"Saved to {orchestrator.save_run(record, f'ui_{int(time.time())}')}")
        show_run(record)
elif page == "Saved runs":
    files = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        st.write("No saved runs yet.")
    else:
        selected = st.selectbox("Run", files, format_func=lambda p: p.stem)
        show_run(json.loads(selected.read_text(encoding="utf-8")))
else:
    report = config.RESULTS_DIR / "report.md"
    st.markdown(report.read_text(encoding="utf-8") if report.exists() else "No benchmark report yet.")

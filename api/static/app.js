"use strict";
/* Crosscheck UI: a tiny hash-routed page over the JSON API. No framework and no build step.
   Security rule: everything that comes from a user or a model is inserted as text (el() below never parses HTML),
   so model output such as "<script>" in a finding is displayed, not executed. */

const view = document.getElementById("view");
let pollTimer = null;
let status = null;

// ---------- helpers ----------

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    const error = new Error(problemText(body, response.status));
    error.status = response.status;
    throw error;
  }
  return body;
}

function problemText(body, code) {
  if (code === 401) return "You are not signed in. Reload the page to sign in again.";
  const detail = body && body.detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg.replace(/^Value error, /, "")).join("; ");
  if (typeof detail === "string") return detail;
  return `The server answered ${code}. Try again in a moment.`;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const tokens = (u) => (u ? (u.prompt_tokens || 0) + (u.completion_tokens || 0) : 0);
const kTokens = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
function duration(seconds) {
  if (seconds === null || seconds === undefined) return null;
  const m = Math.floor(seconds / 60), s = Math.round(seconds % 60);
  return m ? `${m} min ${s} s` : `${s} s`;
}

function show(...nodes) {
  clearTimeout(pollTimer);
  view.replaceChildren(...nodes);
}

// ---------- health strip ----------

async function refreshStatus() {
  const health = document.getElementById("health");
  try {
    status = await api("/api/status");
    const p = status.providers;
    const provider = (name, label) => el("span", { title: providerTitle(p[name]) },
      el("span", { class: `dot ${p[name].state}` }), label);
    const q = status.queue;
    health.replaceChildren(
      provider("groq", "Groq"), provider("gemini", "Gemini"),
      el("span", {}, q.running || q.waiting ? `${q.running} running, ${q.waiting} waiting` : "Queue empty"),
      el("span", { title: "Generator tokens used today / daily budget" },
        `${kTokens(status.tokens_today.generator)} of ${kTokens(status.tokens_today.generator_budget)} tokens`),
      el("span", { title: "Running version" }, `v ${status.version.slice(0, 7)}`));
  } catch (error) {
    health.replaceChildren(el("span", {}, el("span", { class: "dot down" }), "Service unreachable"));
  }
}

function providerTitle(p) {
  if (p.state === "closed") return "Healthy";
  if (p.state === "open") return `Failing (${p.last_error_type}): calls are paused for a minute`;
  return `Recovering after ${p.last_error_type}: the next call is a trial`;
}

// ---------- error explanations (one per error type in reviewer_loop/errors.py) ----------

const PROBLEMS = {
  rate_limited: ["A model provider is rate limiting us", "The free-tier limit was hit repeatedly. Wait a few minutes and submit again."],
  provider_timeout: ["A model provider did not answer in time", "Try again. If it keeps happening, the status bar shows whether the provider is failing."],
  provider_unavailable: ["A model provider is down or overloaded", "The status bar shows when it recovers. Submit again then."],
  provider_auth: ["A model provider rejected our API key", "This needs an operator: the key is wrong, expired or revoked."],
  provider_rejected: ["A model provider rejected the request", "Try a shorter or simpler task. If it persists, tell an operator."],
  malformed_output: ["A model answered in the wrong format twice", "This happens occasionally. Submitting again usually works."],
  budget_exceeded: ["Today's token budget is used up", "The budget protects the free tier. It resets tomorrow."],
  tool_failure: ["The review tools stopped working", "The tool server crashed or could not start. Submit again; if it repeats, tell an operator."],
  deadline_exceeded: ["The run took too long", "Runs are stopped after their time budget. The last good version is kept below."],
  internal: ["Something went wrong on our side", "Tell an operator and include the run id below."],
};

// ---------- pages ----------

async function newReviewPage() {
  const form = el("form", { class: "panel" });
  const task = el("textarea", { id: "task", required: true, maxlength: status ? status.max_task_chars : 4000,
    placeholder: "Describe the function you need, in plain words: what it takes, what it returns, and what should happen with bad input." });
  const examples = el("select", { id: "example", "aria-label": "Start from an example" },
    el("option", { value: "" }, "Start from an example task…"));
  const rounds = el("input", { id: "rounds", type: "number", min: 1, max: status ? status.max_iterations : 4,
    value: status ? status.max_iterations : 4 });
  const inject = el("input", { type: "checkbox", id: "inject" });
  const error = el("p", { class: "form-error", role: "alert" });
  const submit = el("button", { class: "primary", type: "submit" }, "Start review");

  form.append(
    el("label", { for: "task" }, "What should the code do?"), task,
    el("div", { class: "row" },
      el("div", {}, el("label", { for: "example" }, "Or pick an example"), examples),
      el("div", { class: "narrow" }, el("label", { for: "rounds" }, "Max rounds"), rounds),
      el("div", { class: "narrow" }, submit)),
    status && status.demo_injection ? el("p", { class: "hint" }, el("label", { class: "check" }, inject,
      "Add one deliberately wrong review finding, to see how disagreement is handled")) : null,
    error);

  examples.addEventListener("change", () => {
    const chosen = examples.selectedOptions[0];
    if (chosen.value) task.value = chosen.dataset.prompt;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    error.textContent = "";
    try {
      const run = await api("/api/runs", { method: "POST", body: JSON.stringify({
        task: task.value, max_iterations: Number(rounds.value), inject_wrong_finding: inject.checked }) });
      location.hash = `#/runs/${run.id}`;
    } catch (e) {
      error.textContent = e.message;
      submit.disabled = false;
    }
  });

  const recent = el("div", {}, el("p", { class: "muted" }, "Loading your runs…"));
  show(el("h1", {}, "New review"),
    el("p", { class: "muted" }, "Groq writes the code and its tests. Gemini reviews them with the test and lint results in hand. They go back and forth until the tests pass, lint is clean and no blocking finding is left."),
    form, el("h2", {}, "Your recent runs"), recent);
  task.focus();

  api("/api/examples").then((list) => list.forEach((ex) =>
    examples.append(el("option", { value: ex.id, "data-prompt": ex.prompt }, `${ex.id} (${ex.trap})`)))).catch(() => {});
  api("/api/runs").then((runs) => recent.replaceChildren(runsTable(runs.slice(0, 5))))
    .catch((e) => recent.replaceChildren(el("p", { class: "form-error" }, e.message)));
}

function runsTable(runs) {
  if (!runs.length) return el("div", { class: "empty" }, "No runs yet. Describe a function above to start one.");
  return el("table", {},
    el("tr", {}, el("th", {}, "Started"), el("th", {}, "Task"), el("th", {}, "Result"), el("th", {}, "Took")),
    runs.map((run) => el("tr", { class: "link", tabindex: 0, onclick: () => { location.hash = `#/runs/${run.id}`; },
      onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/runs/${run.id}`; } },
      el("td", {}, run.created || ""), el("td", {}, run.task.length > 90 ? `${run.task.slice(0, 90)}…` : run.task),
      el("td", {}, el("span", { class: `status ${run.status}` }, run.status), run.error_type ? ` (${run.error_type})` : ""),
      el("td", {}, duration(run.duration_s) || ""))));
}

async function runsPage() {
  show(el("h1", {}, "My runs"), el("p", { class: "muted" }, "Loading…"));
  try {
    show(el("h1", {}, "My runs"), runsTable(await api("/api/runs")));
  } catch (e) {
    show(el("h1", {}, "My runs"), el("p", { class: "form-error" }, e.message));
  }
}

async function runPage(id, benchmark = false) {
  const hash = location.hash;
  try {
    const record = await api(benchmark ? `/api/benchmark/${id}` : `/api/runs/${id}`);
    if (location.hash !== hash) return;  // the user navigated away while we were loading
    show(...renderRun(record, benchmark));
    if (record.status === "queued" || record.status === "running") {
      pollTimer = setTimeout(() => runPage(id, benchmark), 2000);
    }
  } catch (e) {
    show(el("h1", {}, "Run not found"), el("p", { class: "muted" }, e.message), el("a", { href: "#/" }, "Start a new review"));
  }
}

async function benchmarkPage() {
  show(el("h1", {}, "Benchmark"), el("p", { class: "muted" }, "Loading…"));
  const rows = await api("/api/benchmark");
  const count = (pick) => rows.filter(pick).length;
  const sum = (pick) => rows.reduce((total, r) => total + (pick(r) || 0), 0);
  show(el("h1", {}, "Benchmark"),
    el("p", { class: "muted" }, "Ten tasks with hidden tests the loop never sees. The baseline is the generator's first draft; the result is the code after review. Scores come from the hidden tests."),
    el("div", { class: "headline" },
      el("div", {}, el("strong", {}, `${count((r) => r.baseline && r.baseline.all_passed)} → ${count((r) => r.final && r.final.all_passed)} of ${rows.length}`), "tasks passing every hidden test"),
      el("div", {}, el("strong", {}, `${sum((r) => r.baseline && r.baseline.passed)} → ${sum((r) => r.final && r.final.passed)} of ${sum((r) => r.final && r.final.total)}`), "hidden tests passing"),
      el("div", {}, el("strong", {}, `${count((r) => r.status === "converged")} of ${rows.length}`), "runs converged")),
    el("table", {},
      el("tr", {}, ["Task", "Trap", "Result", "Rounds", "Draft", "After review", "Outcome", "Findings"].map((h) => el("th", {}, h))),
      rows.map((r) => el("tr", { class: "link", tabindex: 0, onclick: () => { location.hash = `#/benchmark/${r.task}`; },
        onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/benchmark/${r.task}`; } },
        el("td", {}, el("code", {}, r.task)), el("td", {}, r.trap),
        el("td", {}, el("span", { class: `status ${r.status}` }, r.status)), el("td", {}, r.rounds),
        el("td", {}, r.baseline ? `${r.baseline.passed}/${r.baseline.total}` : "–"),
        el("td", {}, r.final ? `${r.final.passed}/${r.final.total}` : "–"),
        el("td", {}, r.outcome), el("td", {}, r.findings)))));
}

// ---------- the run view ----------

function renderRun(record, benchmark) {
  const findings = Object.fromEntries((record.findings || []).map((f) => [f.id, f]));
  const steps = record.iterations || [];
  const usage = record.usage || {};
  const nodes = [];

  // prompt files wrap at a fixed width: join single line breaks, keep blank-line paragraphs
  const taskText = (record.task || "").replace(/([^\n])\n(?!\n)/g, "$1 ");
  const card = el("section", { class: "panel task-card" }, el("p", {}, taskText));
  if (record.task && record.task.length > 320) {
    card.classList.add("clamped");
    card.append(el("button", { type: "button", onclick: (e) => {
      card.classList.toggle("clamped");
      e.target.textContent = card.classList.contains("clamped") ? "Show the whole task" : "Show less";
    } }, "Show the whole task"));
  }
  card.append(el("div", { class: "meta" },
    benchmark ? el("span", {}, "Benchmark run") : el("span", {}, `Started ${record.created || record.started || ""}`),
    duration(record.duration_s) ? el("span", {}, duration(record.duration_s)) : null,
    usage.generator ? el("span", { title: usage.generator.model }, `Generator ${kTokens(tokens(usage.generator))} tokens`) : null,
    usage.reviewer ? el("span", { title: usage.reviewer.model }, `Reviewer ${kTokens(tokens(usage.reviewer))} tokens`) : null,
    record.id ? el("span", {}, `Run ${record.id}`) : null));
  nodes.push(card, ...verdict(record, steps, findings));

  if (steps.length || record.baseline) nodes.push(conversation(record, steps, findings));
  if (record.final || steps.length) nodes.push(...detailTabs(record, steps));
  return nodes;
}

function verdict(record, steps, findings) {
  const rounds = plural(steps.length, "round");
  const s = record.status;
  const texts = {
    converged: ["Converged", `in ${rounds}: the tests pass, lint is clean and no blocking finding is left`],
    deadlock: ["Stopped: no progress", "The generator returned the same code while blocking issues remained. The last good version is below."],
    timeout: ["Stopped at the round limit", `${rounds} used without converging. The last good version is below.`],
    error: ["Failed", steps.length ? `after ${rounds}` : "before the first review"],
    interrupted: ["Interrupted", "The service restarted before this run finished."],
    queued: ["Waiting for a free worker", "Runs execute one at a time to stay inside the free-tier limits."],
    running: ["Running", steps.length ? `round ${steps.length + 1} in progress` : "writing the first draft"],
  };
  const [title, text] = texts[s] || [s, ""];
  const out = [el("div", { class: "verdict" },
    el("h1", { class: `status ${s} ${s === "running" || s === "queued" ? "working" : ""}` }, title), el("p", {}, text))];

  if (s === "error" || s === "interrupted") {
    const [heading, advice] = PROBLEMS[record.error_type] || ["The run did not finish", record.error || ""];
    out.push(el("div", { class: "problem", role: "alert" }, el("h3", {}, heading), el("p", {}, advice),
      record.error ? el("details", {}, el("summary", {}, "Technical detail"),
        el("p", {}, `Error type: ${record.error_type || "none"}`), el("p", {}, record.error),
        record.id ? el("p", {}, `Run id: ${record.id}`) : null) : null));
  }
  const escalated = record.escalated_to_human || [];
  if (escalated.length) {
    out.push(el("div", { class: "problem" }, el("h3", {}, "Needs a human decision"),
      el("p", {}, `The models still disagree about ${escalated.map((id) => `${id} (${findings[id] ? findings[id].title : ""})`).join(", ")}. Neither side's evidence settled it, so the loop stopped arguing.`)));
  }
  return out;
}

function conversation(record, steps, findings) {
  const grid = el("div", { class: "voices" },
    el("div", { class: "who gen" }, `Generator · ${record.generator_model || "Groq"}`),
    el("div", { class: "who ref" }, "Evidence and rulings"),
    el("div", { class: "who rev" }, `Reviewer · ${record.reviewer_model || "Gemini"}`));

  const events = [];
  for (const f of Object.values(findings)) for (const h of f.history || []) events.push({ ...h, finding: f });
  const at = (round, actor) => events.filter((e) => e.iteration === round && e.actor === actor);

  if (record.baseline) {
    const firstTests = steps[0] && steps[0].test_results;
    const count = firstTests ? firstTests.passed + firstTests.failed : null;
    grid.append(el("div", { class: "round-label" }, "First draft"),
      el("div", { class: "col gen" }, el("div", { class: "bubble" },
        "Wrote ", el("code", {}, "solution.py"), " and ", el("code", {}, "test_solution.py"),
        count ? ` with ${plural(count, "test")}.` : ".")),
      el("div", { class: "col ref" }), el("div", { class: "col rev" }));
  }

  steps.forEach((step) => {
    const n = step.iteration;
    const generatorSays = at(n, "generator");
    grid.append(el("div", { class: "round-label" }, `Round ${n}`),
      el("div", { class: "col gen" },
        generatorSays.length ? el("div", { class: "bubble" }, "Replied to the review and revised the code:",
          generatorSays.map((e) => el("div", { class: "decision" },
            el("span", { class: `verb ${e.status === "declined" ? "decline" : "accept"}` },
              e.status === "declined" ? "Declined " : "Accepted "), `${e.finding.id}`,
            e.reason ? [": ", el("q", {}, e.reason)] : null))) : null),
      evidence(step, at(n, "orchestrator")),
      el("div", { class: "col rev" },
        step.review_summary ? el("div", { class: "bubble" }, step.review_summary) : null,
        (step.new_findings || []).map((id) => findings[id] && findingItem(findings[id])),
        at(n, "reviewer").filter((e) => e.status !== "open").map((e) => el("div", { class: "decision" },
          el("span", { class: `verb ${e.status === "withdrawn" ? "accept" : "decline"}` },
            e.status === "withdrawn" ? "Withdrew " : "Maintained "), e.finding.id,
          e.reason ? [": ", el("q", {}, e.reason)] : null))));
  });
  return grid;
}

function evidence(step, rulings) {
  const t = step.test_results || {}, l = step.lint_results || {};
  const total = (t.passed || 0) + (t.failed || 0);
  const testChip = t.status === "passed" ? el("span", { class: "chip pass" }, `${t.passed} of ${total} pass`)
    : t.status === "timeout" ? el("span", { class: "chip fail" }, "timed out")
    : t.status === "error" && !total ? el("span", { class: "chip fail" }, "did not run")
    : el("span", { class: "chip fail" }, `${t.passed || 0} of ${total} pass`);
  const codes = [...new Set((l.issues || []).filter((i) => i.blocking).map((i) => i.code))];
  const lintChip = l.blocking_count ? el("span", { class: "chip lint", title: codes.join(", ") },
    `${l.blocking_count} blocking (${codes.join(", ")})`)
    : el("span", { class: "chip pass" }, l.advisory_count ? `clean, ${l.advisory_count} advisory` : "clean");
  const words = { fixed: "fix kept", reverted: "fix rolled back", withdrawn: "closed: decline not contested",
    disputed: "disputed: escalated to a human", late_objection: "late objection: downgraded" };
  return el("div", { class: "col ref" },
    el("b", {}, "Tests"), testChip, el("b", {}, "Lint"), lintChip,
    step.reverted ? el("div", { class: "bubble rollback ruling" }, el("strong", {}, "Rolled back "),
      `the previous revision: ${step.revert_reason}`) : null,
    rulings.map((e) => el("div", { class: "ruling" }, el("strong", {}, e.finding.id), ` ${words[e.status] || e.status}`)));
}

function findingItem(f) {
  return el("details", { class: "finding" },
    el("summary", {}, el("span", { class: "fid" }, f.id), el("span", { class: "ftitle" }, f.title,
      f.injected ? el("span", { class: "muted" }, " (injected for the demo)") : null),
      el("span", { class: `sev ${f.severity}` }, f.severity)),
    el("div", { class: "body" },
      el("p", {}, el("code", {}, `${f.file}:${f.line}`), f.blocking ? " · blocking" : " · advisory",
        f.standard_ref ? ` · standard ${f.standard_ref}` : ""),
      el("p", {}, f.rationale), f.suggested_fix ? el("p", {}, "Suggested fix: ", f.suggested_fix) : null,
      el("p", {}, "Now: ", el("span", { class: `state ${f.status}` }, f.status.replace("_", " ")))));
}

function detailTabs(record, steps) {
  const panes = {
    "Final code": () => {
      if (!record.final) return [el("div", { class: "empty" }, "No code was produced.")];
      return [el("div", { class: "file-name" }, "solution.py"), el("pre", {}, record.final.solution_code),
        el("div", { class: "file-name" }, "test_solution.py"), el("pre", {}, record.final.test_code)];
    },
    "Changes per round": () => steps.map((s) => [el("div", { class: "file-name" }, `Round ${s.iteration}`),
      s.reverted ? el("div", { class: "empty" }, `The revision was rolled back: ${s.revert_reason}`)
        : s.diff ? diffBlock(s.diff) : el("div", { class: "empty" }, s.iteration === 1 ? "First reviewed version." : "No changes.")]),
    "All findings": () => [findingsTable(record.findings || [])],
    "Tool calls": () => [toolCallsTable(record.tool_calls || [])],
  };
  const body = el("div", {});
  const buttons = Object.keys(panes).map((name) => el("button", { type: "button", onclick: (e) => {
    buttons.forEach((b) => b.classList.toggle("on", b === e.target));
    body.replaceChildren(...panes[name]().flat());
  } }, name));
  buttons[0].classList.add("on");
  body.replaceChildren(...panes["Final code"]().flat());
  return [el("div", { class: "tabs", role: "tablist" }, buttons), body];
}

function diffBlock(diff) {
  return el("pre", { class: "diff" }, diff.split("\n").map((line) => {
    const kind = line.startsWith("@@") ? "hunk" : line.startsWith("+") && !line.startsWith("+++") ? "add"
      : line.startsWith("-") && !line.startsWith("---") ? "del" : null;
    return [kind ? el("span", { class: kind }, line) : line, "\n"];
  }));
}

function findingsTable(list) {
  if (!list.length) return el("div", { class: "empty" }, "The reviewer raised no findings.");
  return el("table", {},
    el("tr", {}, ["", "Severity", "Where", "Finding", "Outcome", "What happened"].map((h) => el("th", {}, h))),
    list.map((f) => el("tr", {}, el("td", {}, el("b", {}, f.id)),
      el("td", {}, el("span", { class: `sev ${f.severity}` }, f.severity), f.blocking ? "" : el("div", { class: "muted" }, "advisory")),
      el("td", {}, el("code", {}, `${f.file}:${f.line}`)), el("td", {}, f.title),
      el("td", {}, el("span", { class: `state ${f.status}` }, f.status.replace("_", " "))),
      el("td", { class: "muted" }, (f.history || []).map((h) => el("div", {},
        `Round ${h.iteration}, ${h.actor}: ${h.status}${h.reason ? ` (${h.reason})` : ""}`))))));
}

function toolCallsTable(calls) {
  if (!calls.length) return el("div", { class: "empty" }, "No tool calls were recorded.");
  return el("table", {},
    el("tr", {}, ["Round", "MCP tool", "Seconds", "Result"].map((h) => el("th", {}, h))),
    calls.map((c) => el("tr", {}, el("td", {}, c.iteration), el("td", {}, el("code", {}, c.tool)),
      el("td", {}, c.seconds), el("td", {}, c.is_error ? el("span", { class: "chip fail" }, "error") : "ok"))));
}

// ---------- routing ----------

function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  const section = parts[0] || "new";
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("on", a.dataset.nav === (section === "runs" && parts[1] ? "runs" : section)));
  view.focus({ preventScroll: true });
  window.scrollTo(0, 0);
  if (section === "runs" && parts[1]) return runPage(parts[1]);
  if (section === "runs") return runsPage();
  if (section === "benchmark" && parts[1]) return runPage(parts[1], true);
  if (section === "benchmark") return benchmarkPage().catch((e) => show(el("p", { class: "form-error" }, e.message)));
  return newReviewPage();
}

window.addEventListener("hashchange", route);
refreshStatus().finally(route);
setInterval(refreshStatus, 15000);

const el = (id) => document.getElementById(id);

const dropzone = el("dropzone");
const fileInput = el("file-input");
const uploadError = el("upload-error");
const stepAccounts = el("step-accounts");
const stepResults = el("step-results");
const accountsBody = el("accounts-body");
const runBtn = el("run-btn");
const resultsList = el("results-list");

let session = null;
let total = 0;
let results = [];

/* ---- upload ---- */

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});
dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("is-over"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-over"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("is-over");
  if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) upload(fileInput.files[0]);
});

async function upload(file) {
  showError(null);
  const body = new FormData();
  body.append("file", file);

  let payload;
  try {
    const response = await fetch("/api/upload", { method: "POST", body });
    payload = await response.json();
    if (!response.ok) return showError(payload.error || "Upload failed.");
  } catch (err) {
    return showError("Could not reach the server. Is it still running?");
  }

  session = payload.session;
  total = payload.accounts.length;
  renderAccounts(payload.accounts);
  el("account-count").textContent = `${total} account${total === 1 ? "" : "s"}`;
  stepAccounts.hidden = false;
  stepResults.hidden = true;
  resultsList.innerHTML = "";
  results = [];
  stepAccounts.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function showError(message) {
  uploadError.textContent = message || "";
  uploadError.hidden = !message;
}

function renderAccounts(accounts) {
  accountsBody.innerHTML = "";
  for (const account of accounts) {
    const tr = document.createElement("tr");
    for (const value of [account.row, account.name, account.contact, account.email]) {
      const td = document.createElement("td");
      td.textContent = value == null ? "" : value;
      tr.appendChild(td);
    }
    accountsBody.appendChild(tr);
  }
}

/* ---- run ---- */

runBtn.addEventListener("click", () => {
  if (!session) return;
  runBtn.disabled = true;
  runBtn.textContent = "Researching…";
  resultsList.innerHTML = "";
  results = [];
  stepResults.hidden = false;
  el("download-actions").hidden = true;
  el("progress").hidden = false;
  setProgress(0);
  stepResults.scrollIntoView({ behavior: "smooth", block: "start" });

  const force = el("force-refresh").checked ? "1" : "0";
  const engine = el("evidence-engine").checked ? "evidence" : "legacy";
  const stream = new EventSource(`/api/run/${session}?force=${force}&engine=${engine}`);

  stream.addEventListener("result", (event) => {
    const result = JSON.parse(event.data);
    results.push(result);
    resultsList.appendChild(card(result));
    setProgress(results.length, result.name);
  });

  stream.addEventListener("done", (event) => {
    stream.close();
    const summary = JSON.parse(event.data);
    finish(summary);
  });

  stream.onerror = () => {
    stream.close();
    el("progress-label").textContent = "Connection to the server was lost.";
    resetRunButton();
  };
});

function setProgress(done, name) {
  el("progress-fill").style.width = `${total ? (done / total) * 100 : 0}%`;
  el("progress-label").textContent = done === 0
    ? `Researching ${total} account${total === 1 ? "" : "s"}…`
    : `${done} of ${total} complete${name ? ` — ${name}` : ""}`;
}

function finish(summary) {
  resetRunButton();
  el("progress").hidden = true;

  results.sort((a, b) =>
    (b.rank_score ?? b.heat ?? 0) - (a.rank_score ?? a.heat ?? 0) || a.row - b.row);
  resultsList.innerHTML = "";
  for (const result of results) resultsList.appendChild(card(result));

  el("results-count").textContent = summary.failed
    ? `${summary.completed} scored, ${summary.failed} failed`
    : `${summary.completed} scored, hottest first`;

  if (summary.completed) {
    el("download-btn").href = `/api/download/${session}`;
    el("download-actions").hidden = false;
  }
}

function resetRunButton() {
  runBtn.disabled = false;
  runBtn.textContent = "Run query";
}

/* ---- result card ---- */

function card(result) {
  const wrapper = document.createElement("div");
  wrapper.className = "result";

  const heatWrap = document.createElement("div");
  heatWrap.className = "heat-stack";
  const heat = document.createElement("div");
  heat.className = `heat heat-${result.heat || "none"}`;
  const raw = result.rank_score;
  heat.textContent = result.heat
    ? (raw != null && raw < 2 ? raw.toFixed(1) : result.heat)
    : "—";
  if (raw != null) heat.title = `raw score ${raw}`;
  heatWrap.appendChild(heat);
  if (result.evidence_grade) {
    // Grade sits beside the score, never replaces it: a 3 on grade C
    // evidence is not the same lead as a 3 on grade A.
    const grade = document.createElement("div");
    grade.className = `grade grade-${result.evidence_grade}`;
    grade.textContent = `Grade ${result.evidence_grade}`;
    grade.title = "A: 2+ confirmed from deterministic sources · B: confirmed, grounded search only · C: nothing confirmed";
    heatWrap.appendChild(grade);
  }
  wrapper.appendChild(heatWrap);

  const body = document.createElement("div");
  body.className = "result-body";

  const head = document.createElement("div");
  head.className = "result-head";
  const name = document.createElement("span");
  name.className = "result-name";
  name.textContent = result.name;
  head.appendChild(name);
  if (result.cached) head.appendChild(tag("cached"));
  if (result.engine === "evidence") head.appendChild(tag("evidence engine"));
  if (result.needs_confirmation) head.appendChild(tag("confirm company number"));
  if (result.findings && result.findings.current_system) {
    head.appendChild(tag(result.findings.current_system));
  }
  body.appendChild(head);

  if (result.error) {
    const error = document.createElement("p");
    error.className = "result-error";
    error.textContent = result.error;
    body.appendChild(error);
    wrapper.appendChild(body);
    return wrapper;
  }

  const analysis = document.createElement("p");
  analysis.className = "result-analysis";
  analysis.textContent = result.analysis;
  body.appendChild(analysis);

  if (result.hook) {
    const hook = document.createElement("p");
    hook.className = "result-hook";
    hook.textContent = `\u201C${result.hook}\u201D`;
    hook.title = "Opening line for a call — rests only on confirmed signals";
    body.appendChild(hook);
  }

  if (result.reasons && result.reasons.length) {
    const list = document.createElement("ul");
    list.className = "reasons";
    for (const reason of result.reasons) {
      const item = document.createElement("li");
      item.textContent = reason;
      list.appendChild(item);
    }
    body.appendChild(list);
  }

  if (result.sources && result.sources.length) {
    const sources = document.createElement("div");
    sources.className = "sources";
    const label = document.createElement("p");
    label.className = "sources-label";
    label.textContent = `Sources (${result.sources.length})`;
    sources.appendChild(label);
    for (const source of result.sources) {
      const link = document.createElement("a");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = source.title || source.url;
      sources.appendChild(link);
    }
    body.appendChild(sources);
  }

  if (result.verdicts) body.appendChild(evidenceToggle(result));
  if (result.needs_confirmation) body.appendChild(confirmPrompt(result));

  wrapper.appendChild(body);
  return wrapper;
}

const VERDICT_LABELS = {
  system_in_use: "System in use",
  competitor_in_place: "Competitor",
  maintenance_expiry: "Maintenance expiry",
  leadership_change: "Leadership change",
  restructuring: "Restructuring",
  hiring_signal: "Hiring signal",
  stated_replacement_intent: "Replacement intent",
};

function evidenceToggle(result) {
  const details = document.createElement("details");
  details.className = "evidence";

  const summary = document.createElement("summary");
  const counts = Object.entries(result.verdicts)
    .filter(([k, v]) => k !== "hook" && v && v.verdict === "confirmed").length;
  summary.textContent = `Evidence — ${counts} confirmed of ${Object.keys(VERDICT_LABELS).length} signals`;
  details.appendChild(summary);

  const table = document.createElement("table");
  table.className = "evidence-table";
  table.innerHTML = "<thead><tr><th>Signal</th><th>Verdict</th><th>Value</th><th>Date</th><th>Source</th></tr></thead>";
  const tbody = document.createElement("tbody");

  for (const [key, label] of Object.entries(VERDICT_LABELS)) {
    const item = result.verdicts[key] || {};
    const tr = document.createElement("tr");

    tr.appendChild(cellText(label, "ev-signal"));

    const verdict = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `vbadge v-${item.verdict || "unknown"}`;
    badge.textContent = (item.verdict || "unknown").replace("_", " ");
    verdict.appendChild(badge);
    tr.appendChild(verdict);

    tr.appendChild(cellText(item.value || item.quote || ""));
    tr.appendChild(cellText(item.event_date || item.evidence_date || ""));

    const source = document.createElement("td");
    if (item.source_url) {
      const link = document.createElement("a");
      link.href = item.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      try { link.textContent = new URL(item.source_url).hostname.replace(/^www\./, ""); }
      catch { link.textContent = "source"; }
      source.appendChild(link);
      if (item.url_status && item.url_status !== "resolved") {
        source.appendChild(tag(item.url_status.replace("_", " ")));
      }
    } else {
      source.textContent = "—";
      source.className = "ev-muted";
    }
    tr.appendChild(source);

    if (item.reasoning) tr.title = item.reasoning;
    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  details.appendChild(table);

  if (result.judge_notes && result.judge_notes.length) {
    const notes = document.createElement("p");
    notes.className = "ev-notes";
    notes.textContent = `Adjusted on review: ${result.judge_notes.join("; ")}`;
    details.appendChild(notes);
  }
  return details;
}

function cellText(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function confirmPrompt(result) {
  const box = document.createElement("div");
  box.className = "confirm";

  const label = document.createElement("label");
  label.textContent = "Companies House match was ambiguous. Enter the company number:";
  box.appendChild(label);

  const row = document.createElement("div");
  row.className = "confirm-row";

  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "e.g. 11874049";
  input.maxLength = 10;
  row.appendChild(input);

  const button = document.createElement("button");
  button.className = "btn btn-quiet";
  button.textContent = "Confirm";
  row.appendChild(button);

  const status = document.createElement("span");
  status.className = "confirm-status";
  row.appendChild(status);
  box.appendChild(row);

  button.addEventListener("click", async () => {
    const value = input.value.trim();
    if (!value) return;
    button.disabled = true;
    status.textContent = "Saving…";
    try {
      const response = await fetch("/api/confirm-company", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account: result.name, company_number: value }),
      });
      const payload = await response.json();
      if (!response.ok) {
        status.textContent = payload.error || "Could not save.";
        button.disabled = false;
        return;
      }
      status.textContent = `Saved — ${payload.confirmed.company_number}. Re-run to use it.`;
      input.disabled = true;
    } catch {
      status.textContent = "Could not reach the server.";
      button.disabled = false;
    }
  });

  return box;
}

function tag(text) {
  const span = document.createElement("span");
  span.className = "tag";
  span.textContent = text;
  return span;
}

/* ---- reset ---- */

el("reset-btn").addEventListener("click", () => {
  session = null;
  results = [];
  fileInput.value = "";
  stepAccounts.hidden = true;
  stepResults.hidden = true;
  showError(null);
  window.scrollTo({ top: 0, behavior: "smooth" });
});

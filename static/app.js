const requestTypeEl = document.getElementById("requestType");
const runBtnEl = document.getElementById("runBtn");
const messageCardEl = document.getElementById("messageCard");
const logsEl = document.getElementById("logs");
const outputEl = document.getElementById("output");

const state = {
  schema: null,
  requestTypes: [],
  messages: []
};

function addLog(text, tone = "info") {
  const item = document.createElement("div");
  item.className = `log-item log-${tone}`;
  item.textContent = text;
  logsEl.appendChild(item);
  logsEl.scrollTop = logsEl.scrollHeight;
}

function clearLogs() {
  logsEl.innerHTML = "";
}

function prettyLabel(value) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function messageForType(requestType) {
  return state.messages.find((msg) => msg.request_type === requestType) || null;
}

function renderMessagePreview(requestType) {
  const message = messageForType(requestType);
  if (!message) {
    messageCardEl.classList.add("empty");
    messageCardEl.textContent = "No sample message found for this type.";
    return;
  }

  messageCardEl.classList.remove("empty");
  messageCardEl.innerHTML = `
    <div class="message-meta">
      <strong>${escapeHtml(message.id || "no-id")}</strong> · ${escapeHtml(message.source || "unknown source")} · ${escapeHtml(message.sender || "unknown sender")}
    </div>
    <p class="message-text">${escapeHtml(message.message || "no message text")}</p>
  `;
}

function renderList(items, emptyLabel = "No items") {
  if (!Array.isArray(items) || !items.length) {
    return `<p class="block-body">${escapeHtml(emptyLabel)}</p>`;
  }
  return `<ul class="missing-list">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function renderTicketOutcome(ticket) {
  return `
    <div class="template-card ticket-template">
      <div class="template-top">
        <span class="template-kicker">Internal Ticket</span>
        <span class="template-id">${escapeHtml(ticket.ticket_id || "BUG-UNKNOWN")}</span>
      </div>
      <h3>${escapeHtml(ticket.title || "No summary")}</h3>
      <div class="kv-grid">
        <div><span>Status</span><strong class="kv-value">${escapeHtml(ticket.status || "-")}</strong></div>
        <div><span>Queue</span><strong class="kv-value">${escapeHtml(ticket.queue || "-")}</strong></div>
        <div><span>Priority</span><strong class="kv-value">${escapeHtml(ticket.priority || "-")}</strong></div>
        <div><span>Reporter</span><strong class="kv-value">${escapeHtml(ticket.reporter || "-")}</strong></div>
      </div>
    </div>
  `;
}

function renderTaskOutcome(task) {
  return `
    <div class="template-card task-template">
      <div class="template-top">
        <span class="template-kicker">Feature Task Sheet</span>
        <span class="template-id">${escapeHtml(task.task_id || "FEAT-UNKNOWN")}</span>
      </div>
      <h3>${escapeHtml(task.title || "No summary")}</h3>
      <table class="task-table">
        <tr><th>Workspace</th><td>${escapeHtml(task.workspace || "-")}</td></tr>
        <tr><th>Status</th><td>${escapeHtml(task.status || "-")}</td></tr>
        <tr><th>Owner Team</th><td>${escapeHtml(task.owner_team || "-")}</td></tr>
        <tr><th>Impact Priority</th><td>${escapeHtml(task.impact_priority || "-")}</td></tr>
        <tr><th>Requester</th><td>${escapeHtml(task.requester || "-")}</td></tr>
        <tr><th>Source</th><td>${escapeHtml(task.source || "-")}</td></tr>
      </table>
    </div>
  `;
}

function renderReplyOutcome(reply, manualChunks) {
  const chunkBadges = Array.isArray(reply.grounding_chunk_ids) && reply.grounding_chunk_ids.length
    ? reply.grounding_chunk_ids.map((id) => `<span class="chunk-chip">${escapeHtml(id)}</span>`).join("")
    : "<span class=\"chunk-chip\">No chunk ids</span>";
  return `
    <div class="template-card reply-template">
      <div class="template-top">
        <span class="template-kicker">Customer Reply Draft</span>
        <span class="template-id">${escapeHtml(reply.message_id || "Q-UNKNOWN")}</span>
      </div>
      <p class="reply-text">${escapeHtml(reply.reply_draft || "No draft generated.")}</p>
      <div class="template-section">
        <p class="block-title">Grounded By</p>
        <div class="chunk-chip-wrap">${chunkBadges}</div>
      </div>
    </div>
  `;
}

function renderOutput(data) {
  const extraction = data.extraction || {};
  const requestType = data.request_type || extraction.request_type || "unknown";
  const outcomeType = data.outcome_type || "unknown";
  const outcome = data.outcome || {};
  const manualChunks = Array.isArray(data.question_manual_chunks) ? data.question_manual_chunks : [];
  const missing = Array.isArray(extraction.missing_info_questions) ? extraction.missing_info_questions : [];

  let outcomeHtml = `<p class="block-body">No outcome generated.</p>`;
  if (outcomeType === "ticket_entry") {
    outcomeHtml = renderTicketOutcome(outcome);
  } else if (outcomeType === "task_sheet") {
    outcomeHtml = renderTaskOutcome(outcome);
  } else if (outcomeType === "reply_draft") {
    outcomeHtml = renderReplyOutcome(outcome, manualChunks);
  }

  outputEl.classList.remove("empty");
  outputEl.innerHTML = `
    <div class="badges">
      <span class="badge">${escapeHtml(prettyLabel(requestType))}</span>
      <span class="badge">Priority ${escapeHtml(extraction.priority?.level || "-")}</span>
      <span class="badge">Outcome ${escapeHtml(prettyLabel(outcomeType))}</span>
    </div>

    <div class="block">
      <p class="block-title">Routed Outcome</p>
      ${outcomeHtml}
    </div>

    <div class="block">
      <p class="block-title">Extraction Summary</p>
      <p class="block-body"><strong>Summary:</strong> ${escapeHtml(extraction.summary || "no information found")}</p>
      <p class="block-body"><strong>Priority Rationale:</strong> ${escapeHtml(extraction.priority?.rationale || "no information found")}</p>
    </div>

    <div class="block">
      <p class="block-title">Missing Information Questions</p>
      ${renderList(missing, "No missing information questions.")}
    </div>

    <div class="block">
      <p class="block-title">Raw Extraction JSON</p>
      <pre>${JSON.stringify({ extraction, outcome_type: outcomeType, outcome }, null, 2)}</pre>
    </div>
  `;
}

function renderError(message) {
  outputEl.classList.remove("empty");
  outputEl.innerHTML = `<p class="block-body">${escapeHtml(message)}</p>`;
}

async function runPipeline() {
  const requestType = requestTypeEl.value;
  if (!requestType) {
    addLog("Please choose a request type to continue.", "error");
    return;
  }

  clearLogs();
  outputEl.classList.add("empty");
  outputEl.textContent = "Running...";

  addLog(`Selected type: ${prettyLabel(requestType)}.`);
  const selected = messageForType(requestType);
  addLog(`Loaded sample message ${selected?.id || "unknown"}.`);

  runBtnEl.disabled = true;
  try {
    addLog("Processing your request...");
    const response = await fetch("/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_type: requestType })
    });

    const data = await response.json();

    if (Array.isArray(data.pipeline_logs)) {
      const stepMap = {
        request_received: "Request received.",
        input_resolved: "Input confirmed.",
        classification_prompt_ready: "Extraction prompt prepared.",
        classification_llm_start: "Model is classifying and extracting.",
        classification_llm_done: "Structured extraction received.",
        classification_json_parse_done: "Extraction JSON parsed.",
        classification_schema_check_done: "Extraction schema validated.",
        outcome_route_selected: "Outcome route selected.",
        question_manual_retrieval_done: "Loaded relevant chunks from manual.json.",
        question_reply_prompt_ready: "Reply prompt prepared from manual chunks.",
        question_reply_llm_start: "Model is drafting grounded reply.",
        question_reply_llm_done: "Grounded reply draft received.",
        question_reply_json_parse_done: "Reply JSON parsed.",
        pipeline_complete: "Pipeline complete.",
        pipeline_failed: "Pipeline could not complete."
      };
      data.pipeline_logs.forEach((entry) => {
        const nice = stepMap[entry.step] || "Step completed.";
        addLog(nice);
      });
    }

    if (!response.ok) {
      addLog("Could not finish this run. Please try again.", "error");
      renderError(data.error || "Could not finish this run.");
      return;
    }

    addLog("Your routed outcome is ready.", "ok");
    renderOutput(data);
  } catch (error) {
    addLog("Something interrupted the request. Please try again.", "error");
    renderError("Something interrupted the request. Please try again.");
  } finally {
    runBtnEl.disabled = false;
  }
}

async function init() {
  clearLogs();
  addLog("Loading demo data...");

  try {
    const [schemaRes, messagesRes] = await Promise.all([
      fetch("/schema"),
      fetch("/messages")
    ]);

    const schemaData = await schemaRes.json();
    const messagesData = await messagesRes.json();

    state.schema = schemaData.schema;
    state.requestTypes = Array.isArray(schemaData.request_type_options)
      ? schemaData.request_type_options
      : [];
    state.messages = Array.isArray(messagesData) ? messagesData : [];

    if (!state.requestTypes.length) {
      const uniqueTypes = Array.from(new Set(state.messages.map((m) => m.request_type).filter(Boolean)));
      state.requestTypes = uniqueTypes;
    }

    requestTypeEl.innerHTML = state.requestTypes
      .map((requestType) => `<option value="${requestType}">${prettyLabel(requestType)}</option>`)
      .join("");

    if (!state.requestTypes.length) {
      addLog("No request types are available right now.", "error");
      requestTypeEl.innerHTML = "<option value=''>No types found</option>";
      runBtnEl.disabled = true;
      return;
    }

    const initialType = state.requestTypes[0];
    requestTypeEl.value = initialType;
    renderMessagePreview(initialType);
    addLog("Ready.");
  } catch (error) {
    addLog("Could not load the demo data.", "error");
    runBtnEl.disabled = true;
  }
}

requestTypeEl.addEventListener("change", (event) => {
  const requestType = event.target.value;
  renderMessagePreview(requestType);
  addLog(`Switched to ${prettyLabel(requestType)}.`);
});

runBtnEl.addEventListener("click", runPipeline);

init();

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

function renderOutput(data) {
  const extraction = data.extraction;
  const routing = data.routing || {};
  const missing = Array.isArray(extraction.missing_info_questions)
    ? extraction.missing_info_questions
    : [];
  const manualChunks = Array.isArray(data.retrieved_manual_chunks) ? data.retrieved_manual_chunks : [];
  const routePayload = routing.result && routing.result.payload ? routing.result.payload : null;

  outputEl.classList.remove("empty");
  outputEl.innerHTML = `
    <div class="badges">
      <span class="badge">${escapeHtml(prettyLabel(extraction.request_type || "unknown"))}</span>
      <span class="badge">Priority ${escapeHtml(extraction.priority?.level || "-")}</span>
      <span class="badge">Model ${escapeHtml(data.model || "-")}</span>
      ${routing.route ? `<span class="badge">Route ${escapeHtml(prettyLabel(routing.route))}</span>` : ""}
    </div>

    <div class="block">
      <p class="block-title">Priority Rationale</p>
      <p class="block-body">${escapeHtml(extraction.priority?.rationale || "no information found")}</p>
    </div>

    <div class="block">
      <p class="block-title">Summary</p>
      <p class="block-body">${escapeHtml(extraction.summary || "no information found")}</p>
    </div>

    <div class="block">
      <p class="block-title">Suggested Next Action</p>
      <p class="block-body">${escapeHtml(extraction.suggested_next_action || "no information found")}</p>
    </div>

    <div class="block">
      <p class="block-title">Routing (Mock)</p>
      <p class="block-body">
        Action: <strong>${escapeHtml(prettyLabel(routing.action || "n/a"))}</strong><br />
        Endpoint: <code>${escapeHtml(routing.mock_endpoint || "n/a")}</code><br />
        Status: ${escapeHtml(routing.result?.status || "n/a")}
      </p>
      <pre>${JSON.stringify(routePayload || {}, null, 2)}</pre>
    </div>

    <div class="block">
      <p class="block-title">Missing Information Questions</p>
      ${missing.length ? `<ul class="missing-list">${missing.map((q) => `<li>${escapeHtml(q)}</li>`).join("")}</ul>` : "<p class=\"block-body\">[]</p>"}
    </div>

    <div class="block">
      <p class="block-title">Manual Chunks Used</p>
      ${
        manualChunks.length
          ? `<ul class="missing-list">${manualChunks
              .map(
                (chunk) =>
                  `<li><strong>${escapeHtml(chunk.id || "UNKNOWN")}</strong>${chunk.title ? ` - ${escapeHtml(chunk.title)}` : ""}</li>`
              )
              .join("")}</ul>`
          : "<p class=\"block-body\">No manual chunks found.</p>"
      }
    </div>

    <div class="block">
      <p class="block-title">Raw JSON</p>
      <pre>${JSON.stringify(extraction, null, 2)}</pre>
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
        manual_retrieval_done: "Relevant manual references loaded.",
        prompt_ready: "Instructions prepared.",
        llm_request_start: "Model is analyzing the message.",
        llm_request_done: "Model response received.",
        json_parse_done: "Response structured as JSON.",
        schema_check_done: "Schema validation complete.",
        routing_start: "Routing stage started.",
        routing_selected: "Routing path selected.",
        routing_dispatch_done: "Mock route payload created.",
        routing_complete: "Routing complete.",
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

    addLog("Your extraction is ready.", "ok");
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

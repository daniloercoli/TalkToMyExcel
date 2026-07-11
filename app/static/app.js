let staging = null;

/* ponytail: marked/DOMPurify from CDN; no local dependency */
const parseMd = (text) =>
  window.marked && window.DOMPurify
    ? DOMPurify.sanitize(window.marked.parse(text || ""), { ADD_ATTR: ["target"] })
    : escapeHtml(text || "");

marked?.use({ breaks: true, gfm: true, headerIds: false, mangle: false });

const uploadForm = document.getElementById("uploadForm");
const fileInput = document.getElementById("fileInput");
const fileName = document.getElementById("fileName");
const uploadDialog = document.getElementById("uploadDialog");
const uploadDialogEyebrow = document.getElementById("uploadDialogEyebrow");
const uploadDialogTitle = document.getElementById("uploadDialogTitle");
const uploadDialogText = document.getElementById("uploadDialogText");
const uploadDialogFile = document.getElementById("uploadDialogFile");
const uploadSteps = [...document.querySelectorAll("[data-upload-step]")];
const profilePanel = document.getElementById("profilePanel");
const sheetList = document.getElementById("sheetList");
const importButton = document.getElementById("importButton");
const activeDataset = document.getElementById("activeDataset");
const chatStatus = document.getElementById("chatStatus");
const messages = document.getElementById("messages");
const emptyChat = document.getElementById("emptyChat");
const chatForm = document.getElementById("chatForm");
const questionInput = document.getElementById("questionInput");
const contextUsage = document.getElementById("contextUsage");
const clearSession = document.getElementById("clearSession");
const rebuildIndexBtn = document.getElementById("rebuildIndexBtn");
async function refreshContext() {
  if (!contextUsage) return;
  try {
    const res = await fetch("/api/session/context");
    if (!res.ok) throw new Error("Context refresh failed");
    const data = await res.json();
    const p = Math.round(data.percentage || 0);
    contextUsage.textContent = p + "%";
    contextUsage.style.borderColor = p > 80 ? "var(--danger)" : "";
    const source = data.source === "last_llm_payload" ? "last LLM payload" : "saved chat estimate";
    contextUsage.title = `${Number(data.estimated_tokens || 0).toLocaleString()} estimated tokens, ${Number(data.chars || 0).toLocaleString()} chars (${source})`;
  } catch {}
}
clearSession?.addEventListener("click", async () => {
  try {
    clearSession.disabled = true;
    const response = await fetch("/api/session/clear", {method: "POST"});
    let payload = {};
    try {
      payload = await response.json();
    } catch {}
    if (!response.ok || !payload.ok) throw new Error(payload.error || "Clear failed");
    messages.innerHTML = "";
    if (emptyChat) messages.appendChild(emptyChat);
    refreshContext();
  } catch (error) {
    alert(error.message);
  } finally {
    clearSession.disabled = false;
  }
});
rebuildIndexBtn?.addEventListener("click", async () => {
  try {
    rebuildIndexBtn.disabled = true;
    rebuildIndexBtn.textContent = "Rebuilding...";
    const response = await fetch("/api/semantic-index/rebuild", {method: "POST"});
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "Rebuild failed");
    alert("Semantic index rebuilt successfully");
    refreshContext();
  } catch (error) {
    alert(error.message);
  } finally {
    rebuildIndexBtn.disabled = false;
    rebuildIndexBtn.textContent = "Rebuild Search Index";
  }
});
const dialogPresets = {
  upload: {
    eyebrow: "Upload in progress",
    title: "Preparing your workbook",
    messages: [
      "We are sending the file securely to your workspace.",
      "We are scanning sheets, columns, and sample rows.",
      "We are preparing the import preview for you.",
    ],
    steps: ["Uploading file", "Scanning sheets", "Preparing preview"],
  },
  import: {
    eyebrow: "Data processing",
    title: "Building your analysis workspace",
    messages: [
      "We are turning selected sheets into structured tables.",
      "We are preparing semantic text from the columns you selected.",
      "We are indexing the data so answers can find the right rows.",
    ],
    steps: ["Structuring data", "Preparing semantics", "Indexing rows"],
  },
};
let uploadMessages = dialogPresets.upload.messages;
let uploadStepTimer = null;
let uploadStepIndex = 0;

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!fileInput.files.length) return;
  const file = fileInput.files[0];
  const body = new FormData();
  body.append("file", file);
  setBusy(uploadForm, true);
  showUploadDialog(file, dialogPresets.upload);
  try {
    const response = await fetch("/api/staging", {method: "POST", body});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Upload failed");
    staging = payload;
    renderProfile(payload.profile);
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(uploadForm, false);
    hideUploadDialog();
  }
});

fileInput.addEventListener("change", () => {
  fileName.textContent = fileInput.files[0]?.name || "No file selected";
});

uploadDialog?.addEventListener("cancel", (event) => {
  event.preventDefault();
});

importButton.addEventListener("click", () => {
  importSelected(false);
});

activeDataset.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-dataset-id]");
  if (!button || !button.classList.contains("remove-dataset")) return;
  const datasetId = button.dataset.datasetId;
  if (!confirm("Remove this dataset from the workspace?")) return;
  setBusy(button, true);
  try {
    const response = await fetch(`/api/workbooks/${encodeURIComponent(datasetId)}`, {method: "DELETE"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Delete failed");
    renderActiveDataset(payload.active);
    addMessage("Dataset removed from the workspace.", "bot");
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(button, false);
  }
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    questionInput.value = button.dataset.prompt;
    questionInput.focus();
  });
});

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.dispatchEvent(new Event("submit", {cancelable: true, bubbles: true}));
  }
});

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.dispatchEvent(new Event("submit", {cancelable: true, bubbles: true}));
  }
});

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.dispatchEvent(new Event("submit", {cancelable: true, bubbles: true}));
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;
  sendQuestion(question);
});

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.dispatchEvent(new Event("submit", {cancelable: true, bubbles: true}));
  }
});

function sendQuestion(question) {
  addMessage(question, "user");
  questionInput.value = "";
  const bot = addMessage("Working...", "bot");
  (async () => {
    try {
      const response = await fetch("/api/query", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({question}),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Query failed");
      bot.innerHTML = "";
      bot.innerHTML = parseMd(payload.answer);
      wrapTables();
      refreshContext();
      if (payload.sources?.length) bot.appendChild(renderSources(payload.sources));
    } catch (error) {
      bot.textContent = error.message;
    }
  })();
}

function renderProfile(profile) {
  profilePanel.hidden = false;
  sheetList.innerHTML = "";
  profile.sheets.forEach((sheet) => {
    const card = document.createElement("section");
    card.className = "sheet-card";
    const checked = profile.default_sheets.includes(sheet.name) ? "checked" : "";
    card.innerHTML = `
      <div class="sheet-head">
        <label class="check-row">
          <input type="checkbox" class="sheet-check" data-sheet="${escapeAttr(sheet.name)}" ${checked}>
          <strong>${escapeHtml(sheet.name)}</strong>
        </label>
        <span class="muted">${sheet.rows} rows</span>
      </div>
      <div class="column-list"></div>
    `;
    const columnList = card.querySelector(".column-list");
    sheet.columns
      .filter(column => column.semantic_score > 0 || sheet.suggested_semantic_columns.includes(column.name))
      .slice(0, 12)
      .forEach((column) => {
        const selected = sheet.suggested_semantic_columns.includes(column.name) ? "checked" : "";
        columnList.insertAdjacentHTML("beforeend", `
          <label class="check-row">
            <input type="checkbox" class="semantic-check" data-sheet="${escapeAttr(sheet.name)}" value="${escapeAttr(column.name)}" ${selected}>
            <span>${escapeHtml(column.name)} <small class="muted">score ${column.semantic_score}</small></span>
          </label>
        `);
      });
    sheetList.appendChild(card);
  });
}

async function importSelected(replaceExisting) {
  if (!staging) return;
  const sheets = [...document.querySelectorAll(".sheet-check:checked")].map(input => input.dataset.sheet);
  const semanticColumns = {};
  document.querySelectorAll(".semantic-check:checked").forEach((input) => {
    if (!semanticColumns[input.dataset.sheet]) semanticColumns[input.dataset.sheet] = [];
    semanticColumns[input.dataset.sheet].push(input.value);
  });
  if (!sheets.length) {
    alert("Select at least one sheet/table.");
    return;
  }
  setBusy(importButton, true);
  showUploadDialog({name: staging.profile?.filename}, dialogPresets.import);
  try {
    const response = await fetch("/api/workbooks", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        staging_id: staging.staging_id,
        sheets,
        semantic_columns: semanticColumns,
        replace_existing: replaceExisting,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Import failed");
    renderActiveDataset(payload.active);
    profilePanel.hidden = true;
    addMessage("Dataset imported into the workspace. You can start asking questions.", "bot");
    refreshContext();
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(importButton, false);
    hideUploadDialog();
  }
}

function addMessage(text, kind) {
  emptyChat?.remove();
  const div = document.createElement("div");
  div.className = `message ${kind}`;
  if (kind === "bot") {
    div.innerHTML = parseMd(text);
    wrapTables();
  } else {
    div.textContent = text;
  }
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function renderActiveDataset(active) {
  const datasets = active?.datasets || [];
  activeDataset.dataset.hasActive = datasets.length ? "1" : "0";
  rebuildIndexBtn.hidden = !datasets.length;
  if (!datasets.length) {
    activeDataset.innerHTML = `
      <span class="dataset-dot muted-dot"></span>
      <div>
        <strong>No workspace data</strong>
        <span>Upload a spreadsheet to begin.</span>
      </div>
    `;
    if (chatStatus) chatStatus.textContent = "No dataset";
    return;
  }
  activeDataset.innerHTML = `
    <div class="dataset-list">
      ${datasets.map(dataset => `
        <div class="dataset-item" data-dataset-id="${escapeAttr(dataset.id)}">
          <span class="dataset-dot"></span>
          <div>
            <strong>${escapeHtml(dataset.filename)}</strong>
            <span>${tableLabel(dataset.tables.length)} active</span>
          </div>
          <button class="remove-dataset" type="button" data-dataset-id="${escapeAttr(dataset.id)}" title="Remove dataset">Remove</button>
        </div>
      `).join("")}
    </div>
  `;
  if (chatStatus) chatStatus.textContent = datasetLabel(datasets.length);
}

function datasetLabel(count) {
  return `${count} ${count === 1 ? "dataset" : "datasets"}`;
}

function tableLabel(count) {
  return `${count} ${count === 1 ? "table" : "tables"}`;
}

function renderSources(sources) {
  const wrap = document.createElement("div");
  wrap.className = "sources";
  sources.slice(0, 10).forEach((source) => {
    const pill = document.createElement("span");
    pill.className = "source-pill";
    const file = source.file ? `${source.file} / ` : "";
    pill.textContent = `${file}${source.sheet || "sheet"} row ${source.row || "?"}`;
    wrap.appendChild(pill);
  });
  return wrap;
}

function setBusy(element, busy) {
  const buttons = element.tagName === "BUTTON" ? [element] : [...element.querySelectorAll("button")];
  buttons.forEach((button) => button.disabled = busy);
}

function showUploadDialog(file, preset = dialogPresets.upload) {
  if (!uploadDialog) return;
  uploadMessages = preset.messages;
  uploadDialogEyebrow.textContent = preset.eyebrow;
  uploadDialogTitle.textContent = preset.title;
  uploadDialogFile.textContent = file?.name ? file.name : "";
  uploadSteps.forEach((step, index) => {
    step.textContent = preset.steps[index] || step.textContent;
  });
  setUploadStep(0);
  if (typeof uploadDialog.showModal === "function") {
    if (!uploadDialog.open) uploadDialog.showModal();
  } else {
    uploadDialog.setAttribute("open", "");
  }
  uploadStepTimer = window.setInterval(() => {
    setUploadStep((uploadStepIndex + 1) % uploadMessages.length);
  }, 1300);
}

function hideUploadDialog() {
  if (uploadStepTimer) {
    window.clearInterval(uploadStepTimer);
    uploadStepTimer = null;
  }
  if (!uploadDialog) return;
  if (typeof uploadDialog.close === "function" && uploadDialog.open) {
    uploadDialog.close();
  } else {
    uploadDialog.removeAttribute("open");
  }
}

function setUploadStep(index) {
  uploadStepIndex = index;
  uploadSteps.forEach((step, current) => {
    step.classList.toggle("active", current === index);
  });
  if (uploadDialogText) uploadDialogText.textContent = uploadMessages[index];
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

function escapeAttr(text) {
  return escapeHtml(text);
}

(function () {
  window.wrapTables = function () {
    const wrap = (table) => {
      if (table.closest('.table-scroll')) return;
      const div = document.createElement('div');
      div.className = 'table-scroll';
      table.replaceWith(div);
      div.appendChild(table);
    };
    messages.querySelectorAll('table').forEach(wrap);
  };
  window.wrapTables();
  refreshContext();
})();

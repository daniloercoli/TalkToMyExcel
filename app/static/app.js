let staging = null;

const uploadForm = document.getElementById("uploadForm");
const fileInput = document.getElementById("fileInput");
const fileName = document.getElementById("fileName");
const profilePanel = document.getElementById("profilePanel");
const sheetList = document.getElementById("sheetList");
const importButton = document.getElementById("importButton");
const activeDataset = document.getElementById("activeDataset");
const chatStatus = document.getElementById("chatStatus");
const messages = document.getElementById("messages");
const emptyChat = document.getElementById("emptyChat");
const chatForm = document.getElementById("chatForm");
const questionInput = document.getElementById("questionInput");

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!fileInput.files.length) return;
  const body = new FormData();
  body.append("file", fileInput.files[0]);
  setBusy(uploadForm, true);
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
  }
});

fileInput.addEventListener("change", () => {
  fileName.textContent = fileInput.files[0]?.name || "No file selected";
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

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;
  addMessage(question, "user");
  questionInput.value = "";
  const bot = addMessage("Working...", "bot");
  try {
    const response = await fetch("/api/query", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question}),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Query failed");
    bot.textContent = payload.answer;
    if (payload.sources?.length) bot.appendChild(renderSources(payload.sources));
  } catch (error) {
    bot.textContent = error.message;
  }
});

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
  } catch (error) {
    alert(error.message);
  } finally {
    setBusy(importButton, false);
  }
}

function addMessage(text, kind) {
  emptyChat?.remove();
  const div = document.createElement("div");
  div.className = `message ${kind}`;
  div.textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function renderActiveDataset(active) {
  const datasets = active?.datasets || [];
  activeDataset.dataset.hasActive = datasets.length ? "1" : "0";
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

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

function escapeAttr(text) {
  return escapeHtml(text);
}

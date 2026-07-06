// Браузер ходит на demo/web (:8080), который проксирует:
//   /api/data/*       -> data-service:8084
//   /api/rag/documents -> rag:8082
//   /api/{chat,backlog,session/history} -> demo/api:8081 (агент)
const apiBase = window.DEMO_API_BASE || `${window.location.protocol}//${window.location.host}`;

const chatHistoryKey = "agentTutorMessages";
const tenantStorageKey = "agentTutorTenantId";
const agentStorageKey = "agentTutorAgentId";
const storage = window.localStorage;

const state = {
  data: null,
  tab: null,
  filter: "",
  manifest: null,
  tenantId: null,      // current tenant
  tenants: [],          // available tenants
  agentId: null,       // selected agent name
  agents: [],          // agents list
};

// SSE Debug logging
const sseDebug =
  window.DEMO_DEBUG === "1" || window.location.search.includes("sse_debug=1");
function sseLog(...args) {
  if (sseDebug) console.log("[SSE]", ...args);
}

// Constants
const THINKING_MESSAGE = "Думаю и проверяю данные...";

// ── Manifest — единственный источник вкладок и колонок ──
// Пока manifest не загружен — placeholder "Загрузка сущностей…" из HTML.
// Никакого university-специфичного хардкода.

const $ = (selector) => document.querySelector(selector);

let currentSessionId;
let manifestRetries = 0;
const MANIFEST_MAX_RETRIES = 10;
const MANIFEST_RETRY_MS = 3000; // 3 секунды между попытками

// ── Tenant-aware fetch ──

function fetchWithTenant(url, options = {}) {
  const headers = options.headers || {};
  // Only add X-Tenant-ID if we have a non-default tenant selected
  if (state.tenantId) {
    headers["X-Tenant-ID"] = state.tenantId;
  }
  return fetch(url, { ...options, headers });
}

// ── Init ──

async function init() {
  // Restore tenant from localStorage
  const savedTenantId = readTenantId();

  bindChat();
  showTabPlaceholder();
  // Параллельно: health + tenant list + agents (загружаем всё, кроме сессии)
  await Promise.all([
    checkHealth(),
    loadTenants(savedTenantId),  // также вызывает loadAgents — восстанавливает state.agentId
  ]);
  // Теперь state.agentId известен — создаём сессию под правильным ключом
  currentSessionId = getSessionId();
  await restoreServerHistory();
  // Manifest загружается после того как tenant выбран
  await loadManifest();
}

// ── Placeholder пока manifest не пришёл ──

function showTabPlaceholder() {
  const tabBar = document.getElementById("tabBar");
  if (!tabBar) return;
  tabBar.innerHTML = '<span class="tab-placeholder">Загрузка сущностей…</span>';
  $("#tableTitle").textContent = "Загрузка…";
  $("#tableBody").innerHTML = "";
  $("#metrics").innerHTML = "";

  // Фильтр — привязываем сразу (будет работать после загрузки вкладок)
  $("#filter").addEventListener("input", (event) => {
    state.filter = event.target.value.toLowerCase();
    renderTable();
  });
}

// ── Manifest (опционально, перестраивает вкладки если пришёл) ──

// ── Manifest (повторяет попытки пока не загрузится) ──

async function loadManifest() {
  while (manifestRetries < MANIFEST_MAX_RETRIES) {
    try {
      const response = await fetchWithTenant(`${apiBase}/api/manifest`);
      if (response.ok) {
        state.manifest = await response.json();
        buildTabsFromManifest();
        // Загружаем данные для первой вкладки
        if (state.tab) await loadData();
        return;
      }
    } catch (_) {
      // data-service ещё не готов — подождём и попробуем снова
    }
    manifestRetries++;
    console.warn(`Manifest retry ${manifestRetries}/${MANIFEST_MAX_RETRIES}…`);
    await new Promise((r) => setTimeout(r, MANIFEST_RETRY_MS));
  }
  // Исчерпали попытки — показываем ошибку
  const tabBar = document.getElementById("tabBar");
  if (tabBar) tabBar.innerHTML = '<span class="tab-placeholder" style="color:#b4235a">Не удалось загрузить сущности. Обновите страницу.</span>';
}

function buildTabsFromManifest() {
  const tabBar = document.getElementById("tabBar");
  if (!tabBar) return;

  const endpoints = state.manifest?.endpoints || [];
  const tabOps = ["list", "find", "custom_query"];
  const collectionEndpoints = endpoints.filter(
    (ep) => ep.method === "GET" && tabOps.includes(ep.op) && !ep.path.includes("{")
  );

  // Собираем уникальные вкладки
  const seen = new Set();
  const tabs = [];
  collectionEndpoints.forEach((ep) => {
    const key = ep.path.replace(/^\//, "");
    if (!seen.has(key)) {
      seen.add(key);
      tabs.push({ key, ep });
    }
  });

  if (tabs.length === 0) return; // ничего не нашли — оставляем fallback

  // Сохраняем текущую активную вкладку
  const currentTab = state.tab;

  // Перестраиваем кнопки
  tabBar.innerHTML = "";

  tabs.forEach(({ key, ep }) => {
    const entity = ep.entity
      ? (state.manifest?.entities || []).find((e) => e.name === ep.entity)
      : null;
    const label = entity
      ? entity.name.charAt(0).toUpperCase() + entity.name.slice(1)
      : key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " ");

    const btn = document.createElement("button");
    btn.className = "tab";
    btn.dataset.tab = key;
    btn.textContent = label;
    btn.addEventListener("click", () => switchTab(key, btn));
    tabBar.appendChild(btn);
  });

  // Добавляем documents
  const docBtn = document.createElement("button");
  docBtn.className = "tab";
  docBtn.dataset.tab = "documents";
  docBtn.textContent = "Documents";
  docBtn.addEventListener("click", () => switchTab("documents", docBtn));
  tabBar.appendChild(docBtn);

  // Восстанавливаем активную вкладку
  const target = currentTab || tabs[0].key;
  const targetBtn = tabBar.querySelector(`[data-tab="${target}"]`);
  if (targetBtn) {
    switchTab(target, targetBtn);
  } else {
    const first = tabBar.querySelector(".tab");
    if (first) switchTab(first.dataset.tab, first);
  }
}

// ── Tenant Selector ──

function readTenantId() {
  try {
    return storage.getItem(tenantStorageKey) || null;
  } catch {
    return null;
  }
}

function writeTenantId(id) {
  try {
    if (id) {
      storage.setItem(tenantStorageKey, id);
    } else {
      storage.removeItem(tenantStorageKey);
    }
  } catch { /* ignore */ }
}

async function loadTenants(preferredTenantId) {
  const select = $("#tenantSelect");
  if (!select) return;

  try {
    const resp = await fetch(`${apiBase}/api/tenants`);
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const data = await resp.json();
    state.tenants = data.tenants || [];
  } catch (err) {
    console.warn("Failed to load tenants:", err);
    state.tenants = ["default"];
  }

  if (state.tenants.length === 0) {
    state.tenants = ["default"];
  }

  // Determine active tenant
  let activeTenant = preferredTenantId;
  if (!activeTenant || !state.tenants.includes(activeTenant)) {
    activeTenant = state.tenants[0];
  }

  // Populate select
  select.innerHTML = state.tenants
    .map((t) => `<option value="${escapeHtml(t)}" ${t === activeTenant ? "selected" : ""}>${escapeHtml(t)}</option>`)
    .join("");

  // Set initial tenant
  setTenantId(activeTenant);

  // Listen for changes
  select.addEventListener("change", async () => {
    const newTenantId = select.value;
    if (newTenantId === state.tenantId) return;
    await setTenantId(newTenantId);
    // Полный перезапуск UI для нового tenant'а
    await reloadForNewTenant();
  });

  // Load agents
  await loadAgents();
}

async function loadAgents() {
  const select = $("#agentSelect");
  if (!select) return;

  try {
    const resp = await fetch(`${apiBase}/api/agents`);
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const data = await resp.json();
    state.agents = data.agents || [];
  } catch (err) {
    console.warn("Failed to load agents:", err);
    state.agents = [];
  }

  // Restore stored agent
  let activeAgent = null;
  try { activeAgent = storage.getItem(agentStorageKey); } catch {}
  if (activeAgent && !state.agents.find(function(a) { return a.name === activeAgent; })) {
    activeAgent = null;
  }

  select.innerHTML = '<option value="">Нет (прямой чат с tenant)</option>' +
    state.agents.map(function(a) {
      var sel = a.name === activeAgent ? 'selected' : '';
      return '<option value="' + escapeHtml(a.name) + '" ' + sel + '>' + escapeHtml(a.name) + '</option>';
    }).join('');

  if (activeAgent) {
    state.agentId = activeAgent;
    select.value = activeAgent;
  }

  select.addEventListener("change", function() {
    var val = select.value;
    state.agentId = val || null;
    try {
      if (val) { storage.setItem(agentStorageKey, val); }
      else { storage.removeItem(agentStorageKey); }
    } catch {}
    // Switch session: новый ключ по агенту → новая сессия → грузим историю
    currentSessionId = getSessionId();
    restoreServerHistory();
    // Очищаем сообщение-приветствие если есть
    var messagesEl = $("#messages");
    if (messagesEl) {
      messagesEl.innerHTML = "";
    }
  });
}

function setTenantId(id) {
  state.tenantId = id;
  writeTenantId(id);
  // Update select visual if populated
  const select = $("#tenantSelect");
  if (select && select.value !== id) {
    select.value = id;
  }
}

async function reloadForNewTenant() {
  // Сброс и перезагрузка всего
  showTabPlaceholder();
  // Сброс manifest (загрузим заново под новым tenant)
  state.manifest = null;
  state.tab = null;
  state.data = null;
  manifestRetries = 0;

  // Генерируем новую сессию для нового tenant'а
  currentSessionId = getSessionId();

  // Очищаем историю чата (разные tenant'ы — разные данные)
  writeStoredMessages([]);
  $("#messages").innerHTML =
    '<div class="message assistant">Спросите про студента, оценки, расписание или материалы.</div>';

  // Reload manifest
  await loadManifest();
}

function switchTab(tabKey, btn) {
  state.tab = tabKey;
  state.filter = "";
  $("#filter").value = "";
  document
    .querySelectorAll(".tab")
    .forEach((t) => t.classList.toggle("active", t === btn));
  loadData();
}

// ── Загрузка данных ──

async function loadData() {
  try {
    let stats, docs, tabData;

    if (state.tab === "documents") {
      [stats, docs] = await Promise.all([
        fetchWithTenant(`${apiBase}/api/data/stats`).then((r) => (r.ok ? r.json() : null)),
        fetchWithTenant(`${apiBase}/api/rag/documents`).then((r) => (r.ok ? r.json() : null)),
      ]);
      tabData = [];

      const documentList = docs?.documents || docs || [];
      state.data = {
        stats: stats || {},
        documents: documentList,
      };
      state.data[state.tab] = documentList;
      renderMetrics();
      renderTable();
      return;
    } else {
      [stats, docs, tabData] = await Promise.all([
        fetchWithTenant(`${apiBase}/api/data/stats`).then((r) => (r.ok ? r.json() : null)),
        fetchWithTenant(`${apiBase}/api/rag/documents`).then((r) => (r.ok ? r.json() : null)),
        fetchWithTenant(`${apiBase}/api/data/${state.tab}`).then((r) => (r.ok ? r.json() : null)),
      ]);
    }

    state.data = {
      stats: stats || {},
      tabData: Array.isArray(tabData) ? tabData : [],
      documents: (docs?.documents || docs || []),
    };
    state.data[state.tab] = state.data.tabData;
  } catch (err) {
    console.warn("loadData failed:", err);
    state.data = { stats: {}, tabData: [], documents: [] };
    state.data[state.tab] = [];
  }

  renderMetrics();
  renderTable();
}

// ── Метрики ──

function renderMetrics() {
  const root = $("#metrics");
  const stats = state.data?.stats || {};
  root.innerHTML = Object.entries(stats)
    .map(([key, value]) => {
      const label =
        key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " ");
      return `<div class="metric"><b>${value}</b><span>${label}</span></div>`;
    })
    .join("");
}

// ── Таблица ──

function getTabColumns() {
  // Documents — специальная вкладка (RAG), не из manifest
  if (state.tab === "documents") {
    return {
      title: "Documents",
      columns: [
        ["title", "Title"],
        ["discipline_name", "Discipline"],
        ["mime_type", "Type"],
        ["created_at", "Added"],
      ],
    };
  }

  // Data-service вкладки — kolонки из entity.fields
  if (state.manifest) {
    const endpoint = (state.manifest.endpoints || []).find(
      (ep) => ep.path === "/" + state.tab && ep.entity
    );
    if (endpoint) {
      const entity = (state.manifest.entities || []).find((e) => e.name === endpoint.entity);
      if (entity && entity.fields && entity.fields.length > 0) {
        return {
          title: entity.name.charAt(0).toUpperCase() + entity.name.slice(1),
          columns: entity.fields.map((f) => [
            f.name,
            f.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
          ]),
        };
      }
    }
  }

  // Без manifest — raw keys из первого ряда данных
  const rows = state.data[state.tab] || [];
  if (rows.length > 0 && typeof rows[0] === "object") {
    const keys = Object.keys(rows[0]);
    return {
      title: state.tab.charAt(0).toUpperCase() + state.tab.slice(1),
      columns: keys.map((k) => [k, k]),
    };
  }

  return { title: state.tab || "Data", columns: [["id", "ID"]] };
}

function formatCell(value) {
  if (value === null || value === undefined) return "\u2014";

  // Массив объектов — показываем количество + компактный список
  if (Array.isArray(value)) {
    if (value.length === 0) return "[ ]";
    if (typeof value[0] === "object" && value[0] !== null) {
      // Ищем читаемое поле: name, title, discipline_name, teacher_name, day, time_slot
      const labelKey =
        value[0].discipline_name ? "discipline_name" :
        value[0].teacher_name ? "teacher_name" :
        value[0].name ? "name" :
        value[0].title ? "title" :
        Object.keys(value[0])[0];
      const items = value
        .slice(0, 4)
        .map((it) => escapeHtml(String(it[labelKey] ?? JSON.stringify(it))))
        .join(", ");
      const more = value.length > 4 ? ` +${value.length - 4}` : "";
      return `<span title="${escapeHtml(JSON.stringify(value, null, 2))}">${items}${more}</span>`;
    }
    // Массив примитивов — через запятую
    const items = value.slice(0, 5).map((v) => escapeHtml(String(v))).join(", ");
    const more = value.length > 5 ? ` +${value.length - 5}` : "";
    return `<span title="${escapeHtml(JSON.stringify(value))}">${items}${more}</span>`;
  }

  // Объект (не массив) — компактный JSON
  if (typeof value === "object") {
    const keys = Object.keys(value);
    const snippet = keys
      .slice(0, 3)
      .map((k) => `${k}: ${String(value[k]).substring(0, 30)}`)
      .join(", ");
    const more = keys.length > 3 ? ` … +${keys.length - 3}` : "";
    return `<span title="${escapeHtml(JSON.stringify(value, null, 2))}">{ ${escapeHtml(snippet)}${more} }</span>`;
  }

  return escapeHtml(String(value));
}

function renderTable() {
  if (!state.tab) return;

  const config = getTabColumns();
  const rows = (state.data?.[state.tab] || []).filter((row) =>
    JSON.stringify(row).toLowerCase().includes(state.filter)
  );

  $("#tableTitle").textContent = config.title;
  $("#tableHead").innerHTML =
    "<tr>" +
    config.columns
      .map(([, title]) => "<th>" + escapeHtml(title) + "</th>")
      .join("") +
    "</tr>";
  $("#tableBody").innerHTML = rows
    .map((row) => {
      const cells = config.columns.map(([key]) =>
        "<td>" + formatCell(row[key]) + "</td>"
      );
      return "<tr>" + cells.join("") + "</tr>";
    })
    .join("");
}

// ── Health ──

async function checkHealth() {
  const status = $("#status");
  try {
    const response = await fetch(`${apiBase}/health`);
    const data = await response.json();
    status.textContent =
      data.ollama?.status === "ok"
        ? `API: ${data.ollama.model}`
        : "API: Ollama недоступна";
    status.style.background =
      data.ollama?.status === "ok" ? "#eaf7f5" : "#fff7ed";
    status.style.color = data.ollama?.status === "ok" ? "#0b5f59" : "#a15c07";
  } catch {
    status.textContent = "API: недоступен";
    status.style.background = "#fff1f3";
    status.style.color = "#b4235a";
  }
}

// ── История чата с сервера ──

async function restoreServerHistory() {
  const messages = $("#messages");
  messages.innerHTML = "";
  let pendingToolNames = [];

  try {
    if (!currentSessionId) return;

    const url = state.agentId
      ? `${apiBase}/api/session/history?session_id=${encodeURIComponent(currentSessionId)}&agent_name=${encodeURIComponent(state.agentId)}`
      : `${apiBase}/api/session/history?session_id=${encodeURIComponent(currentSessionId)}`;
    const response = await fetchWithTenant(url);
    if (!response.ok) return;

    const data = await response.json();
    const serverMessages = data.messages || [];

    for (const msg of serverMessages) {
      if (msg.role === "user") {
        addMessage("user", msg.content || "", { persist: false, scroll: false });
      } else if (msg.role === "assistant") {
        const toolNames = normalizeToolNames(msg.tool_calls);
        const content = stripLeadingToolLabels(String(msg.content || ""), toolNames);

        if (!content.trim() && toolNames.length > 0) {
          pendingToolNames = normalizeToolNames([...pendingToolNames, ...toolNames]);
          continue;
        }

        const mergedTools = normalizeToolNames([...pendingToolNames, ...toolNames]);
        pendingToolNames = [];

        if (mergedTools.length > 0) {
          messages.appendChild(createToolStripNode(mergedTools));
        }

        const node = document.createElement("div");
        node.className = "message assistant";
        node.dataset.raw = content;
        node.innerHTML = renderAssistantMarkup(content);
        messages.appendChild(node);
        appendStoredMessage("assistant", content, mergedTools);
      }
    }

    if (pendingToolNames.length > 0) {
      messages.appendChild(createToolStripNode(pendingToolNames));
      appendStoredMessage("assistant", "", pendingToolNames);
    }

    scrollMessagesToBottom(messages);
  } catch { /* ignore */ }
}

// ── Чат ──

function bindChat() {
  const form = $("#chatForm");
  const input = $("#chatInput");
  const widget = $("#chatWidget");
  const open = $("#openChat");

  $("#collapseChat").addEventListener("click", () => {
    widget.classList.add("hidden");
    open.classList.remove("hidden");
  });

  open.addEventListener("click", () => {
    widget.classList.remove("hidden");
    open.classList.add("hidden");
    input.focus();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    addMessage("user", text, { persist: true });
    const answer = addMessage("assistant", "", { persist: false });
    await streamChat(text, answer);
  });
}

async function streamChat(message, target) {
  target.classList.add("is-thinking");
  target.dataset.tools = "[]";
  target.dataset.saved = "false";
  sseLog("Stream started, message:", message.substring(0, 50));
  try {
    // If agent selected, use /api/chat/{agentName} — agent's tenant_ids from config
    // Otherwise use /api/chat with tenant from dropdown
    var chatEndpoint = state.agentId
      ? apiBase + "/api/chat/" + encodeURIComponent(state.agentId)
      : apiBase + "/api/chat";

    var headers = { "Content-Type": "application/json" };
    if (!state.agentId && state.tenantId) {
      headers["X-Tenant-ID"] = state.tenantId;
    }

    const response = await fetch(chatEndpoint, {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ message, session_id: currentSessionId }),
    });
    sseLog("SSE connection established, status:", response.status);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let chunkCount = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) { sseLog("SSE stream completed, total chunks:", chunkCount); break; }
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop();
      for (const chunk of chunks) {
        chunkCount++;
        sseLog("Chunk #" + chunkCount + ":", chunk.substring(0, 100));
        handleEventChunk(chunk, target);
      }
    }
  } catch (error) {
    sseLog("SSE error:", error);
    target.classList.add("error");
    target.textContent = `Ошибка: ${error.message}`;
  }
}

function getSessionId() {
  // Каждый агент и no-agent режим имеют свой localStorage-ключ
  var storageKey = state.agentId
    ? "agentTutorSessionId_" + state.agentId
    : "agentTutorSessionId";
  try {
    const existing = storage.getItem(storageKey);
    if (existing) return existing;
    const generated = createSessionId();
    storage.setItem(storageKey, generated);
    return generated;
  } catch {
    return createSessionId();
  }
}

function createSessionId() {
  return (
    window.crypto?.randomUUID?.() ||
    `session-${Date.now()}-${Math.random().toString(16).slice(2)}`
  );
}

function handleEventChunk(chunk, target) {
  const line = chunk.split("\n").find((item) => item.startsWith("data:"));
  if (!line) { sseLog("No data line found in chunk"); return; }
  const payload = JSON.parse(line.slice(5).trim());
  sseLog("Event type:", payload.type, payload);
  clearAssistantThinking(target);
  if (payload.type === "token") {
    sseLog("Token received:", payload.text?.substring(0, 50));
    const messages = $("#messages");
    const shouldStickToBottom = isScrolledNearBottom(messages);
    if (target.dataset.raw === THINKING_MESSAGE) {
      target.dataset.raw = "";
      target.innerHTML = "";
    }
    appendAssistantToken(target, payload.text);
    if (shouldStickToBottom) scrollMessagesToBottom(messages);
  }
  if (payload.type === "final") {
    sseLog("Final text received:", payload.text?.substring(0, 100));
    const messages = $("#messages");
    const shouldStickToBottom = isScrolledNearBottom(messages);
    const currentRaw = target.dataset.raw || "";
    const finalText = typeof payload.text === "string" ? payload.text : "";
    if (target.dataset.raw === THINKING_MESSAGE) {
      target.dataset.raw = "";
      target.innerHTML = "";
    }
    setAssistantText(target, mergeAssistantText(currentRaw, finalText));
    if (shouldStickToBottom) scrollMessagesToBottom(messages);
  }
  if (payload.type === "tool_call") {
    sseLog("Tool call:", payload.name, payload.arguments);
    const tools = JSON.parse(target.dataset.tools || "[]");
    if (!tools.includes(payload.name)) {
      tools.push(payload.name);
      target.dataset.tools = JSON.stringify(tools);
    }
    ensureAssistantToolStrip(target, tools);
  }
  if (payload.type === "done" && !(target.dataset.raw || "").trim()) {
    sseLog("Done event (empty response)");
    target.dataset.raw = "Модель не вернула текст. Попробуйте уточнить запрос.";
    target.textContent = target.dataset.raw;
  }
  if (payload.type === "done") {
    sseLog("Done event - saving message");
    saveAssistantMessage(target);
  }
  if (payload.type === "error") {
    sseLog("Error event:", payload.text);
    target.classList.add("error");
    target.textContent = `Ошибка: ${payload.text}`;
  }
}

function appendAssistantToken(target, text) {
  setAssistantText(target, `${target.dataset.raw || ""}${text}`);
}

function setAssistantText(target, raw) {
  clearAssistantThinking(target);
  target.dataset.raw = raw;
  target.innerHTML = renderAssistantMarkup(raw);
}

function clearAssistantThinking(target) {
  if (target.classList.contains("is-thinking")) target.classList.remove("is-thinking");
}

function mergeAssistantText(currentRaw, finalText) {
  const current = String(currentRaw || "");
  const final = String(finalText || "");
  if (!final) return current;
  if (!current) return final;
  if (final.startsWith(current)) return final;
  if (current.startsWith(final)) return current;
  return final.length >= current.length ? final : current;
}

// ── Markdown rendering ──

function renderAssistantMarkup(raw) {
  const chunks = [];
  const text = raw.trim();
  if (!text) return "";

  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length) {
    if (isTableStart(lines, i)) {
      const tableLines = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) { tableLines.push(lines[i]); i += 1; }
      chunks.push(renderMarkdownTable(tableLines));
      continue;
    }
    if (/^\s*[-*]\s+/.test(lines[i])) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*]\s+/, "")); i += 1; }
      chunks.push(`<ul>${items.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(lines[i])) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*\d+\.\s+/, "")); i += 1; }
      chunks.push(`<ol>${items.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</ol>`);
      continue;
    }
    const paragraph = [];
    while (
      i < lines.length && lines[i].trim() &&
      !isTableStart(lines, i) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+\.\s+/.test(lines[i])
    ) { paragraph.push(lines[i]); i += 1; }
    if (paragraph.length) {
      chunks.push(`<p>${inlineMarkdown(paragraph.join("\n")).replaceAll("\n", "<br>")}</p>`);
    }
    i += 1;
  }
  return chunks.join("");
}

function renderToolStrip(toolNames = []) {
  const uniqueTools = [...new Set(normalizeToolNames(toolNames))];
  if (!uniqueTools.length) return "";
  return `<div class="tool-strip">${uniqueTools.map((name) => `<span>tool: ${escapeHtml(name)}</span>`).join("")}</div>`;
}

function createToolStripNode(toolNames = []) {
  const node = document.createElement("div");
  node.className = "assistant-tools";
  node.dataset.tools = JSON.stringify(normalizeToolNames(toolNames));
  node.innerHTML = renderToolStrip(toolNames);
  return node;
}

function ensureAssistantToolStrip(target, toolNames = []) {
  const normalizedTools = normalizeToolNames(toolNames);
  if (!normalizedTools.length) return null;
  const messages = $("#messages");
  const previous = target.previousElementSibling;
  if (previous && previous.classList.contains("assistant-tools")) {
    previous.dataset.tools = JSON.stringify(normalizedTools);
    previous.innerHTML = renderToolStrip(normalizedTools);
    return previous;
  }
  const node = createToolStripNode(normalizedTools);
  messages.insertBefore(node, target);
  return node;
}

function stripLeadingToolLabels(text, toolNames = []) {
  let value = String(text || "");
  const normalizedTools = normalizeToolNames(toolNames);
  if (!normalizedTools.length || !value.startsWith("tool:")) return value;
  let changed = true;
  while (changed) {
    changed = false;
    for (const name of normalizedTools) {
      const prefix = `tool: ${name}`;
      if (value.startsWith(prefix)) { value = value.slice(prefix.length); changed = true; }
    }
  }
  return value.trimStart();
}

function isTableStart(lines, index) {
  return Boolean(lines[index]?.trim().startsWith("|") && lines[index + 1]?.includes("---"));
}

function renderMarkdownTable(lines) {
  const rows = lines
    .filter((line) => !/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line))
    .map((line) =>
      line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim())
    );
  if (!rows.length) return "";
  const [head, ...body] = rows;
  return `<div class="markdown-table"><table><thead><tr>${head.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function normalizeToolNames(toolCalls = []) {
  if (!Array.isArray(toolCalls)) return [];
  return toolCalls
    .map((toolCall) => {
      if (typeof toolCall === "string") return toolCall.trim();
      if (!toolCall || typeof toolCall !== "object") return "";
      const directName = toolCall.name ?? toolCall.tool_name;
      if (typeof directName === "string") return directName.trim();
      const functionName = toolCall.function?.name;
      return typeof functionName === "string" ? functionName.trim() : "";
    })
    .filter(Boolean);
}

function inlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

// ── Messages ──

function addMessage(kind, text, options = {}) {
  const node = document.createElement("div");
  node.className = `message ${kind}`;
  if (kind === "assistant") {
    node.dataset.raw = text;
    const tools = normalizeToolNames(options.tools || []);
    if (tools.length > 0) node.dataset.tools = JSON.stringify(tools);
    node.innerHTML = renderAssistantMarkup(text);
  } else {
    node.textContent = text;
  }
  const messages = $("#messages");
  messages.appendChild(node);
  if (options.persist) appendStoredMessage(kind, text, options.tools);
  if (options.scroll !== false) scrollMessagesToBottom(messages);
  return node;
}

function restoreChatHistory() {
  const storedMessages = readStoredMessages();
  if (!storedMessages.length) return;
  const messages = $("#messages");
  messages.innerHTML = "";
  let pendingToolNames = [];
  storedMessages.forEach((message) => {
    if (message.kind === "assistant") {
      const tools = normalizeToolNames(message.tools || []);
      const text = stripLeadingToolLabels(String(message.text || ""), tools);
      if (!text.trim() && tools.length > 0) {
        pendingToolNames = normalizeToolNames([...pendingToolNames, ...tools]);
        return;
      }
      const mergedTools = normalizeToolNames([...pendingToolNames, ...tools]);
      pendingToolNames = [];
      const node = document.createElement("div");
      node.className = `message ${message.kind}`;
      node.dataset.raw = text;
      if (mergedTools.length > 0) {
        node.dataset.tools = JSON.stringify(mergedTools);
        messages.appendChild(createToolStripNode(mergedTools));
      }
      node.innerHTML = renderAssistantMarkup(text);
      messages.appendChild(node);
    } else {
      addMessage(message.kind, message.text, { persist: false, scroll: false });
    }
  });
  if (pendingToolNames.length > 0) messages.appendChild(createToolStripNode(pendingToolNames));
  scrollMessagesToBottom(messages);
}

function saveAssistantMessage(target) {
  if (target.dataset.saved === "true") return;
  const text = String(target.dataset.raw || "");
  const tools = normalizeToolNames(JSON.parse(target.dataset.tools || "[]"));
  appendStoredMessage("assistant", text, tools);
  target.dataset.saved = "true";
}

function appendStoredMessage(kind, text, tools = []) {
  const normalizedTools = normalizeToolNames(tools);
  const value =
    kind === "assistant"
      ? stripLeadingToolLabels(String(text || ""), normalizedTools).trim()
      : String(text || "").trim();
  if (!value && normalizedTools.length === 0) return;
  const messages = readStoredMessages();
  const message = { kind, text: value };
  if (normalizedTools.length > 0) message.tools = normalizedTools;
  messages.push(message);
  writeStoredMessages(messages);
}

function readStoredMessages() {
  try {
    const raw = storage.getItem(chatHistoryKey);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isStoredMessage);
  } catch { return []; }
}

function writeStoredMessages(messages) {
  try { storage.setItem(chatHistoryKey, JSON.stringify(messages)); } catch {}
}

function isStoredMessage(value) {
  return value && ["user", "assistant"].includes(value.kind) && typeof value.text === "string";
}

function isScrolledNearBottom(node) {
  return node.scrollHeight - node.scrollTop - node.clientHeight < 48;
}

function scrollMessagesToBottom(node = $("#messages")) {
  node.scrollTop = node.scrollHeight;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

init();

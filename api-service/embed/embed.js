/**
 * Helperium — Embeddable Chat Widget
 * ======================================
 * Vanilla JS, no dependencies. Shadow DOM isolation.
 *
 * Usage:
 *   <script src="/embed/embed.js"
 *           data-agent="support-agent"
 *           data-api-base="https://your-server.com"
 *           data-title="Assistant"
 *           data-greeting="How can I help?"
 *           data-accent="#0f766e"
 *           data-position="right">
 *   </script>
 *
 * SSE endpoint: POST {api-base}/api/chat/{agent}
 * Body: {"message": "...", "session_id": "..."}
 * Response: text/event-stream with data: {json} events
 */
(function () {
  'use strict';

  /* ─── Configuration ─── */
  var script = document.currentScript;
  if (!script) {
    script = document.querySelector('script[data-agent]');
  }
  if (!script) return;

  var CONFIG = {
    agent: script.getAttribute('data-agent') || '',
    apiBase: script.getAttribute('data-api-base') || window.location.origin,
    title: script.getAttribute('data-title') || 'Assistant',
    greeting: script.getAttribute('data-greeting') || 'How can I help?',
    accent: script.getAttribute('data-accent') || '#0f766e',
    position: script.getAttribute('data-position') === 'left' ? 'left' : 'right',
    lang: script.getAttribute('data-lang') || (navigator.language.startsWith('ru') ? 'ru' : 'en'),
    placeholder: script.getAttribute('data-placeholder') || 'Ask a question...',
    width: script.getAttribute('data-width') || 'min(380px, calc(100vw - 28px))',
    height: script.getAttribute('data-height') || 'min(620px, calc(100vh - 44px))',
    triggerOffsetBottom: script.getAttribute('data-trigger-offset-bottom') || '16px',
    headerColor: script.getAttribute('data-header-color') || '',
    showHeader: script.getAttribute('data-show-header') !== 'false',
    botBubbleColor: script.getAttribute('data-bot-bubble-color') || '#eef3f4',
    botBubbleText: script.getAttribute('data-bot-bubble-text') || 'var(--ink)',
    voiceInput: script.getAttribute('data-voice-input') !== 'false',
    voiceOutput: script.getAttribute('data-voice-output') !== 'false'
  };

  if (!CONFIG.agent) {
    console.error('[AgentTutor] Missing data-agent attribute');
    return;
  }

  /* ─── Global API bridge: allows app.js to switch agent at runtime ─── */
  window.__agentTutorSetAgent = null;
  /* ─── State ─── */
  var state = {
    sessionId: null,
    open: false,
    messages: []
  };
  var STORAGE_KEY = 'at_messages_' + CONFIG.agent;
  var SESSION_KEY = 'at_session_' + CONFIG.agent;

  /* ─── CSS (embedded for Shadow DOM) ─── */
  function getWidgetCSS(cfg) {
    var headerBg = cfg.headerColor || cfg.accent;
    var headDisplay = cfg.showHeader ? '' : 'display: none;';
    return [
    ':host {',
    '  all: initial;',
    '  --accent: ' + cfg.accent + ';',
    '  --accent-strong: ' + cfg.accent + ';',
    '  --ink: #1e293b;',
    '  --muted: #64748b;',
    '  --line: #e2e8f0;',
    '  --panel: #ffffff;',
    '  --rose: #e11d48;',
    '  --blue: #2563eb;',
    '  --shadow: 0 18px 50px rgba(23, 32, 38, 0.14);',
    '  --radius: 8px;',
    '  --trigger-offset-bottom: ' + cfg.triggerOffsetBottom + ';',
    '  --panel-width: ' + cfg.width + ';',
    '  --panel-height: ' + cfg.height + ';',
    '  --header-bg: ' + headerBg + ';',
    '  --bot-bubble-bg: ' + cfg.botBubbleColor + ';',
    '  --bot-bubble-text: ' + cfg.botBubbleText + ';',
    '  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;',
    '}',
    '',
    '.at-root {',
    '  all: initial;',
    '  display: block;',
    '  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;',
    '  font-size: 14px;',
    '  line-height: 1.45;',
    '  color: #1e293b;',
    '}',
    '',
    '.at-trigger {',
    '  position: fixed;',
    '  bottom: var(--trigger-offset-bottom);',
    '  width: 56px;',
    '  height: 56px;',
    '  border: 0;',
    '  border-radius: 50%;',
    '  background: var(--accent);',
    '  color: white;',
    '  cursor: pointer;',
    '  box-shadow: var(--shadow);',
    '  z-index: 2147483647;',
    '  font-size: 24px;',
    '  display: flex;',
    '  align-items: center;',
    '  justify-content: center;',
    '  transition: opacity 0.2s, transform 0.2s;',
    '  padding: 0;',
    '}',
    '.at-trigger:hover { opacity: 0.9; transform: scale(1.05); }',
    '.at-trigger.at-right { right: var(--trigger-offset-bottom); }',
    '.at-trigger.at-left { left: var(--trigger-offset-bottom); }',
    '.at-trigger svg { width: 28px; height: 28px; fill: currentColor; }',
    '',
    '.at-panel {',
    '  position: fixed;',
    '  bottom: var(--trigger-offset-bottom);',
    '  width: var(--panel-width);',
    '  height: var(--panel-height);',
    '  display: grid;',
    '  grid-template-rows: auto 1fr auto;',
    '  overflow: hidden;',
    '  background: var(--panel);',
    '  border: 1px solid var(--line);',
    '  border-radius: var(--radius);',
    '  box-shadow: var(--shadow);',
    '  z-index: 2147483646;',
    '  transition: opacity 0.2s, transform 0.2s;',
    '}',
    '.at-panel.at-right { right: var(--trigger-offset-bottom); }',
    '.at-panel.at-left { left: var(--trigger-offset-bottom); }',
    '.at-panel.at-hidden {',
    '  opacity: 0;',
    '  transform: translateY(10px) scale(0.96);',
    '  pointer-events: none;',
    '}',
    '',
    '.at-head {',
    '  display: flex;',
    '  justify-content: space-between;',
    '  gap: 12px;',
    '  align-items: center;',
    '  padding: 14px 14px 12px;',
    '  border-bottom: 1px solid var(--line);',
    '  background: var(--header-bg);',
    '  ' + headDisplay,
    '}',
    '.at-head-info strong { display: block; font-size: 15px; font-weight: 600; }',
    '.at-head-info span { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; }',
    '.at-close {',
    '  width: 32px; height: 32px;',
    '  border: 0; border-radius: 50%;',
    '  background: var(--accent); color: white;',
    '  cursor: pointer; font-size: 18px;',
    '  display: flex; align-items: center; justify-content: center;',
    '  flex-shrink: 0; padding: 0;',
    '}',
    '',
    '.at-messages {',
    '  min-height: 0;',
    '  overflow-y: auto;',
    '  overflow-x: hidden;',
    '  padding: 14px;',
    '  display: flex;',
    '  flex-direction: column;',
    '  gap: 2px;',
    '}',
    '',
    '.at-msg {',
    '  min-width: 0;',
    '  max-width: 92%;',
    '  flex: 0 0 auto;',
    '  padding: 10px 12px;',
    '  border-radius: var(--radius);',
    '  font-size: 14px;',
    '  line-height: 1.45;',
    '  white-space: pre-wrap;',
    '  overflow-wrap: anywhere;',
    '}',
    '.at-msg.at-user {',
    '  align-self: flex-end;',
    '  background: var(--accent);',
    '  color: white;',
    '}',
    '.at-msg.at-assistant {',
    '  align-self: flex-start;',
    '  background: var(--bot-bubble-bg);',
    '  color: var(--bot-bubble-text);',
    '  white-space: normal;',
    '  margin-top: -3px;',
    '}',
    '.at-msg.at-assistant.at-thinking { position: relative; min-height: 12px; }',
    '.at-msg.at-assistant.at-thinking::before {',
    '  content: "Thinking and checking data......";',
    '  display: inline-block;',
    '  color: var(--muted);',
    '  font-style: italic;',
    '}',
    '.at-msg.at-error { background: #fff1f3; color: var(--rose); }',
    '.at-msg.at-assistant p { margin: 0 0 5px; }',
    '.at-msg.at-assistant p:last-child, .at-msg.at-assistant ul:last-child, .at-msg.at-assistant ol:last-child { margin-bottom: 0; }',
    '.at-msg.at-assistant ul, .at-msg.at-assistant ol { margin: 0 0 10px; padding-left: 20px; }',
    '.at-msg.at-assistant li { margin: 3px 0; }',
    '.at-msg.at-assistant strong { font-weight: 750; }',
    '.at-msg.at-assistant code {',
    '  padding: 1px 5px;',
    '  border-radius: 5px;',
    '  background: rgba(15, 118, 110, 0.1);',
    '  color: var(--accent);',
    '  font-size: 0.92em;',
    '}',
    '',
    '.at-tool-strip {',
    '  align-self: flex-start;',
    '  max-width: 92%;',
    '  display: flex;',
    '  flex-wrap: wrap;',
    '  gap: 6px;',
    '  margin-bottom: 2px;',
    '}',
    '.at-tool-strip span {',
    '  display: inline-flex;',
    '  align-items: center;',
    '  min-height: 22px;',
    '  padding: 3px 8px;',
    '  border-radius: 999px;',
    '  background: rgba(37, 99, 235, 0.1);',
    '  color: var(--blue);',
    '  font-size: 11px;',
    '  font-weight: 700;',
    '}',
    '',
    '.at-table-wrap {',
    '  max-width: 100%;',
    '  overflow-x: auto;',
    '  margin: 8px 0 12px;',
    '  border: 1px solid var(--line);',
    '  border-radius: var(--radius);',
    '  background: white;',
    '}',
    '.at-table-wrap table { min-width: 520px; width: 100%; border-collapse: collapse; }',
    '.at-table-wrap th, .at-table-wrap td {',
    '  padding: 9px 10px;',
    '  border-bottom: 1px solid var(--line);',
    '  font-size: 12px;',
    '  line-height: 1.35;',
    '}',
    '.at-table-wrap th { background: #f9fbfb; color: var(--muted); font-weight: 600; text-align: left; }',
    '.at-table-wrap tr:last-child td { border-bottom: 0; }',
    '',
    '.at-form {',
    '  display: grid;',
    '  grid-template-columns: 1fr;',
    '  gap: 8px;',
    '  padding: 12px;',
    '  border-top: 1px solid var(--line);',
    '}',
    '.at-form textarea {',
    '  resize: none;',
    '  min-height: 44px;',
    '  max-height: 120px;',
    '  border: 1px solid var(--line);',
    '  border-radius: var(--radius);',
    '  padding: 10px 12px;',
    '  font-family: inherit;',
    '  font-size: 14px;',
    '  outline: none;',
    '  color: var(--ink);',
    '  background: var(--panel);',
    '}',
    '.at-form textarea:focus { border-color: var(--accent); }',
    '.at-form button {',
    '  border: 0;',
    '  border-radius: var(--radius);',
    '  background: var(--accent);',
    '  color: white;',
    '  cursor: pointer;',
    '  font-size: 20px;',
    '  display: flex;',
    '  align-items: center;',
    '  justify-content: center;',
    '  padding: 0;',
    '}',
    '.at-form button:hover { opacity: 0.9; }',
    '.at-form button:disabled { opacity: 0.4; cursor: not-allowed; }',
    '',
    '.at-mic-btn {',
    '  width: 36px; height: 36px;',
    '  border: 0;',
    '  border-radius: 50%;',
    '  background: transparent;',
    '  color: var(--muted);',
    '  cursor: pointer;',
    '  font-size: 18px;',
    '  display: flex;',
    '  align-items: center;',
    '  justify-content: center;',
    '  padding: 0;',
    '  flex-shrink: 0;',
    '  transition: background 0.2s, color 0.2s;',
    '}',
    '.at-mic-btn:hover { background: #f1f5f9; }',
    '.at-mic-btn.at-mic-recording {',
    '  background: var(--rose, #e11d48);',
    '  color: white;',
    '  animation: at-mic-pulse 1s infinite;',
    '}',
    '.at-mic-btn.at-mic-disabled {',
    '  opacity: 0.3;',
    '  cursor: not-allowed;',
    '}',
    '@keyframes at-mic-pulse {',
    '  0% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0.4); }',
    '  70% { box-shadow: 0 0 0 8px rgba(225, 29, 72, 0); }',
    '  100% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0); }',
    '}',
    '.at-mic-timer {',
    '  position: absolute;',
    '  bottom: 56px;',
    '  left: 50%;',
    '  transform: translateX(-50%);',
    '  background: var(--rose, #e11d48);',
    '  color: white;',
    '  font-size: 12px;',
    '  font-weight: 700;',
    '  padding: 3px 8px;',
    '  border-radius: 999px;',
    '  display: none;',
    '  z-index: 10;',
    '}',
    '.at-mic-timer.at-mic-timer-visible { display: block; }',
    '',
    '.at-msg.at-retry-countdown {',
    '  align-self: center;',
    '  background: transparent;',
    '  color: var(--muted);',
    '  font-size: 12px;',
    '  text-align: center;',
    '  max-width: 100%;',
    '}',
    '.at-retry-btn {',
    '  display: inline-block;',
    '  margin-top: 8px;',
    '  padding: 6px 16px;',
    '  border: 1px solid var(--accent);',
    '  border-radius: var(--radius);',
    '  background: var(--panel);',
    '  color: var(--accent);',
    '  cursor: pointer;',
    '  font-size: 13px;',
    '  font-family: inherit;',
    '  outline: none;',
    '}',
    '.at-retry-btn:hover { background: var(--accent); color: white; }',
    '',
    '@media (max-width: 480px) {',
    '  .at-panel {',
    '    width: 100vw; height: 100vh;',
    '    bottom: 0; right: 0 !important; left: 0 !important;',
    '    border-radius: 0; border: 0;',
    '  }',
    '  .at-trigger { bottom: 12px; }',
    '  .at-trigger.at-right { right: 12px; }',
    '  .at-trigger.at-left { left: 12px; }',
    '}'
  ].join('\n');
  }
  var WIDGET_CSS = getWidgetCSS(CONFIG);

  /* ─── Utilities ─── */

  function escapeHtml(val) {
    return String(val)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function getSessionId() {
    try {
      var stored = sessionStorage.getItem(SESSION_KEY);
      if (stored) return stored;
    } catch (e) { /* ignore */ }
    var id = window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : 'sess-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
    try {
      sessionStorage.setItem(SESSION_KEY, id);
    } catch (e) { /* ignore */ }
    return id;
  }

  function loadStoredMessages() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) { return []; }
  }

  function saveStoredMessages(msgs) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(msgs));
    } catch (e) { /* ignore */ }
  }

  function scrollToBottom(el) {
    if (el) el.scrollTop = el.scrollHeight;
  }

  function isScrolledNearBottom(el) {
    return el && (el.scrollHeight - el.scrollTop - el.clientHeight < 48);
  }

  /* ─── Markdown Renderer ─── */

  function renderMarkdown(text) {
    var chunks = [];
    var lines = (text || '').split('\n');
    var i = 0;

    while (i < lines.length) {
      // Table
      if (isTableStart(lines, i)) {
        var tableLines = [];
        while (i < lines.length && lines[i].trim().charAt(0) === '|') {
          tableLines.push(lines[i]);
          i++;
        }
        chunks.push(renderTable(tableLines));
        continue;
      }
      // Unordered list
      if (/^\s*[-*]\s+/.test(lines[i])) {
        var items = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*[-*]\s+/, ''));
          i++;
        }
        chunks.push('<ul>' + items.map(function (item) {
          return '<li>' + inlineMarkdown(item) + '</li>';
        }).join('') + '</ul>');
        continue;
      }
      // Ordered list
      if (/^\s*\d+\.\s+/.test(lines[i])) {
        var oitems = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          oitems.push(lines[i].replace(/^\s*\d+\.\s+/, ''));
          i++;
        }
        chunks.push('<ol>' + oitems.map(function (item) {
          return '<li>' + inlineMarkdown(item) + '</li>';
        }).join('') + '</ol>');
        continue;
      }
      // Paragraph
      var para = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !isTableStart(lines, i) &&
        !/^\s*[-*]\s+/.test(lines[i]) &&
        !/^\s*\d+\.\s+/.test(lines[i])
      ) {
        para.push(lines[i]);
        i++;
      }
      if (para.length) {
        chunks.push('<p>' + inlineMarkdown(para.join('\n')).replace(/\n/g, '<br>') + '</p>');
      }
      // Empty line — skip
      if (i < lines.length && !lines[i].trim()) i++;
    }

    return chunks.join('');
  }

  function isTableStart(lines, idx) {
    var line = lines[idx];
    var next = lines[idx + 1];
    if (!line || !next) return false;
    return line.trim().charAt(0) === '|' && /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?/.test(next);
  }

  function renderTable(lines) {
    var dataRows = [];
    for (var j = 0; j < lines.length; j++) {
      // Skip separator row
      if (/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(lines[j])) continue;
      var cells = lines[j].trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(function (c) { return c.trim(); });
      dataRows.push(cells);
    }
    if (!dataRows.length) return '';
    var head = dataRows[0];
    var body = dataRows.slice(1);
    return '<div class="at-table-wrap"><table><thead><tr>' +
      head.map(function (c) { return '<th>' + inlineMarkdown(c) + '</th>'; }).join('') +
      '</tr></thead><tbody>' +
      body.map(function (row) {
        return '<tr>' + row.map(function (c) { return '<td>' + inlineMarkdown(c) + '</td>'; }).join('') + '</tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function inlineMarkdown(val) {
    return escapeHtml(val)
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  }

  /* ─── DOM Builder ─── */

  function buildWidget(host) {
    var pos = CONFIG.position;
    var posClass = pos === 'left' ? 'at-left' : 'at-right';

    // ── Trigger Button ──
    var trigger = document.createElement('button');
    trigger.className = 'at-trigger ' + posClass;
    trigger.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/><path d="M7 9h10v2H7zm0-3h10v2H7z"/></svg>';
    host.appendChild(trigger);

    // ── Panel ──
    var panel = document.createElement('div');
    panel.className = 'at-panel ' + posClass + ' at-hidden';

    // Header
    var head = document.createElement('div');
    head.className = 'at-head';
    head.innerHTML = '<div class="at-head-info"><strong>' + escapeHtml(CONFIG.title) + '</strong><span>' + escapeHtml(CONFIG.agent) + '</span></div>';
    var closeBtn = document.createElement('button');
    closeBtn.className = 'at-close';
    closeBtn.textContent = '\u2212'; // minus sign
    head.appendChild(closeBtn);
    panel.appendChild(head);

    // Messages area
    var messages = document.createElement('div');
    messages.className = 'at-messages';
    panel.appendChild(messages);

    // Form
    var form = document.createElement('form');
    form.className = 'at-form';

    // Mic timer (positioned above form, absolute)
    var micTimer = document.createElement('div');
    micTimer.className = 'at-mic-timer';
    form.appendChild(micTimer);

    var formRow = document.createElement('div');
    formRow.style.cssText = 'display:flex;gap:6px;align-items:center;';

    var textarea = document.createElement('textarea');
    textarea.rows = 2;
    textarea.placeholder = CONFIG.placeholder;
    formRow.appendChild(textarea);

    var micBtn = document.createElement('button');
    micBtn.type = 'button';
    micBtn.className = 'at-mic-btn';
    micBtn.title = CONFIG.lang === 'ru' ? 'Голосовое сообщение' : 'Voice message';
    // TODO: Бля чо это нахуй за пиздец это надо вычистить это ужасно
    micBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5zm6.91 4c-.49 0-.9.36-.98.85C16.52 12.2 14.47 14 12 14s-4.52-1.8-4.93-4.15c-.08-.49-.49-.85-.98-.85-.61 0-1.09.54-1 1.14.49 3 2.89 5.35 5.91 5.78V20c0 .55.45 1 1 1s1-.45 1-1v-2.08c3.02-.43 5.42-2.78 5.91-5.78.09-.6-.39-1.14-1-1.14z"/></svg>';
    micBtn.style.display = CONFIG.voiceInput && navigator.mediaDevices && navigator.mediaDevices.getUserMedia ? '' : 'none';
    formRow.appendChild(micBtn);

    var sendBtn = document.createElement('button');
    sendBtn.type = 'submit';
    sendBtn.textContent = '\u2197'; // ↗
    formRow.appendChild(sendBtn);

    form.appendChild(formRow);
    panel.appendChild(form);

    host.appendChild(panel);

    return { trigger: trigger, panel: panel, messages: messages, form: form, textarea: textarea, closeBtn: closeBtn, sendBtn: sendBtn, head: head, micBtn: micBtn, micTimer: micTimer };
  }

  /* ─── Chat Logic ─── */

  function runChat(ui) {
    var messagesEl = ui.messages;
    var headEl = ui.head;
    var sessionId = getSessionId();

    // ── Pending retry state ──
    var pendingMessage = null;
    var retryAttempts = {};
    var MAX_RETRIES = 3;

    // ── Set global bridge so app.js can switch agent ──
    window.__agentTutorSetAgent = function __agentTutorSetAgent(name) {
      if (!name) return;
      CONFIG.agent = name;
      STORAGE_KEY = 'at_messages_' + CONFIG.agent;
      SESSION_KEY = 'at_session_' + CONFIG.agent;
      state.sessionId = null;
      state.messages = [];
      sessionId = getSessionId();
      state.sessionId = sessionId;
      // Update header to show new agent name (only .at-head-info, keep closeBtn)
      var infoEl = headEl.querySelector('.at-head-info');
      if (infoEl) {
        infoEl.innerHTML = '<strong>' + escapeHtml(CONFIG.title) + '</strong><span>' + escapeHtml(CONFIG.agent) + '</span>';
      }
      // Clear messages and reload history
      messagesEl.innerHTML = '';
      restoreHistory();
    };

    // Sync with already-selected agent on page load (too early for app.js sync)
    try {
      var storedAgent = window.localStorage.getItem('agentTutorAgentId');
      if (storedAgent && CONFIG.agent !== storedAgent) {
        window.__agentTutorSetAgent(storedAgent);
      }
    } catch(e) {}

    // ── Session storage helpers ──
    function readStored() {
      var stored = loadStoredMessages();
      // Filter for this session only (compat with potential multi-agent)
      return stored.filter(function (m) {
        return m.sessionId === sessionId;
      }).map(function (m) {
        return { kind: m.kind, text: m.text, tools: m.tools || [] };
      });
    }

    function appendStored(kind, text, tools) {
      var stored = loadStoredMessages();
      stored.push({
        sessionId: sessionId,
        kind: kind,
        text: String(text || ''),
        tools: tools || [],
        ts: Date.now()
      });
      // Keep max 100 messages per session
      var filtered = stored.filter(function (m) { return m.sessionId === sessionId; });
      if (filtered.length > 100) {
        var extra = filtered.length - 100;
        var removed = 0;
        stored = stored.filter(function (m) {
          if (m.sessionId === sessionId && removed < extra) {
            removed++;
            return false;
          }
          return true;
        });
      }
      saveStoredMessages(stored);
    }

    // ── Message rendering ──
    function addMessage(kind, text, opts) {
      opts = opts || {};
      var node = document.createElement('div');
      node.className = 'at-msg ' + (kind === 'user' ? 'at-user' : 'at-assistant');

      if (kind === 'assistant') {
        node.dataset.raw = text || '';
        node.innerHTML = renderMarkdown(text || '');
      } else {
        node.textContent = text || '';
      }

      if (opts.before) {
        messagesEl.insertBefore(node, opts.before);
      } else {
        messagesEl.appendChild(node);
      }

      if (opts.persist) {
        appendStored(kind, text, opts.tools);
      }

      if (opts.scroll !== false) {
        scrollToBottom(messagesEl);
      }

      return node;
    }

    function restoreHistory() {
      var stored = readStored();
      if (!stored.length) {
        // Show greeting
        addMessage('assistant', CONFIG.greeting, { persist: false, scroll: false });
        return;
      }

      messagesEl.innerHTML = '';
      var pendingToolNames = [];

      stored.forEach(function (msg) {
        if (msg.kind === 'user') {
          addMessage('user', msg.text, { persist: false, scroll: false });
        } else if (msg.kind === 'assistant') {
          var tools = (msg.tools || []).filter(Boolean);
          var text = String(msg.text || '');

          if (!text.trim() && tools.length > 0) {
            pendingToolNames = pendingToolNames.concat(tools);
            return;
          }

          var mergedTools = pendingToolNames.concat(tools);
          pendingToolNames = [];

          if (mergedTools.length > 0) {
            messagesEl.appendChild(makeToolStrip(mergedTools));
          }

          var node = document.createElement('div');
          node.className = 'at-msg at-assistant';
          node.dataset.raw = text;
          node.innerHTML = renderMarkdown(text);
          messagesEl.appendChild(node);
        }
      });

      if (pendingToolNames.length > 0) {
        messagesEl.appendChild(makeToolStrip(pendingToolNames));
      }

      scrollToBottom(messagesEl);
    }

    function makeToolStrip(toolNames, displayNames) {
      displayNames = displayNames || {};
      var unique = [];
      toolNames.forEach(function (n) {
        if (unique.indexOf(n) === -1) unique.push(n);
      });
      if (!unique.length) return null;
      var el = document.createElement('div');
      el.className = 'at-tool-strip';
      el.innerHTML = unique.map(function (name) {
        var display = displayNames[name] || name;
        var icon = getToolIcon(display);
        return '<span>' + icon + ' ' + escapeHtml(display) + '</span>';
      }).join('');
      return el;
    }

    function getToolIcon(displayText) {
      var lower = displayText.toLowerCase();
      if (lower.indexOf('поиск') !== -1 || lower.indexOf('найти') !== -1) return '\uD83D\uDD0D';
      if (lower.indexOf('чтение') !== -1 || lower.indexOf('получение') !== -1 || lower.indexOf('данных') !== -1) return '\uD83D\uDCCB';
      if (lower.indexOf('запрос') !== -1) return '\uD83D\uDCCA';
      return '\u26A1';
    }

    function appendToken(target, text) {
      var raw = target.dataset.raw || '';
      target.dataset.raw = raw + text;
      target.innerHTML = renderMarkdown(raw + text);
    }

    function setFinalText(target, text) {
      target.classList.remove('at-thinking');
      target.dataset.raw = text;
      target.innerHTML = renderMarkdown(text);
    }

    // ── Retry / Rate Limit Handling ──

    function scheduleRetry(message, delayMs) {
      var remaining = Math.ceil(delayMs / 1000);

      // Show countdown message
      var countdownMsg = document.createElement('div');
      countdownMsg.className = 'at-msg at-retry-countdown';
      countdownMsg.textContent = '\u23F3 Retry in ' + remaining + 's...';
      messagesEl.appendChild(countdownMsg);
      scrollToBottom(messagesEl);

      var interval = setInterval(function() {
        remaining--;
        if (remaining <= 0) {
          clearInterval(interval);
          countdownMsg.remove();
          retryChat(message);
        } else {
          countdownMsg.textContent = '\u23F3 Retry in ' + remaining + 's...';
        }
      }, 1000);
    }

    function retryChat(message) {
      // Use the same function that streamChat would create internally
      var answerNode = addMessage('assistant', '', { persist: false, scroll: false });
      streamChat(message, answerNode);
    }

    // ── SSE Streaming (shared parser — used by both text and voice chat) ──

    function _pumpSSE(response, targetNode) {
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';

      function pump() {
        return reader.read().then(function (result) {
          if (result.done) return;

          buffer += decoder.decode(result.value, { stream: true });
          var parts = buffer.split('\n\n');
          buffer = parts.pop();

          parts.forEach(function (chunk) {
            var line = chunk.split('\n').find(function (l) { return l.indexOf('data:') === 0; });
            if (!line) return;

            try {
              var payload = JSON.parse(line.slice(5).trim());
            } catch (e) { return; }

            if (targetNode.classList.contains('at-thinking')) {
              targetNode.classList.remove('at-thinking');
            }

            if (payload.type === 'token') {
              appendToken(targetNode, payload.text || '');
            } else if (payload.type === 'final') {
              setFinalText(targetNode, payload.text || '');
            } else if (payload.type === 'tool_call') {
              var tools = JSON.parse(targetNode.dataset.tools || '[]');
              var displayNames = JSON.parse(targetNode.dataset.displayNames || '{}');
              if (tools.indexOf(payload.name) === -1) {
                tools.push(payload.name);
                targetNode.dataset.tools = JSON.stringify(tools);
              }
              if (payload.display_name && !displayNames[payload.name]) {
                displayNames[payload.name] = payload.display_name;
                targetNode.dataset.displayNames = JSON.stringify(displayNames);
              }
              ensureToolStrip(targetNode, tools, displayNames);
            } else if (payload.type === 'audio') {
              if (CONFIG.voiceOutput && payload.data) {
                playAudioBase64(payload.data);
              }
            } else if (payload.type === 'done') {
              if (targetNode.classList.contains('at-error')) return;
              var raw = targetNode.dataset.raw || '';
              if (!raw.trim()) {
                setFinalText(targetNode, CONFIG.lang === 'ru'
                  ? 'Не удалось получить ответ.' : 'No response.');
              }
              var toolNames = [];
              try { toolNames = JSON.parse(targetNode.dataset.tools || '[]'); } catch (e) { /* ignore */ }
              appendStored('assistant', targetNode.dataset.raw || '', toolNames);
              targetNode.dataset.saved = 'true';
              scrollToBottom(messagesEl);
            } else if (payload.type === 'error') {
              targetNode.classList.remove('at-thinking');
              targetNode.classList.add('at-error');
              targetNode.textContent = payload.text || (CONFIG.lang === 'ru'
                ? 'Произошла ошибка.' : 'An error occurred.');
            }
          });

          return pump();
        });
      }

      return pump();
    }

    function streamChat(message, targetNode) {
      targetNode.classList.add('at-thinking');
      targetNode.dataset.tools = '[]';
      targetNode.dataset.displayNames = '{}';
      targetNode.dataset.saved = 'false';

      var url = CONFIG.apiBase + '/api/chat/' + encodeURIComponent(CONFIG.agent);

      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: message, session_id: sessionId })
      }).then(function (response) {
        if (response.status === 429) {
          targetNode.classList.remove('at-thinking');
          targetNode.remove();

          var retryAfter = response.headers.get('Retry-After');
          var delay = 5;
          if (retryAfter) {
            var parsed = parseInt(retryAfter, 10);
            if (!isNaN(parsed) && parsed > 0) delay = parsed;
          }

          retryAttempts[message] = (retryAttempts[message] || 0) + 1;
          var attempts = retryAttempts[message];

          if (attempts >= MAX_RETRIES) {
            var failMsg = document.createElement('div');
            failMsg.className = 'at-msg at-assistant';
            failMsg.textContent = '\u26A0\uFE0F Server overloaded. Try again later.';
            messagesEl.appendChild(failMsg);
            scrollToBottom(messagesEl);

            var retryBtn = document.createElement('button');
            retryBtn.className = 'at-retry-btn';
            retryBtn.textContent = 'Retry';
            failMsg.appendChild(retryBtn);
            retryBtn.addEventListener('click', function() {
              delete retryAttempts[message];
              failMsg.remove();
              retryChat(message);
            });
            return;
          }

          var rateMsg = document.createElement('div');
          rateMsg.className = 'at-msg at-assistant';
          rateMsg.textContent = '\u26A0\uFE0F Server temporarily overloaded. Retry in ' + delay + 's.';
          messagesEl.appendChild(rateMsg);
          scrollToBottom(messagesEl);

          pendingMessage = message;
          scheduleRetry(message, delay * 1000);
          return;
        }

        if (!response.ok) {
          targetNode.classList.remove('at-thinking');
          targetNode.classList.add('at-error');
          targetNode.textContent = 'Error: ' + response.status;
          return;
        }

        return _pumpSSE(response, targetNode);
      }).catch(function (err) {
        targetNode.classList.remove('at-thinking');
        targetNode.classList.add('at-error');
        // TODO: РЕФАКТОРИНГ ЭТООГО
        targetNode.innerHTML = '\u26A0\uFE0F No connection to server. Check your internet.<br><button class="at-retry-btn" data-message="' + escapeHtml(message) + '">Retry</button>';
        var btn = targetNode.querySelector('.at-retry-btn');
        if (btn) {
          btn.addEventListener('click', function() {
            var msg = btn.getAttribute('data-message');
            targetNode.classList.remove('at-error');
            targetNode.innerHTML = '';
            streamChat(msg, targetNode);
          });
        }
      });
    }

    function ensureToolStrip(targetNode, toolNames, displayNames) {
      displayNames = displayNames || {};
      var unique = [];
      toolNames.forEach(function (n) {
        if (unique.indexOf(n) === -1) unique.push(n);
      });
      if (!unique.length) return;
      var prev = targetNode.previousElementSibling;
      if (prev && prev.className === 'at-tool-strip') {
        prev.innerHTML = unique.map(function (name) {
          var display = displayNames[name] || name;
          var icon = getToolIcon(display);
          return '<span>' + icon + ' ' + escapeHtml(display) + '</span>';
        }).join('');
        return;
      }
      var strip = makeToolStrip(unique, displayNames);
      if (strip) messagesEl.insertBefore(strip, targetNode);
    }

    // ── Event Bindings ──

    // Toggle panel
    ui.trigger.addEventListener('click', function () {
      state.open = true;
      ui.panel.classList.remove('at-hidden');
      ui.trigger.style.display = 'none';
      ui.textarea.focus();
      scrollToBottom(messagesEl);
    });

    ui.closeBtn.addEventListener('click', function () {
      state.open = false;
      ui.panel.classList.add('at-hidden');
      ui.trigger.style.display = 'flex';
    });

    // ── Submit logic ──
    function handleSubmit() {
      var text = ui.textarea.value.trim();
      if (!text) return;
      ui.textarea.value = '';
      addMessage('user', text, { persist: true });
      var answerNode = addMessage('assistant', '', { persist: false, scroll: false });
      streamChat(text, answerNode);
    }

    // Textarea: Enter to send, Shift+Enter for newline
    ui.textarea.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    });

    // Submit (mouse click or form submit)
    ui.form.addEventListener('submit', function (e) {
      e.preventDefault();
      handleSubmit();
    });

    // ── Voice Input ──
    var mediaRecorder = null;
    var micChunks = [];
    var micStream = null;
    var micTimerInterval = null;
    var micStartTime = 0;
    var micDuration = 0;
    var MAX_RECORDING_SEC = 120;

    function updateMicTimer() {
      micDuration = Math.floor((Date.now() - micStartTime) / 1000);
      var m = Math.floor(micDuration / 60);
      var s = micDuration % 60;
      ui.micTimer.textContent = (m > 0 ? m + 'm ' : '') + s + 's';
    }

    function startVoiceRecording() {
      if (mediaRecorder && mediaRecorder.state === 'recording') return;

      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        ui.micBtn.classList.add('at-mic-disabled');
        return;
      }

      navigator.mediaDevices.getUserMedia({ audio: true })
        .then(function (stream) {
          micStream = stream;
          micChunks = [];

          var mimeType = 'audio/webm;codecs=opus';
          if (!MediaRecorder.isTypeSupported(mimeType)) {
            mimeType = 'audio/webm';
          }

          mediaRecorder = new MediaRecorder(stream, { mimeType: mimeType });

          mediaRecorder.ondataavailable = function (e) {
            if (e.data.size > 0) micChunks.push(e.data);
          };

          mediaRecorder.onstop = function () {
            stream.getTracks().forEach(function (t) { t.stop(); });
            micStream = null;
            ui.micBtn.classList.remove('at-mic-recording');
            ui.micTimer.classList.remove('at-mic-timer-visible');
            if (micTimerInterval) { clearInterval(micTimerInterval); micTimerInterval = null; }

            var blob = new Blob(micChunks, { type: mimeType });
            if (blob.size === 0) return;

            // Show user message with mic icon + duration
            var durStr = ui.micTimer.textContent || (micDuration + 's');
            addMessage('user', '🎤 ' + durStr, { persist: true });

            var answerNode = addMessage('assistant', '', { persist: false, scroll: false });
            streamVoiceChat(blob, answerNode);
          };

          mediaRecorder.start();
          ui.micBtn.classList.add('at-mic-recording');
          ui.micTimer.classList.add('at-mic-timer-visible');
          micStartTime = Date.now();
          micDuration = 0;
          updateMicTimer();
          micTimerInterval = setInterval(updateMicTimer, 1000);

          // Auto-stop at max duration
          if (MAX_RECORDING_SEC > 0) {
            setTimeout(function () {
              if (mediaRecorder && mediaRecorder.state === 'recording') {
                mediaRecorder.stop();
              }
            }, MAX_RECORDING_SEC * 1000);
          }
        })
        .catch(function (err) {
          if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
            addMessage('assistant', (CONFIG.lang === 'ru'
              ? '❌ Разрешите доступ к микрофону в настройках браузера'
              : '❌ Please allow microphone access in browser settings'),
              { persist: false });
          } else {
            ui.micBtn.classList.add('at-mic-disabled');
          }
        });
    }

    function stopVoiceRecording() {
      if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
      }
    }

    if (CONFIG.voiceInput && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      ui.micBtn.addEventListener('mousedown', function (e) {
        e.preventDefault();
        if (ui.micBtn.classList.contains('at-mic-recording')) {
          stopVoiceRecording();
        } else {
          startVoiceRecording();
        }
      });
    }

    function streamVoiceChat(blob, targetNode) {
      targetNode.classList.add('at-thinking');
      targetNode.dataset.tools = '[]';
      targetNode.dataset.displayNames = '{}';
      targetNode.dataset.saved = 'false';

      var url = CONFIG.apiBase + '/api/chat/voice';
      var formData = new FormData();
      formData.append('audio', blob, 'voice.webm');
      formData.append('session_id', sessionId);
      formData.append('agent', CONFIG.agent);
      formData.append('lang', CONFIG.lang);

      fetch(url, {
        method: 'POST',
        body: formData
      }).then(function (response) {
        if (response.status === 429) {
          targetNode.classList.remove('at-thinking');
          targetNode.remove();
          var msg = document.createElement('div');
          msg.className = 'at-msg at-assistant';
          msg.textContent = CONFIG.lang === 'ru'
            ? '⚠️ Сервер перегружен. Попробуйте позже.'
            : '⚠️ Server overloaded. Try again later.';
          messagesEl.appendChild(msg);
          scrollToBottom(messagesEl);
          return;
        }

        if (!response.ok) {
          targetNode.classList.remove('at-thinking');
          targetNode.classList.add('at-error');
          targetNode.textContent = 'Error: ' + response.status;
          return;
        }

        return _pumpSSE(response, targetNode);
      }).catch(function () {
        targetNode.classList.remove('at-thinking');
        targetNode.classList.add('at-error');
        targetNode.innerHTML = '\u26A0\uFE0F Connection error.';
      });
    }

    // ── Audio playback for TTS ──
    function playAudioBase64(b64data) {
      try {
        var binaryStr = atob(b64data);
        var byteArray = new Uint8Array(binaryStr.length);
        for (var i = 0; i < binaryStr.length; i++) {
          byteArray[i] = binaryStr.charCodeAt(i);
        }
        var blob = new Blob([byteArray], { type: 'audio/mpeg' });
        var url = URL.createObjectURL(blob);
        var audio = new Audio(url);
        audio.play().catch(function () {
          // Autoplay blocked — user needs to interact first
        });
      } catch (e) {
        // Invalid base64
      }
    }

    // ── Init ──
    restoreHistory();
  }

  function init() {
    state.sessionId = getSessionId();
    buildUI();
  }

  function buildUI() {
    // Create host element
    var host = document.createElement('div');
    host.id = 'helperium-widget-' + CONFIG.agent.replace(/[^a-zA-Z0-9_-]/g, '');

    // Shadow DOM
    var shadow = host.attachShadow({ mode: 'open' });

    // Inject styles
    var style = document.createElement('style');
    style.textContent = WIDGET_CSS;
    shadow.appendChild(style);

    // Create widget UI inside shadow
    var root = document.createElement('div');
    root.className = 'at-root';
    shadow.appendChild(root);

    var ui = buildWidget(root);
    runChat(ui);

    document.body.appendChild(host);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

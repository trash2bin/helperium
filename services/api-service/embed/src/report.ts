/**
 * Helperium Embed Widget — Problem Reports
 *
 * Per-message "report a problem" flag: opens an inline mini-form and POSTs
 * the offending answer plus diagnostic context to the public /api/reports
 * endpoint. Report content is only stored server-side for operator review;
 * it never reaches the LLM.
 *
 * Field caps mirror the server DTO (ReportCreateRequest, extra=forbid) so a
 * legitimate client never gets a 422 on oversized input.
 */

import { ICONS } from './icons';
import type { WidgetConfig } from './types';

const MAX_COMMENT = 1000;
const MAX_TEXT = 4000;
const MAX_ERROR_TEXT = 500;
const MAX_PAGE_URL = 2048;
const MAX_TRANSCRIPT_ITEMS = 20;
const MAX_TRANSCRIPT_TEXT = 2000;
const MAX_LISTED_TOOLS = 20;
const MAX_STORED_REPORTED = 50;

/** Diagnostic context collected from a message bubble. */
export interface ReportMessageContext {
  kind: 'assistant' | 'error';
  text: string;
  tools: string[];
  displayNames: string[];
  correlationId: string;
}

/** Mutable dependencies (session id / transcript rebind on agent switch). */
export interface ReportDeps {
  readonly config: WidgetConfig;
  readonly getSessionId: () => string;
  readonly getTranscript: () => Array<{
    kind: string;
    text: string;
    tools: string[];
    ts?: number;
  }>;
}

/* ─── Reported-state persistence (sessionStorage) ─── */

function reportedStoreKey(agent: string): string {
  return 'at_reported_' + agent;
}

/** Stable per-bubble key derived from the message content. */
export function reportedKey(kind: string, text: string): string {
  const src = kind + ':' + String(text || '').slice(0, 200);
  let hash = 5381;
  for (let i = 0; i < src.length; i++) {
    hash = ((hash << 5) + hash + src.charCodeAt(i)) | 0;
  }
  return (hash >>> 0).toString(36);
}

export function isReported(config: WidgetConfig, key: string): boolean {
  try {
    const raw = sessionStorage.getItem(reportedStoreKey(config.agent));
    const list: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) && list.includes(key);
  } catch {
    return false;
  }
}

export function markReported(config: WidgetConfig, key: string): void {
  try {
    const raw = sessionStorage.getItem(reportedStoreKey(config.agent));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    const list = Array.isArray(parsed) ? parsed.filter((k): k is string => typeof k === 'string') : [];
    if (!list.includes(key)) list.push(key);
    while (list.length > MAX_STORED_REPORTED) list.shift();
    sessionStorage.setItem(reportedStoreKey(config.agent), JSON.stringify(list));
  } catch {
    /* quota exceeded or private browsing — state stays in the DOM only */
  }
}

/* ─── Context & payload ─── */

/**
 * Collects the reported-message context from the bubble node.
 * Error bubbles carry their text in textContent; answers in dataset.raw.
 */
export function collectReportContext(node: HTMLElement): ReportMessageContext {
  const kind: 'assistant' | 'error' = node.classList.contains('at-error')
    ? 'error'
    : 'assistant';
  const text =
    kind === 'error' ? node.textContent || '' : node.dataset.raw || '';

  let tools: string[] = [];
  try {
    const parsed: unknown = JSON.parse(node.dataset.tools || '[]');
    if (Array.isArray(parsed)) {
      tools = parsed.filter((t): t is string => typeof t === 'string');
    }
  } catch {
    tools = [];
  }

  let displayNames: string[] = [];
  try {
    const parsed: unknown = JSON.parse(node.dataset.displayNames || '{}');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      displayNames = Object.values(parsed as Record<string, unknown>).map(String);
    }
  } catch {
    displayNames = [];
  }

  return {
    kind,
    text: String(text || '').slice(0, MAX_TEXT),
    tools,
    displayNames,
    correlationId: node.dataset.correlationId || '',
  };
}

export function buildReportPayload(
  deps: ReportDeps,
  ctx: ReportMessageContext,
  comment: string,
): Record<string, unknown> {
  const transcript = deps
    .getTranscript()
    .slice(-MAX_TRANSCRIPT_ITEMS)
    .map((m) => ({
      kind: m.kind,
      text: String(m.text || '').slice(0, MAX_TRANSCRIPT_TEXT),
      tools: (m.tools || []).slice(0, MAX_LISTED_TOOLS),
      ts: typeof m.ts === 'number' ? new Date(m.ts).toISOString() : null,
    }));

  const payload: Record<string, unknown> = {
    agent: deps.config.agent,
    session_id: deps.getSessionId(),
    lang: deps.config.lang,
    message: {
      kind: ctx.kind,
      text: ctx.text,
      tools: ctx.tools.slice(0, MAX_LISTED_TOOLS),
      display_names: ctx.displayNames.slice(0, MAX_LISTED_TOOLS),
    },
    transcript,
  };

  const trimmedComment = comment.trim().slice(0, MAX_COMMENT);
  if (trimmedComment) payload.comment = trimmedComment;

  if (ctx.kind === 'error' || ctx.correlationId) {
    payload.last_error = {
      text: ctx.kind === 'error' ? ctx.text.slice(0, MAX_ERROR_TEXT) : '',
      correlation_id: ctx.correlationId || null,
    };
  }

  try {
    const pageUrl = window.location.href.slice(0, MAX_PAGE_URL);
    if (pageUrl) payload.page_url = pageUrl;
  } catch {
    /* no window context — omit */
  }

  return payload;
}

/** POSTs the report; resolves on 201, rejects otherwise. */
export async function submitReport(
  deps: ReportDeps,
  ctx: ReportMessageContext,
  comment: string,
): Promise<void> {
  const payload = buildReportPayload(deps, ctx, comment);
  const response = await fetch(deps.config.apiBase + '/api/reports', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error('report_failed_' + response.status);
  }
}

/* ─── UI ─── */

/**
 * Attaches the report flag to an assistant message row. The bubble and the
 * flag share a `.at-bubble-line` wrapper so the flag survives error-path
 * `textContent` overwrites of the bubble itself.
 */
export function attachReportButton(
  deps: ReportDeps,
  node: HTMLDivElement,
  row: HTMLDivElement,
): void {
  const { config } = deps;
  if (row.querySelector('.at-report-btn')) return;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'at-report-btn';
  btn.innerHTML = ICONS.flag;
  const label =
    config.lang === 'ru'
      ? 'Пожаловаться на этот ответ'
      : 'Report this answer';
  btn.setAttribute('aria-label', label);
  btn.title = label;

  const key = reportedKey('assistant', node.dataset.raw || '');
  if (isReported(config, key)) {
    btn.classList.add('at-reported');
    btn.disabled = true;
  }

  btn.addEventListener('click', () => {
    if (!btn.classList.contains('at-reported')) {
      openReportForm(deps, node, row, btn);
    }
  });

  const line = document.createElement('div');
  line.className = 'at-bubble-line';
  const parent = node.parentNode;
  if (parent) {
    parent.insertBefore(line, node);
    line.appendChild(node);
    line.appendChild(btn);
  } else {
    // No parent yet (row not mounted): keep structure valid anyway.
    row.insertBefore(line, row.firstChild);
    line.appendChild(node);
    line.appendChild(btn);
  }
}

function openReportForm(
  deps: ReportDeps,
  node: HTMLDivElement,
  row: HTMLDivElement,
  btn: HTMLButtonElement,
): void {
  const ru = deps.config.lang === 'ru';
  const messagesEl = row.closest('.at-messages');
  const existing = messagesEl?.querySelector('.at-report-form');
  if (existing) existing.remove();

  const form = document.createElement('div');
  form.className = 'at-report-form';

  const input = document.createElement('textarea');
  input.className = 'at-report-input';
  input.rows = 2;
  input.maxLength = MAX_COMMENT;
  input.placeholder = ru ? 'Что не так? (необязательно)' : "What's wrong? (optional)";
  input.setAttribute('aria-label', input.placeholder);

  const actions = document.createElement('div');
  actions.className = 'at-report-actions';

  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'at-report-cancel';
  cancel.textContent = ru ? 'Отмена' : 'Cancel';

  const submit = document.createElement('button');
  submit.type = 'button';
  submit.className = 'at-report-submit';
  submit.textContent = ru ? 'Отправить' : 'Send';

  actions.appendChild(cancel);
  actions.appendChild(submit);
  form.appendChild(input);
  form.appendChild(actions);
  row.parentNode?.insertBefore(form, row.nextSibling);
  input.focus();

  cancel.addEventListener('click', () => form.remove());

  submit.addEventListener('click', () => {
    const comment = input.value.slice(0, MAX_COMMENT);
    const ctx = collectReportContext(node);
    submit.disabled = true;
    submit.textContent = ru ? 'Отправка…' : 'Sending…';
    form.querySelector('.at-report-error')?.remove();

    submitReport(deps, ctx, comment)
      .then(() => {
        form.remove();
        btn.classList.add('at-reported');
        btn.disabled = true;
        btn.setAttribute(
          'aria-label',
          ru ? 'Жалоба отправлена' : 'Report sent',
        );
        markReported(deps.config, reportedKey('assistant', node.dataset.raw || ''));

        const done = document.createElement('div');
        done.className = 'at-report-done';
        done.textContent = ru
          ? 'Спасибо, отчёт отправлен.'
          : 'Thanks, your report was sent.';
        row.parentNode?.insertBefore(done, row.nextSibling);
        window.setTimeout(() => done.remove(), 4000);
      })
      .catch(() => {
        submit.disabled = false;
        submit.textContent = ru ? 'Отправить' : 'Send';
        const error = document.createElement('div');
        error.className = 'at-report-error';
        error.textContent = ru
          ? 'Не удалось отправить. Попробуйте ещё раз.'
          : 'Failed to send. Please try again.';
        form.appendChild(error);
      });
  });
}

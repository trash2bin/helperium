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

  // The reported-state key must track the message's final content: during the
  // live path the row is attached in the thinking state with an empty raw text,
  // so a key computed once here would be shared by every live answer. Refresh
  // whenever the bubble's content-bearing attributes change (streaming chunks,
  // final text, error class) — mutation callbacks run after the batch settles.
  const refreshState = (): void => {
    const ctx = collectReportContext(node);
    if (isReported(config, reportedKey(ctx.kind, ctx.text))) {
      btn.classList.add('at-reported');
      btn.disabled = true;
    } else {
      btn.classList.remove('at-reported');
      btn.disabled = false;
    }
  };
  refreshState();
  new MutationObserver(refreshState).observe(node, { attributes: true });

  btn.addEventListener('click', () => {
    if (!btn.classList.contains('at-reported')) {
      openReportForm(deps, node, row, btn);
    }
  });

  // Place the flag inside the row, next to the bot avatar. messages.ts builds
  // each row as [bubble, avatar]; we keep that order and only insert the flag
  // before the bubble so it sits at the very left of the row — visually a
  // small icon next to the AI badge.
  if (row.firstChild) {
    row.insertBefore(btn, row.firstChild);
  } else {
    row.appendChild(btn);
  }
}

function openReportForm(
  deps: ReportDeps,
  node: HTMLDivElement,
  row: HTMLDivElement,
  btn: HTMLButtonElement,
): void {
  const ru = deps.config.lang === 'ru';
  const panelEl = row.closest('.at-panel');
  const messagesEl = row.closest('.at-messages');
  // Remove any leftover modal anywhere inside the panel/messages — there can
  // only be one open at a time. Fall back to the closest at-root so the lookup
  // still works when the row is mounted without an at-panel wrapper (e.g.
  // lightweight unit-test mounts).
  const existing =
    panelEl?.querySelector('.at-report-overlay') ||
    messagesEl?.querySelector('.at-report-overlay') ||
    row.closest('.at-root')?.querySelector('.at-report-overlay');
  if (existing) existing.remove();

  const ctx = collectReportContext(node);

  // ── Backdrop overlay (fills the widget panel, blurs the chat underneath)
  const overlay = document.createElement('div');
  overlay.className = 'at-report-overlay';
  overlay.setAttribute('role', 'presentation');

  // ── Centred modal card
  const modal = document.createElement('div');
  modal.className = 'at-report-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute(
    'aria-labelledby',
    'at-report-modal-title',
  );

  const head = document.createElement('div');
  head.className = 'at-report-modal-head';

  const icon = document.createElement('div');
  icon.className = 'at-report-modal-icon';
  icon.innerHTML = ICONS.flag;

  const titleWrap = document.createElement('div');
  const title = document.createElement('h3');
  title.className = 'at-report-modal-title';
  title.id = 'at-report-modal-title';
  title.textContent = ru ? 'Пожаловаться на этот ответ' : 'Report this answer';

  const hint = document.createElement('p');
  hint.className = 'at-report-modal-hint';
  hint.textContent = ru
    ? 'Опишите проблему — мы передадим отзыв оператору. Ответ модели и контекст диалога уйдут вместе с жалобой.'
    : 'Tell us what went wrong — we pass the report to the operator along with the answer and surrounding chat.';

  titleWrap.appendChild(title);
  titleWrap.appendChild(hint);
  head.appendChild(icon);
  head.appendChild(titleWrap);

  // Quoted snippet of the answer so the user always knows which message they
  // are flagging, even after scrolling back.
  const quoteLabel = document.createElement('p');
  quoteLabel.className = 'at-report-modal-quote-label';
  quoteLabel.textContent = ru ? 'Ответ ассистента' : 'Assistant answer';

  const quote = document.createElement('blockquote');
  quote.className = 'at-report-modal-quote';
  const snippet = String(ctx.text || '').trim();
  quote.textContent =
    snippet.length > 280 ? snippet.slice(0, 277) + '…' : snippet;

  // ── Form (legacy class names preserved for test/contract surface)
  const form = document.createElement('div');
  form.className = 'at-report-form';

  const input = document.createElement('textarea');
  input.className = 'at-report-input';
  input.rows = 3;
  input.maxLength = MAX_COMMENT;
  input.placeholder = ru ? 'Что не так с этим ответом? (необязательно)' : "What's wrong with this answer? (optional)";
  input.setAttribute('aria-label', input.placeholder);

  const counter = document.createElement('div');
  counter.className = 'at-report-counter';
  const updateCounter = (): void => {
    const left = MAX_COMMENT - input.value.length;
    counter.textContent = ru
      ? `Осталось ${left} символов`
      : `${left} characters left`;
    counter.classList.toggle(
      'at-report-counter-warn',
      left < MAX_COMMENT * 0.1,
    );
  };
  input.addEventListener('input', updateCounter);
  updateCounter();

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
  form.appendChild(counter);
  form.appendChild(actions);

  modal.appendChild(head);
  modal.appendChild(quoteLabel);
  modal.appendChild(quote);
  modal.appendChild(form);
  overlay.appendChild(modal);

  // Mount the modal inside the widget panel so the absolute overlay fills it.
  // Fallback: append to the messages list (still scoped to the widget root).
  const mount = panelEl || messagesEl || row.closest('.at-root');
  if (!mount) return;
  mount.appendChild(overlay);
  input.focus({ preventScroll: true });

  // ── Close handlers: Cancel button, Esc key, click outside the modal card.
  const close = (): void => {
    overlay.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (e: KeyboardEvent): void => {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
    }
  };
  cancel.addEventListener('click', close);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close();
  });
  document.addEventListener('keydown', onKey);

  submit.addEventListener('click', () => {
    const comment = input.value.slice(0, MAX_COMMENT);
    submit.disabled = true;
    submit.textContent = ru ? 'Отправка…' : 'Sending…';
    cancel.disabled = true;
    input.disabled = true;
    modal.querySelector('.at-report-error')?.remove();

    submitReport(deps, ctx, comment)
      .then(() => {
        close();
        btn.classList.add('at-reported');
        btn.disabled = true;
        btn.setAttribute(
          'aria-label',
          ru ? 'Жалоба отправлена' : 'Report sent',
        );
        // Mark with the key derived from the reported context itself, not the
        // attach-time snapshot: by submit time the bubble has its final text.
        markReported(deps.config, reportedKey(ctx.kind, ctx.text));

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
        cancel.disabled = false;
        input.disabled = false;
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

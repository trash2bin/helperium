/**
 * Helperium Embed Widget — DOM Builder
 *
 * Creates the full widget DOM structure inside a host element.
 * Returns typed references to all interactive elements (UIRefs).
 */

import type { UIRefs, WidgetConfig } from './types';
import { ICONS, escapeHtml } from './icons';

/**
 * Creates an HTML element with className and optional innerHTML.
 *
 * @param tag   - Tag name.
 * @param cls   - CSS class string (space-separated).
 * @param html  - Optional innerHTML content.
 * @returns The created element.
 */
function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  cls: string,
  html?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = cls;
  if (html !== undefined) {
    node.innerHTML = html;
  }
  return node;
}

/**
 * Checks whether the browser supports getUserMedia for voice input.
 *
 * @returns `true` if the API is available.
 */
function hasGetUserMedia(): boolean {
  return !!(
    navigator.mediaDevices && navigator.mediaDevices.getUserMedia
  );
}

/**
 * Builds the complete widget DOM tree inside `host`.
 *
 * DOM layout:
 * ```
 * host
 *  ├─ button.at-trigger          — floating action button
 *  └─ div.at-panel.at-hidden     — chat panel
 *       ├─ div.at-head           — header with title, status, close
 *       ├─ div.at-messages       — message list container
 *       └─ form.at-form          — input area
 *            └─ div.at-form-row
 *                 ├─ textarea
 *                 ├─ button.at-mic-btn
 *                 └─ button.at-send-btn
 * ```
 *
 * @param host   - The host container (will be appended to Shadow DOM root).
 * @param config - Parsed widget configuration.
 * @returns Typed references to all interactive UI elements.
 */
export function buildWidget(host: HTMLDivElement, config: WidgetConfig): UIRefs {
  const posClass = config.position === 'left' ? 'at-left' : 'at-right';

  /* ── Trigger Button ── */
  const trigger = el('button', 'at-trigger ' + posClass, ICONS.chat);
  host.appendChild(trigger);

  /* ── Panel ── */
  const panel = el('div', 'at-panel ' + posClass + ' at-hidden');
  host.appendChild(panel);

  /* ── Header ── */
  const head = el('div', 'at-head');

  const headInfo = el('div', 'at-head-info');
  headInfo.innerHTML =
    '<strong>' + escapeHtml(config.title) + '</strong>' +
    '<span>' + escapeHtml(config.agent) + '</span>';

  const statusEl = el('div', 'at-head-status');
  statusEl.innerHTML =
    '<span class="at-dot"></span> ' +
    (config.lang === 'ru' ? 'Online' : 'Online');
  headInfo.appendChild(statusEl);

  const closeBtn = el('button', 'at-close', ICONS.close);
  head.appendChild(headInfo);
  head.appendChild(closeBtn);
  panel.appendChild(head);

  /* ── Messages Area ── */
  const messages = el('div', 'at-messages');
  panel.appendChild(messages);

  /* ── Form ── */
  const form = document.createElement('form');
  form.className = 'at-form';

  const textarea = document.createElement('textarea');
  textarea.rows = 1;
  textarea.placeholder = config.placeholder;
  textarea.style.height = '38px'; // match button height exactly

  const micBtn = el('button', 'at-mic-btn', ICONS.mic);
  micBtn.type = 'button';
  micBtn.title = config.lang === 'ru' ? 'Зажмите для записи' : 'Hold to record';
  // visibility controlled by CSS classes (at-show-mic / at-show-send / at-legacy)

  const sendBtn = el('button', 'at-send-btn', ICONS.send);
  sendBtn.type = 'submit';

  /* Swap container: holds mic + send, animates between them */
  const isTelegram = config.voiceToggle === 'telegram' && config.voiceInput && hasGetUserMedia();
  const swapBtn = el('div', 'at-swap-btn ' + (isTelegram ? 'at-show-mic' : 'at-show-send'));
  swapBtn.appendChild(micBtn);
  swapBtn.appendChild(sendBtn);

  /* Classic mode: both buttons visible, no swap animation */
  if (!isTelegram && config.voiceInput && hasGetUserMedia()) {
    swapBtn.classList.add('at-legacy');
  }
  /* No voice at all: hide mic completely */
  if (!config.voiceInput || !hasGetUserMedia()) {
    micBtn.style.display = 'none';
  }

  /* Form row: [textarea][swap-container] */
  const formRow = el('div', 'at-form-row');
  formRow.appendChild(textarea);
  formRow.appendChild(swapBtn);
  form.appendChild(formRow);

  /* Mic timer (above form row) */
  const micTimer = el('div', 'at-mic-timer');
  form.insertBefore(micTimer, formRow);

  panel.appendChild(form);

  /* ── Return typed refs ── */
  return { trigger, panel, messages, form, textarea, closeBtn, sendBtn, head, micBtn, micTimer, swapBtn };
}

/* ─── DOM Query / Mutation Helpers ─── */

/**
 * Finds the actual `.at-msg` bubble inside a row or returns the node itself.
 */
export function findMsgNode(n: HTMLElement): HTMLElement {
  if (n.classList.contains('at-msg-row'))
    return n.querySelector('.at-msg') || n;
  if (n.classList.contains('at-msg')) return n;
  return n;
}

/**
 * Removes a message row (or the node itself if not inside a row).
 */
export function removeMsgRow(targetNode: HTMLElement): void {
  const row = targetNode.closest('.at-msg-row');
  if (row) {
    row.remove();
    return;
  }
  targetNode.remove();
}

/**
 * Scrolls a container to the bottom.
 */
export function scrollToBottom(el: HTMLElement | null): void {
  if (el) el.scrollTop = el.scrollHeight;
}

/**
 * Checks if the user is scrolled near the bottom of a container.
 */
export function isScrolledNearBottom(el: HTMLElement | null): boolean {
  return !!(
    el && el.scrollHeight - el.scrollTop - el.clientHeight < 48
  );
}

/**
 * Message Management
 *
 * Functions for creating, restoring, and managing chat messages.
 * These functions take their dependencies as parameters (no closures).
 */

import { scrollToBottom } from './dom';
import { ICONS } from './icons';
import { renderMarkdown } from './markdown';
import { makeToolStrip } from './tools';
import type { AddMessageOptions, WidgetConfig } from './types';

/**
 * Builds one assistant message row (bubble + avatar). The shared factory for
 * live messages and restored history so per-message actions (problem-report
 * flag) attach identically on both paths.
 */
function buildAssistantRow(
  text: string,
  opts: AddMessageOptions,
): { row: HTMLDivElement; node: HTMLDivElement } {
  const row = document.createElement('div');
  row.className = 'at-msg-row';

  const node = document.createElement('div');
  node.className = 'at-msg at-assistant';

  if (opts.thinking) {
    node.dataset.raw = '';
    node.innerHTML = ICONS.thinking;
  } else {
    node.dataset.raw = text || '';
    node.innerHTML = renderMarkdown(text || '');
  }

  const avatar = document.createElement('div');
  avatar.className = 'at-avatar';
  avatar.textContent = 'AI';

  row.appendChild(node);
  row.appendChild(avatar);

  // Attach at row creation — including the thinking state: the live chat
  // path streams into this same node, so waiting for a "final" bubble would
  // leave fresh answers without the flag entirely.
  if (opts.report !== false && opts.onAssistantRow) {
    opts.onAssistantRow(row, node);
  }

  return { row, node };
}

/**
 * Creates a message element and appends it to the messages container.
 */
export function addMessage(
  kind: 'user' | 'assistant',
  text: string,
  messagesEl: HTMLElement,
  opts?: AddMessageOptions & { before?: Node }
): HTMLDivElement {
  const o = opts || {};

  if (kind === 'assistant') {
    const { row } = buildAssistantRow(text, o);

    if (o.before) {
      messagesEl.insertBefore(row, o.before);
    } else {
      messagesEl.appendChild(row);
    }
  } else {
    const node = document.createElement('div');
    node.className = 'at-msg at-user';
    node.textContent = text || '';

    if (o.before) {
      messagesEl.insertBefore(node, o.before);
    } else {
      messagesEl.appendChild(node);
    }
  }

  if (o.scroll !== false) {
    scrollToBottom(messagesEl);
  }

  return messagesEl.lastElementChild as HTMLDivElement;
}

/**
 * Restores chat history from sessionStorage.
 */
export function restoreHistory(
  config: WidgetConfig,
  messagesEl: HTMLElement,
  readStored: () => Array<{
    kind: string;
    text: string;
    tools: string[];
    ts?: number;
  }>,
  addMsg: (
    kind: 'user' | 'assistant',
    text: string,
    opts?: AddMessageOptions
  ) => HTMLDivElement,
  hooks?: { onAssistantRow?: (row: HTMLDivElement, node: HTMLDivElement) => void }
): void {
  const stored = readStored();
  if (!stored.length) {
    addMsg('assistant', config.greeting, {
      persist: false,
      scroll: false,
      report: false,
    });
    return;
  }

  messagesEl.innerHTML = '';
  let pendingToolNames: string[] = [];

  for (const msg of stored) {
    if (msg.kind === 'user') {
      addMsg('user', msg.text, { persist: false, scroll: false });
    } else if (msg.kind === 'assistant') {
      const tools = (msg.tools || []).filter(Boolean);
      const msgText = String(msg.text || '');

      if (!msgText.trim() && tools.length > 0) {
        pendingToolNames = pendingToolNames.concat(tools);
        continue;
      }

      const mergedTools = pendingToolNames.concat(tools);
      pendingToolNames = [];

      if (mergedTools.length > 0) {
        const strip = makeToolStrip(mergedTools);
        if (strip) messagesEl.appendChild(strip);
      }

      const { row } = buildAssistantRow(msgText, hooks?.onAssistantRow
        ? { onAssistantRow: hooks.onAssistantRow }
        : {});
      messagesEl.appendChild(row);
    }
  }

  if (pendingToolNames.length > 0) {
    const strip = makeToolStrip(pendingToolNames);
    if (strip) messagesEl.appendChild(strip);
  }

  scrollToBottom(messagesEl);
}

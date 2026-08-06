/**
 * Message Management
 *
 * Functions for creating, restoring, and managing chat messages.
 * These functions take their dependencies as parameters (no closures).
 */

import type { WidgetConfig, AddMessageOptions, StoredMessage } from './types';
import { ICONS } from './icons';
import { renderMarkdown } from './markdown';
import { scrollToBottom } from './dom';
import { makeToolStrip } from './tools';

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
    const row = document.createElement('div');
    row.className = 'at-msg-row';

    const avatar = document.createElement('div');
    avatar.className = 'at-avatar';
    avatar.textContent = 'AI';

    const node = document.createElement('div');
    node.className = 'at-msg at-assistant';

    if (o.thinking) {
      node.dataset.raw = '';
      node.innerHTML = ICONS.thinking;
    } else {
      node.dataset.raw = text || '';
      node.innerHTML = renderMarkdown(text || '');
    }

    row.appendChild(node);
    row.appendChild(avatar);

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
  readStored: () => Array<{ kind: string; text: string; tools: string[] }>,
  addMsg: (
    kind: 'user' | 'assistant',
    text: string,
    opts?: AddMessageOptions
  ) => HTMLDivElement
): void {
  const stored = readStored();
  if (!stored.length) {
    addMsg('assistant', config.greeting, {
      persist: false,
      scroll: false,
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

      const row = document.createElement('div');
      row.className = 'at-msg-row';

      const avatar = document.createElement('div');
      avatar.className = 'at-avatar';
      avatar.textContent = 'AI';

      const node = document.createElement('div');
      node.className = 'at-msg at-assistant';
      node.dataset.raw = msgText;
      node.innerHTML = renderMarkdown(msgText);

      row.appendChild(node);
      row.appendChild(avatar);
      messagesEl.appendChild(row);
    }
  }

  if (pendingToolNames.length > 0) {
    const strip = makeToolStrip(pendingToolNames);
    if (strip) messagesEl.appendChild(strip);
  }

  scrollToBottom(messagesEl);
}

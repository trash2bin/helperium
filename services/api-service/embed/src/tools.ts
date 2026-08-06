/**
 * Tool Strip UI
 *
 * Renders tool call indicators as pill badges.
 */

import { escapeHtml } from './icons';

/**
 * Maps a tool display name to an emoji icon.
 */
export function getToolIcon(displayText: string): string {
  const lower = displayText.toLowerCase();
  if (/поиск|найти|find|search/i.test(lower)) return '🔍';
  if (/чтение|get|получени/i.test(lower)) return '📋';
  if (/запрос|query/i.test(lower)) return '📊';
  if (/list|список/i.test(lower)) return '📋';
  return '⚡';
}

/**
 * Creates a tool strip element showing which tools were called.
 */
export function makeToolStrip(
  toolNames: string[],
  displayNames?: Record<string, string>
): HTMLDivElement | null {
  const names = displayNames || {};
  const unique = [...new Set(toolNames)];
  if (!unique.length) return null;

  const el = document.createElement('div');
  el.className = 'at-tool-strip';
  el.innerHTML = unique
    .map((name) => {
      const display = names[name] || name;
      const icon = getToolIcon(display);
      return `<span>${icon} ${escapeHtml(display)}</span>`;
    })
    .join('');
  return el;
}

/**
 * Ensures a tool strip exists before a message node, updating if already present.
 */
export function ensureToolStrip(
  targetNode: HTMLElement,
  toolNames: string[],
  displayNames: Record<string, string>,
  messagesEl: HTMLElement
): void {
  const unique = [...new Set(toolNames)];
  if (!unique.length) return;

  const ref =
    targetNode.closest?.('.at-msg-row') || targetNode;
  const prev = ref.previousElementSibling;

  if (prev && prev.className === 'at-tool-strip') {
    prev.innerHTML = unique
      .map((name) => {
        const display = displayNames[name] || name;
        const icon = getToolIcon(display);
        return `<span>${icon} ${escapeHtml(display)}</span>`;
      })
      .join('');
    return;
  }

  const strip = makeToolStrip(unique, displayNames);
  if (strip) messagesEl.insertBefore(strip, ref);
}

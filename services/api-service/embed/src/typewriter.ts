/**
 * Typewriter Effect
 *
 * Gradually reveals text with a blinking cursor.
 */

import { renderMarkdown } from './markdown';
import { scrollToBottom, isScrolledNearBottom } from './dom';

const TYPEWRITER_INTERVAL = 20;
const TYPEWRITER_BURST = 3;

/**
 * Appends a token to the target node's raw buffer and starts typewriter if needed.
 */
export function appendToken(
  target: HTMLDivElement,
  text: string,
  messagesEl: HTMLElement
): void {
  const raw = target.dataset.raw || '';
  target.dataset.raw = raw + text;

  if (!target.dataset.typewriterRunning) {
    target.dataset.typewriterRunning = '1';
    target.dataset.typewriterBuffer = raw + text;
    target.dataset.typewriterDisplayed = raw;
    revealTypewriter(target, messagesEl);
  } else {
    target.dataset.typewriterBuffer =
      (target.dataset.typewriterBuffer || '') + text;
  }
}

/**
 * Reveals text character by character with a cursor effect.
 */
function revealTypewriter(
  target: HTMLDivElement,
  messagesEl: HTMLElement
): void {
  const buffer = target.dataset.typewriterBuffer || '';
  const displayed = target.dataset.typewriterDisplayed || '';

  if (displayed.length >= buffer.length) {
    target.dataset.typewriterRunning = '';
    target.innerHTML = renderMarkdown(buffer);
    scrollToBottom(messagesEl);
    return;
  }

  const charsToReveal = Math.min(
    TYPEWRITER_BURST,
    buffer.length - displayed.length
  );
  const newDisplayed = buffer.slice(
    0,
    displayed.length + charsToReveal
  );
  target.dataset.typewriterDisplayed = newDisplayed;

  const rendered = renderMarkdown(newDisplayed);
  if (newDisplayed.length < buffer.length) {
    target.classList.add('at-typing-cursor');
    target.innerHTML = rendered;
  } else {
    target.classList.remove('at-typing-cursor');
    target.innerHTML = rendered;
    target.dataset.typewriterRunning = '';
    scrollToBottom(messagesEl);
    return;
  }

  if (isScrolledNearBottom(messagesEl)) {
    scrollToBottom(messagesEl);
  }

  setTimeout(() => revealTypewriter(target, messagesEl), TYPEWRITER_INTERVAL);
}

/**
 * Sets the final text on a message node (removes thinking state).
 */
export function setFinalText(
  target: HTMLDivElement,
  text: string
): void {
  target.classList.remove('at-thinking');
  target.dataset.raw = text;
  target.innerHTML = renderMarkdown(text);
}

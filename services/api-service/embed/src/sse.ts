/**
 * Helperium Embed Widget — SSE Streaming
 *
 * Handles POST fetch to the chat SSE endpoint and streams
 * token/tool/final/done/error events from the ReadableStream.
 */

import { ICONS } from './icons';
import type { WidgetConfig } from './types';

/** Callbacks for SSE stream events. */
export interface StreamChatCallbacks {
  /** Called when a token is received during streaming. */
  onToken: (text: string) => void;
  /** Called when a final (complete) message is received. */
  onFinal: (text: string) => void;
  /** Called when the agent invokes a tool. */
  onToolCall: (name: string, displayName?: string) => void;
  /** Called when audio data is received. */
  onAudio: (data: string) => void;
  /** Called when the stream is done. */
  onDone: (raw: string, tools: string[]) => void;
  /** Called when an error event is received. */
  onError: (text: string) => void;
}

/** Options for the streamChat function. */
export interface StreamChatOpts {
  /** The user message to send. */
  message: string;
  /** The target assistant message DOM node (bubble). */
  targetNode: HTMLDivElement;
  /** Parsed widget configuration. */
  config: WidgetConfig;
  /** Current session ID. */
  sessionId: string;
  /** Messages container element. */
  messagesEl: HTMLDivElement;
  /** Map of message text → retry count. */
  retryAttempts: Map<string, number>;
  /** Maximum number of retries before showing failure. */
  maxRetries: number;
  /** Callbacks for stream events. */
  callbacks: StreamChatCallbacks;
  /**
   * Creates a new message in the DOM.
   * Used internally for retry failure messages.
   */
  addMessage: (
    kind: 'user' | 'assistant',
    text: string,
    opts?: Record<string, unknown>,
  ) => HTMLDivElement;
  /** Removes a message row from the DOM. */
  removeMsgRow: (node: HTMLDivElement) => void;
  /** Schedules a retry after a delay (shows countdown). */
  scheduleRetry: (message: string, delayMs: number) => void;
  /** Retries the chat with a new assistant bubble. */
  retryChat: (message: string) => void;
  /** Scrolls the messages container to the bottom. */
  scrollToBottom: (el: HTMLDivElement) => void;
}

/**
 * Callbacks for raw SSE stream reading (without streamChat retry/fetch logic).
 * Used by voice chat and restoreHistory to process SSE events.
 */
export interface SSEReadCallbacks {
  onToken: (text: string) => void;
  onFinal: (text: string) => void;
  onToolCall: (name: string, displayName?: string) => void;
  onAudio: (data: string) => void;
  onDone: (raw: string, tools: string[]) => void;
  onError: (text: string) => void;
}

/**
 * Reads an SSE stream using async/await (no Promise chain) and dispatches
 * parsed JSON events to the appropriate callback.
 *
 * @param response - The fetch Response with a readable body.
 * @param targetNode - The assistant message bubble to update (dataset tools/displayNames).
 * @param callbacks - Event callbacks.
 * @param lang - Language for fallback messages ('ru' or 'en').
 * @returns A promise that resolves when the stream is consumed.
 */
export async function readSSEStream(
  response: Response,
  targetNode: HTMLDivElement,
  callbacks: SSEReadCallbacks,
  lang: string = 'en',
): Promise<void> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop()!;

    for (const chunk of parts) {
      const line = chunk.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;

      let payload: {
        type: string;
        text?: string;
        name?: string;
        display_name?: string;
        data?: string;
      };
      try {
        payload = JSON.parse(line.slice(5).trim());
      } catch {
        continue;
      }

      if (targetNode.classList.contains('at-thinking')) {
        targetNode.classList.remove('at-thinking');
      }

      switch (payload.type) {
        case 'token':
          callbacks.onToken(payload.text || '');
          break;

        case 'final':
          callbacks.onFinal(payload.text || '');
          break;

        case 'tool_call': {
          const tools: string[] = JSON.parse(
            targetNode.dataset.tools || '[]',
          );
          const displayNames: Record<string, string> = JSON.parse(
            targetNode.dataset.displayNames || '{}',
          );

          if (payload.name && !tools.includes(payload.name)) {
            tools.push(payload.name);
            targetNode.dataset.tools = JSON.stringify(tools);
          }
          if (
            payload.display_name &&
            payload.name &&
            !displayNames[payload.name]
          ) {
            displayNames[payload.name] = payload.display_name;
            targetNode.dataset.displayNames = JSON.stringify(displayNames);
          }
          callbacks.onToolCall(payload.name || '', payload.display_name);
          break;
        }

        case 'audio':
          if (payload.data) {
            callbacks.onAudio(payload.data);
          }
          break;

        case 'done':
          if (targetNode.classList.contains('at-error')) return;
          if (!targetNode.dataset.raw?.trim()) {
            callbacks.onFinal(
              lang === 'ru'
                ? 'Не удалось получить ответ.'
                : 'No response.',
            );
          }
          let toolNames: string[] = [];
          try {
            toolNames = JSON.parse(targetNode.dataset.tools || '[]');
          } catch {
            /* ignore */
          }
          callbacks.onDone(targetNode.dataset.raw || '', toolNames);
          targetNode.dataset.saved = 'true';
          break;

        case 'error':
          targetNode.classList.remove('at-thinking');
          targetNode.classList.add('at-error');
          targetNode.textContent =
            payload.text ||
            (lang === 'ru'
              ? 'Произошла ошибка.'
              : 'An error occurred.');
          break;
      }
    }
  }
}

/**
 * Sends a chat message via POST and processes the SSE response stream.
 *
 * Handles:
 * - 429 Too Many Requests (with Retry-After header)
 * - Non-OK responses
 * - Connection errors
 * - Successful SSE streaming via readSSEStream
 *
 * @param opts - Full streaming options.
 */
export function streamChat(opts: StreamChatOpts): void {
  const {
    message,
    targetNode,
    config,
    sessionId,
    messagesEl,
    retryAttempts,
    maxRetries,
    callbacks,
    removeMsgRow,
    scheduleRetry,
    retryChat,
    scrollToBottom,
  } = opts;

  targetNode.classList.add('at-thinking');

  const url = config.apiBase + '/api/chat/' + encodeURIComponent(config.agent);

  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  })
    .then((response) => {
      /* ── 429 Rate Limit ── */
      if (response.status === 429) {
        targetNode.classList.remove('at-thinking');
        removeMsgRow(targetNode);

        const retryAfter = response.headers.get('Retry-After');
        let delay = 5;
        if (retryAfter) {
          const parsed = parseInt(retryAfter, 10);
          if (!isNaN(parsed) && parsed > 0) delay = parsed;
        }

        retryAttempts.set(message, (retryAttempts.get(message) || 0) + 1);
        const attempts = retryAttempts.get(message)!;

        if (attempts >= maxRetries) {
          const failMsg = document.createElement('div');
          failMsg.className = 'at-msg at-assistant at-error';
          failMsg.innerHTML = '\u26A0\uFE0F Server overloaded.';
          const retryBtn = document.createElement('button');
          retryBtn.className = 'at-retry-btn';
          retryBtn.textContent = 'Retry';
          failMsg.appendChild(retryBtn);
          messagesEl.appendChild(failMsg);
          scrollToBottom(messagesEl);
          retryBtn.addEventListener('click', () => {
            retryAttempts.delete(message);
            failMsg.remove();
            retryChat(message);
          });
          return;
        }

        const rateMsg = document.createElement('div');
        rateMsg.className = 'at-msg at-assistant';
        rateMsg.textContent =
          '\u26A0\uFE0F ' +
          (config.lang === 'ru'
            ? 'Сервер перегружен. Повтор через'
            : 'Server overloaded. Retry in') +
          ' ' +
          delay +
          's.';
        messagesEl.appendChild(rateMsg);
        scrollToBottom(messagesEl);

        scheduleRetry(message, delay * 1000);
        return;
      }

      /* ── Non-OK response ── */
      if (!response.ok) {
        targetNode.classList.remove('at-thinking');
        targetNode.classList.add('at-error');
        targetNode.textContent = 'Error: ' + response.status;
        return;
      }

      /* ── Successful SSE stream ── */
      return readSSEStream(response, targetNode, callbacks, config.lang);
    })
    .catch(() => {
      targetNode.classList.remove('at-thinking');
      targetNode.classList.add('at-error');
      targetNode.innerHTML =
        '\u26A0\uFE0F ' +
        (config.lang === 'ru'
          ? 'Нет соединения с сервером.'
          : 'No connection to server.') +
        '<br><button class="at-retry-btn">' +
        (config.lang === 'ru' ? 'Повторить' : 'Retry') +
        '</button>';
      const btn = targetNode.querySelector('.at-retry-btn') as HTMLButtonElement | null;
      if (btn) {
        btn.addEventListener('click', () => {
          targetNode.classList.remove('at-error');
          targetNode.innerHTML = ICONS.thinking;
          streamChat(opts);
        });
      }
    });
}

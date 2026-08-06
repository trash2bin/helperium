/**
 * Helperium Embed Widget — Entry Point
 *
 * Initialises the chat widget: parses config, creates Shadow DOM,
 * wires up event handlers, and exposes the global
 * `window.__agentTutorSetAgent` bridge for runtime agent switching.
 */

import type { WidgetConfig, AddMessageOptions } from './types';
import { parseConfig } from './config';
import { ICONS, escapeHtml } from './icons';
import { getSessionId } from './storage';
import { buildWidget } from './dom';
import { streamChat } from './sse';
import {
  createVoiceState,
  startVoiceRecording,
  stopVoiceRecording,
  streamVoiceChat,
  playAudioBase64,
} from './voice';
import {
  findMsgNode,
  removeMsgRow,
  scrollToBottom,
} from './dom';
import { ensureToolStrip } from './tools';
import { appendToken, setFinalText } from './typewriter';
import { addMessage, restoreHistory } from './messages';
import { createStorage } from './storage';

import CSS_TEXT from './_bundle.css';

/* ─── Constants ─── */
const MAX_RETRIES = 3;

/* ─── Helpers ─── */

function findScript(): HTMLScriptElement | null {
  const cs = document.currentScript;
  if (cs && cs instanceof HTMLScriptElement) return cs;
  // Try script[data-agent] (attribute-based config)
  const byAttr = document.querySelector('script[data-agent]');
  if (byAttr) return byAttr as HTMLScriptElement;
  // Fallback: find the last script that loaded this bundle
  const bySrc = document.querySelector('script[src*="embed.js"]');
  if (bySrc) return bySrc as HTMLScriptElement;
  // Last resort: any script tag (for window.EMBED_CONFIG mode)
  return null;
}

function hexToRgb(hex: string): string {
  const h = hex.replace('#', '');
  const num = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
  return `${(num >> 16) & 255}, ${(num >> 8) & 255}, ${num & 255}`;
}

function buildDynamicStyle(cfg: WidgetConfig): string {
  const headerBg = cfg.headerColor || cfg.accent;
  return `:host {
  --accent: ${cfg.accent};
  --accent-rgb: ${hexToRgb(cfg.accent)};
  --accent-strong: ${cfg.accent};
  --trigger-offset-bottom: ${cfg.triggerOffsetBottom};
  --panel-width: ${cfg.width};
  --panel-height: ${cfg.height};
  --header-bg: ${headerBg};
  --bot-bubble-bg: ${cfg.botBubbleColor};
  --bot-bubble-text: ${cfg.botBubbleText};
}
${cfg.showHeader ? '' : '.at-head { display: none; }'}`;
}

/* ─── Main Widget Initialization ─── */

export function initWidget(): void {
  const script = findScript();

  const config = parseConfig(script as HTMLScriptElement);
  if (!config.agent) return;

  let readStored: () => Array<{ kind: string; text: string; tools: string[] }>;
  let appendStored: (kind: 'user' | 'assistant', text: string, tools?: string[]) => void;
  let sessionId: string;

  /* ── Storage ── */
  const storageKey = 'at_messages_' + config.agent;
  const sessionKey = 'at_session_' + config.agent;
  sessionId = getSessionId(sessionKey);
  ({ readStored, appendStored } = createStorage(storageKey, sessionId));

  /* ── State ── */
  const voice = createVoiceState();
  const retryAttempts = new Map<string, number>();

  /* ── DOM Setup ─── */
  const host = document.createElement('div');
  host.id = 'helperium-widget-' + config.agent.replace(/[^a-zA-Z0-9_-]/g, '');
  const shadow = host.attachShadow({ mode: 'open' });

  const baseStyle = document.createElement('style');
  baseStyle.textContent = CSS_TEXT;
  shadow.appendChild(baseStyle);

  const dynamicStyle = document.createElement('style');
  dynamicStyle.textContent = buildDynamicStyle(config);
  shadow.appendChild(dynamicStyle);

  const root = document.createElement('div');
  root.className = 'at-root';
  shadow.appendChild(root);

  const ui = buildWidget(root, config);
  const messagesEl = ui.messages;

  /* ── Bound addMessage (attaches persist + storage) ── */
  function addMsg(
    kind: 'user' | 'assistant',
    text: string,
    opts?: AddMessageOptions
  ): HTMLDivElement {
    const node = addMessage(kind, text, messagesEl, opts);
    if (opts?.persist) {
      appendStored(kind, text, opts.tools);
    }
    return node;
  }

  /* ── Chat Logic ── */

  function scheduleRetry(message: string, delayMs: number): void {
    let remaining = Math.ceil(delayMs / 1000);

    const countdownMsg = document.createElement('div');
    countdownMsg.className = 'at-msg at-retry-countdown';
    countdownMsg.textContent =
      (config.lang === 'ru' ? 'Повтор через' : 'Retry in') +
      ' ' +
      remaining +
      's...';
    messagesEl.appendChild(countdownMsg);
    scrollToBottom(messagesEl);

    const interval = setInterval(() => {
      remaining--;
      if (remaining <= 0) {
        clearInterval(interval);
        countdownMsg.remove();
        retryChat(message);
      } else {
        countdownMsg.textContent =
          (config.lang === 'ru' ? 'Повтор через' : 'Retry in') +
          ' ' +
          remaining +
          's...';
      }
    }, 1000);
  }

  function retryChat(message: string): void {
    const answerNode = addMsg('assistant', '', {
      thinking: true,
      persist: false,
      scroll: false,
    });
    const bubble = findMsgNode(answerNode);
    doStreamChat(message, bubble as HTMLDivElement);
  }

  function doStreamChat(
    message: string,
    targetNode: HTMLDivElement
  ): void {
    streamChat({
      message,
      targetNode,
      config,
      sessionId,
      messagesEl,
      retryAttempts,
      maxRetries: MAX_RETRIES,
      callbacks: {
        onToken: (text: string) => appendToken(targetNode, text, messagesEl),
        onFinal: (text: string) => setFinalText(targetNode, text),
        onToolCall: (name: string, displayName?: string) => {
          // Accumulate tool names in dataset
          const tools: string[] = JSON.parse(targetNode.dataset.tools || '[]');
          const displayNames: Record<string, string> = JSON.parse(
            targetNode.dataset.displayNames || '{}'
          );
          if (!tools.includes(name)) {
            tools.push(name);
            targetNode.dataset.tools = JSON.stringify(tools);
          }
          if (displayName && !displayNames[name]) {
            displayNames[name] = displayName;
            targetNode.dataset.displayNames = JSON.stringify(displayNames);
          }
          ensureToolStrip(targetNode, tools, displayNames, messagesEl);
        },
        onAudio: (data: string) => playAudioBase64(data),
        onDone: (raw: string, tools: string[]) => {
          appendStored('assistant', raw, tools);
        },
        onError: (text: string) => {
          targetNode.classList.remove('at-thinking');
          targetNode.classList.add('at-error');
          targetNode.textContent = text;
        },
      },
      addMessage: addMsg as (kind: string, text: string, opts?: Record<string, unknown>) => HTMLDivElement,
      removeMsgRow: removeMsgRow as (node: HTMLDivElement) => void,
      scheduleRetry: (msg: string, delayMs: number) => scheduleRetry(msg, delayMs),
      retryChat,
      scrollToBottom,
    });
  }

  /* ── Event Handlers ── */

  ui.trigger.addEventListener('click', () => {
    ui.panel.classList.remove('at-hidden');
    ui.trigger.style.display = 'none';
    ui.textarea.focus();
    scrollToBottom(messagesEl);
  });

  ui.closeBtn.addEventListener('click', () => {
    ui.panel.classList.add('at-hidden');
    ui.trigger.style.display = 'flex';
  });

  function handleSubmit(): void {
    const text = ui.textarea.value.trim();
    if (!text) return;
    ui.textarea.value = '';
    ui.textarea.style.height = 'auto';
    // Reset swap button back to mic after sending
    updateSwapBtn();
    addMsg('user', text, { persist: true });
    const answerNode = addMsg('assistant', '', {
      thinking: true,
      persist: false,
      scroll: false,
    });
    const bubble = findMsgNode(answerNode);
    doStreamChat(text, bubble as HTMLDivElement);
  }

  ui.textarea.addEventListener('input', function () {
    // Auto-resize textarea up to max-height
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    // Telegram-style swap: mic ↔ send
    updateSwapBtn();
  });

  ui.textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  });

  ui.form.addEventListener('submit', (e) => {
    e.preventDefault();
    handleSubmit();
  });

  /* ── Telegram-style mic↔send swap ── */
  function updateSwapBtn(): void {
    if (config.voiceToggle !== 'telegram' || !voiceAvail) return;
    const hasText = ui.textarea.value.trim().length > 0;
    if (hasText) {
      ui.swapBtn.classList.remove('at-show-mic');
      ui.swapBtn.classList.add('at-show-send');
    } else {
      ui.swapBtn.classList.remove('at-show-send');
      ui.swapBtn.classList.add('at-show-mic');
    }
  }

  /* ── Voice: Telegram-style hold-to-record or classic toggle ── */
  const voiceAvail =
    config.voiceInput &&
    typeof navigator.mediaDevices?.getUserMedia === 'function';

  if (config.voiceToggle === 'telegram' && voiceAvail) {
    /* ── Telegram mode: hold mic = record, release = stop+send ── */
    let holding = false;

    ui.micBtn.addEventListener('mousedown', (e) => {
      e.preventDefault();
      if (holding) return;
      holding = true;
      ui.micBtn.classList.add('at-mic-holding');
      startVoiceRecording(voice, {
        micBtn: ui.micBtn,
        micTimer: ui.micTimer,
        config,
        sessionId,
        addMessage: addMsg as (kind: string, text: string, opts?: Record<string, unknown>) => HTMLDivElement,
        onStreamVoice: makeVoiceStreamHandler(),
        onStreamChat: doStreamChat,
      });
    });

    const releaseHandler = () => {
      if (!holding) return;
      holding = false;
      ui.micBtn.classList.remove('at-mic-holding');
      stopVoiceRecording(voice);
    };
    ui.micBtn.addEventListener('mouseup', releaseHandler);
    ui.micBtn.addEventListener('mouseleave', releaseHandler);

    /* Touch support */
    ui.micBtn.addEventListener('touchstart', (e) => {
      e.preventDefault();
      if (holding) return;
      holding = true;
      ui.micBtn.classList.add('at-mic-holding');
      startVoiceRecording(voice, {
        micBtn: ui.micBtn,
        micTimer: ui.micTimer,
        config,
        sessionId,
        addMessage: addMsg as (kind: string, text: string, opts?: Record<string, unknown>) => HTMLDivElement,
        onStreamVoice: makeVoiceStreamHandler(),
        onStreamChat: doStreamChat,
      });
    }, { passive: false });
    ui.micBtn.addEventListener('touchend', releaseHandler);
    ui.micBtn.addEventListener('touchcancel', releaseHandler);

  } else if (voiceAvail) {
    /* ── Classic mode: toggle mic on/off ── */
    ui.micBtn.style.display = 'flex';
    ui.micBtn.addEventListener('mousedown', (e) => {
      e.preventDefault();
      if (ui.micBtn.classList.contains('at-mic-recording')) {
        stopVoiceRecording(voice);
      } else {
        startVoiceRecording(voice, {
          micBtn: ui.micBtn,
        micTimer: ui.micTimer,
          config,
          sessionId,
          addMessage: addMsg as (kind: string, text: string, opts?: Record<string, unknown>) => HTMLDivElement,
          onStreamVoice: makeVoiceStreamHandler(),
          onStreamChat: doStreamChat,
        });
      }
    });
  }

  function makeVoiceStreamHandler() {
    return (blob: Blob, target: HTMLDivElement) => {
      streamVoiceChat(blob, target, {
        config,
        sessionId,
        messagesEl,
        callbacks: {
          onToken: (text: string) => appendToken(target, text, messagesEl),
          onFinal: (text: string) => setFinalText(target, text),
          onToolCall: (name: string, displayName?: string) => {
            ensureToolStrip(target, [name], displayName ? { [name]: displayName } : {}, messagesEl);
          },
          onAudio: (data: string) => playAudioBase64(data),
          onDone: (raw: string, tools: string[]) => {
            appendStored('assistant', raw, tools);
          },
          onError: (text: string) => {
            target.classList.remove('at-thinking');
            target.classList.add('at-error');
            target.textContent = text;
          },
        },
        scrollToBottom: scrollToBottom as (el: HTMLDivElement) => void,
      });
    };
  }

  /* ── Restore History ── */
  restoreHistory(config, messagesEl, readStored, addMsg);

  /* ── Mount to DOM ── */
  document.body.appendChild(host);

  /* ── Global Bridge (for app.js agent switching) ── */
  (window as unknown as Record<string, unknown>).__agentTutorSetAgent = (name: string) => {
    if (!name) return;
    // Update config
    (config as { agent: string }).agent = name;
    // Reset storage keys
    const newStorageKey = 'at_messages_' + name;
    const newSessionKey = 'at_session_' + name;
    const newSessionId = getSessionId(newSessionKey);
    const newStore = createStorage(newStorageKey, newSessionId);
    // Rebind closures
    readStored = newStore.readStored;
    appendStored = newStore.appendStored;
    sessionId = newSessionId;
    // Update header
    const infoEl = ui.head.querySelector('.at-head-info');
    if (infoEl) {
      infoEl.innerHTML =
        '<strong>' + escapeHtml(config.title) + '</strong>' +
        '<span>' + escapeHtml(name) + '</span>';
    }
    // Clear messages and restore history
    messagesEl.innerHTML = '';
    restoreHistory(config, messagesEl, readStored, addMsg);
    // Persist agent choice
    try {
      localStorage.setItem('agentTutorAgentId', name);
    } catch { /* private browsing */ }
  };

  /* ── Restore agent from localStorage (dashboard sync) ── */
  try {
    const storedAgent = localStorage.getItem('agentTutorAgentId');
    if (storedAgent && config.agent !== storedAgent) {
      (window as unknown as Record<string, (n: string) => void>).__agentTutorSetAgent?.(storedAgent);
    }
  } catch { /* private browsing */ }
}

/* ── Auto-init ── */
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initWidget);
} else {
  initWidget();
}

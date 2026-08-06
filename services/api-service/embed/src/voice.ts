/**
 * Helperium Embed Widget — Voice Input
 *
 * Manages microphone recording via MediaRecorder API,
 * voice chat streaming, and base64 audio playback.
 */

import type { WidgetConfig } from './types';
import { readSSEStream, type SSEReadCallbacks } from './sse';
import { ICONS } from './icons';

/** Mutable state for voice recording session. */
export interface VoiceState {
  mediaRecorder: MediaRecorder | null;
  micChunks: Blob[];
  micStream: MediaStream | null;

  micStartTime: number;
  micDuration: number;
  micTimerInterval: number | null;
}

/** Maximum recording duration in seconds (0 = unlimited). */
const MAX_RECORDING_SEC = 120;

/** Preferred MIME type for recording. */
const PREFERRED_MIME = 'audio/webm;codecs=opus';
const FALLBACK_MIME = 'audio/webm';

/**
 * Creates a fresh voice state object.
 *
 * @returns An initialized VoiceState.
 */
export function createVoiceState(): VoiceState {
  return {
    mediaRecorder: null,
    micChunks: [],
    micStream: null,

    micStartTime: 0,
    micDuration: 0,
    micTimerInterval: null,
  };
}

/**
 * Updates the mic timer display with elapsed time.
 *
 * @param state - Current voice state.
 * @param micTimerEl - The DOM element to update.
 */
export function updateMicTimer(state: VoiceState, micTimerEl: HTMLDivElement): void {
  state.micDuration = Math.floor((Date.now() - state.micStartTime) / 1000);
  const m = Math.floor(state.micDuration / 60);
  const s = state.micDuration % 60;
  micTimerEl.textContent = (m > 0 ? m + 'm ' : '') + s + 's';
}

/** Options for startVoiceRecording. */
export interface StartVoiceOpts {
  /** The mic button element. */
  micBtn: HTMLButtonElement;
  /** The mic timer display element. */
  micTimer: HTMLDivElement;

  /** Parsed widget configuration. */
  config: WidgetConfig;
  /** Current session ID. */
  sessionId: string;
  /** Callback to add a message to the chat. */
  addMessage: (
    kind: 'user' | 'assistant',
    text: string,
    opts?: Record<string, unknown>,
  ) => HTMLDivElement;
  /** Callback to stream voice chat response. */
  onStreamVoice: (blob: Blob, targetNode: HTMLDivElement) => void;
  /** Callback to stream text chat response. */
  onStreamChat: (message: string, targetNode: HTMLDivElement) => void;
}

/**
 * Starts microphone recording using MediaRecorder.
 *
 * On stop, sends the recorded blob to the voice chat endpoint.
 *
 * @param state - Mutable voice state.
 * @param opts - DOM refs and callbacks.
 */
export function startVoiceRecording(
  state: VoiceState,
  opts: StartVoiceOpts,
): void {
  if (state.mediaRecorder && state.mediaRecorder.state === 'recording') return;

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    opts.micBtn.classList.add('at-mic-disabled');
    return;
  }

  navigator.mediaDevices
    .getUserMedia({ audio: true })
    .then((stream) => {
      state.micStream = stream;
      state.micChunks = [];

      let mimeType = PREFERRED_MIME;
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = FALLBACK_MIME;
      }

      state.mediaRecorder = new MediaRecorder(stream, { mimeType });

      state.mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) state.micChunks.push(e.data);
      };

      state.mediaRecorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        state.micStream = null;
        opts.micBtn.innerHTML = ICONS.mic;
        opts.micBtn.classList.remove('at-mic-recording');
        opts.micTimer.classList.remove('at-mic-timer-visible');
        if (state.micTimerInterval) {
          clearInterval(state.micTimerInterval);
          state.micTimerInterval = null;
        }

        const blob = new Blob(state.micChunks, { type: mimeType });
        if (blob.size === 0) return;

        const durStr = opts.micTimer.textContent || state.micDuration + 's';
        opts.addMessage('user', '\uD83C\uDFA4 ' + durStr, { persist: true });

        const answerNode = opts.addMessage('assistant', '', {
          thinking: true,
          persist: false,
          scroll: false,
        });
        opts.onStreamVoice(blob, answerNode);
      };

      state.mediaRecorder.start();
      opts.micBtn.innerHTML = ICONS.micOff;
      opts.micBtn.classList.add('at-mic-recording');
      opts.micTimer.classList.add('at-mic-timer-visible');
      state.micStartTime = Date.now();
      state.micDuration = 0;
      updateMicTimer(state, opts.micTimer);
      state.micTimerInterval = window.setInterval(
        () => updateMicTimer(state, opts.micTimer),
        1000,
      );

      if (MAX_RECORDING_SEC > 0) {
        setTimeout(() => {
          if (state.mediaRecorder && state.mediaRecorder.state === 'recording') {
            state.mediaRecorder.stop();
          }
        }, MAX_RECORDING_SEC * 1000);
      }
    })
    .catch((err: DOMException) => {
      if (
        err.name === 'NotAllowedError' ||
        err.name === 'PermissionDeniedError'
      ) {
        opts.addMessage(
          'assistant',
          opts.config.lang === 'ru'
            ? '\u274C Разрешите доступ к микрофону в настройках браузера'
            : '\u274C Please allow microphone access in browser settings',
          { persist: false },
        );
      } else {
        opts.micBtn.classList.add('at-mic-disabled');
      }
    });
}

/**
 * Stops the current microphone recording (if active).
 *
 * @param state - Mutable voice state.
 */
export function stopVoiceRecording(state: VoiceState): void {
  if (state.mediaRecorder && state.mediaRecorder.state === 'recording') {
    state.mediaRecorder.stop();
  }
}

/** Options for streamVoiceChat. */
export interface StreamVoiceOpts {
  /** Parsed widget configuration. */
  config: WidgetConfig;
  /** Current session ID. */
  sessionId: string;
  /** Messages container element. */
  messagesEl: HTMLDivElement;
  /** Callbacks for SSE events. */
  callbacks: SSEReadCallbacks;
  /** Callback to scroll messages to bottom. */
  scrollToBottom: (el: HTMLDivElement) => void;
}

/**
 * Sends a voice blob to the voice chat endpoint and streams the response.
 *
 * @param blob - The recorded audio blob.
 * @param targetNode - The assistant message bubble to update.
 * @param opts - Config and DOM callbacks.
 */
export function streamVoiceChat(
  blob: Blob,
  targetNode: HTMLDivElement,
  opts: StreamVoiceOpts,
): void {
  targetNode.classList.add('at-thinking');

  const url = opts.config.apiBase + '/api/chat/voice';
  const formData = new FormData();
  formData.append('audio', blob, 'voice.webm');
  formData.append('session_id', opts.sessionId);
  formData.append('agent', opts.config.agent);
  formData.append('lang', opts.config.lang);

  fetch(url, { method: 'POST', body: formData })
    .then((response) => {
      if (response.status === 429) {
        targetNode.classList.remove('at-thinking');
        targetNode.remove();
        const msg = document.createElement('div');
        msg.className = 'at-msg at-assistant';
        msg.textContent =
          opts.config.lang === 'ru'
            ? '\u26A0\uFE0F Сервер перегружен. Попробуйте позже.'
            : '\u26A0\uFE0F Server overloaded. Try again later.';
        opts.messagesEl.appendChild(msg);
        opts.scrollToBottom(opts.messagesEl);
        return;
      }

      if (!response.ok) {
        targetNode.classList.remove('at-thinking');
        targetNode.classList.add('at-error');
        targetNode.textContent = 'Error: ' + response.status;
        return;
      }

      return readSSEStream(response, targetNode, opts.callbacks, opts.config.lang);
    })
    .catch(() => {
      targetNode.classList.remove('at-thinking');
      targetNode.classList.add('at-error');
      targetNode.innerHTML =
        '\u26A0\uFE0F ' +
        (opts.config.lang === 'ru'
          ? 'Ошибка соединения.'
          : 'Connection error.');
    });
}

/**
 * Plays a base64-encoded audio string as an Audio element.
 *
 * Reserved for future audio output from the agent.
 *
 * @param b64data - Base64-encoded audio (e.g. MP3).
 */
export function playAudioBase64(b64data: string): void {
  try {
    const binaryStr = atob(b64data);
    const byteArray = new Uint8Array(binaryStr.length);
    for (let i = 0; i < binaryStr.length; i++) {
      byteArray[i] = binaryStr.charCodeAt(i);
    }
    const blob = new Blob([byteArray], { type: 'audio/mpeg' });
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.play().catch(() => {});
  } catch {
    /* ignore decode errors */
  }
}

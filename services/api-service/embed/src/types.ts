/**
 * Helperium Embed Widget — Type Definitions
 *
 * All interfaces and types for the embeddable chat widget.
 */

/* ─── Configuration (parsed from data-* attributes) ─── */

export interface WidgetConfig {
  readonly agent: string;
  readonly apiBase: string;
  readonly title: string;
  readonly greeting: string;
  readonly accent: string;
  readonly position: 'left' | 'right';
  readonly lang: 'ru' | 'en';
  readonly placeholder: string;
  readonly width: string;
  readonly height: string;
  readonly triggerOffsetBottom: string;
  readonly headerColor: string;
  readonly showHeader: boolean;
  readonly botBubbleColor: string;
  readonly botBubbleText: string;
  readonly voiceInput: boolean;
  readonly voiceOutput: boolean;
  /** 'classic' = toggle mic on/off; 'telegram' = hold-to-record, swap with send */
  readonly voiceToggle: 'classic' | 'telegram';
}

/* ─── Icon Set ─── */

export interface IconSet {
  readonly chat: string;
  readonly close: string;
  readonly send: string;
  readonly mic: string;
  readonly micOff: string;
  readonly thinking: string;
}

/* ─── Widget State ─── */

export interface WidgetState {
  sessionId: string | null;
  open: boolean;
  messages: Message[];
}

export interface Message {
  readonly kind: 'user' | 'assistant';
  readonly text: string;
  readonly tools?: string[];
  readonly timestamp?: number;
}

/* ─── SSE Event Types ─── */

export type SSEEventType = 'token' | 'final' | 'tool_call' | 'audio' | 'done' | 'error';

export interface SSETokenEvent {
  readonly type: 'token';
  readonly text: string;
}

export interface SSEFinalEvent {
  readonly type: 'final';
  readonly text: string;
}

export interface SSEToolCallEvent {
  readonly type: 'tool_call';
  readonly name: string;
  readonly display_name?: string;
}

export interface SSEAudioEvent {
  readonly type: 'audio';
  readonly data: string; // base64 audio
}

export interface SSEDoneEvent {
  readonly type: 'done';
}

export interface SSEErrorEvent {
  readonly type: 'error';
  readonly text: string;
}

export type SSEEvent =
  | SSETokenEvent
  | SSEFinalEvent
  | SSEToolCallEvent
  | SSEAudioEvent
  | SSEDoneEvent
  | SSEErrorEvent;

/* ─── DOM Element References ─── */

export interface UIRefs {
  readonly trigger: HTMLButtonElement;
  readonly panel: HTMLDivElement;
  readonly messages: HTMLDivElement;
  readonly form: HTMLFormElement;
  readonly textarea: HTMLTextAreaElement;
  readonly closeBtn: HTMLButtonElement;
  readonly sendBtn: HTMLButtonElement;
  readonly head: HTMLDivElement;
  readonly micBtn: HTMLButtonElement;
  readonly micTimer: HTMLDivElement;

  readonly swapBtn: HTMLDivElement;
}

/* ─── AddMessage Options ─── */

export interface AddMessageOptions {
  readonly persist?: boolean;
  readonly thinking?: boolean;
  readonly scroll?: boolean;
  readonly tools?: string[];
  readonly before?: Node;
}

/* ─── Stored Message (sessionStorage format) ─── */

export interface StoredMessage {
  readonly kind: 'user' | 'assistant';
  readonly text: string;
  readonly tools?: string[];
}

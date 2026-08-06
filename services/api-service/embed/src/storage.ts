/**
 * Helperium Embed Widget — Session & Storage Utilities
 *
 * Manages widget session IDs and message history persistence
 * via sessionStorage / localStorage.
 */

import type { StoredMessage } from './types';

/**
 * Retrieves the current session ID for the given storage key,
 * or creates a new UUID and persists it.
 *
 * @param key - The sessionStorage key (e.g. "at_session_myAgent").
 * @returns A stable session identifier string.
 */
export function getSessionId(key: string): string {
  try {
    const stored = sessionStorage.getItem(key);
    if (stored) return stored;
  } catch {
    /* sessionStorage unavailable — fall through */
  }

  const id =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : 'sess-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);

  try {
    sessionStorage.setItem(key, id);
  } catch {
    /* quota exceeded or private browsing — use in-memory */
  }

  return id;
}

/**
 * Loads persisted chat messages from sessionStorage.
 *
 * @param key - The sessionStorage key (e.g. "at_messages_myAgent").
 * @returns An array of StoredMessage (empty if nothing stored or on error).
 */
export function loadStoredMessages(key: string): StoredMessage[] {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as StoredMessage[]) : [];
  } catch {
    return [];
  }
}

/**
 * Persists chat messages to sessionStorage.
 *
 * @param key  - The sessionStorage key.
 * @param msgs - The messages to save.
 */
export function saveStoredMessages(key: string, msgs: StoredMessage[]): void {
  try {
    sessionStorage.setItem(key, JSON.stringify(msgs));
  } catch {
    /* quota exceeded or private browsing — silently ignore */
  }
}

/* ─── Session-Aware Storage ─── */

/** Extended message type with session metadata. */
interface StoredMessageWithMeta extends StoredMessage {
  sessionId?: string;
  ts?: number;
}

/**
 * Creates session-aware storage functions for a given agent/session.
 *
 * @param storageKey - The sessionStorage key for messages.
 * @param sessionId  - The current session ID to filter by.
 * @returns Object with readStored and appendStored functions.
 */
export function createStorage(storageKey: string, sessionId: string) {
  /**
   * Read stored messages for the current session only.
   */
  function readStored(): Array<{
    kind: string;
    text: string;
    tools: string[];
  }> {
    const stored = loadStoredMessages(storageKey);
    return stored
      .filter((m) => (m as StoredMessageWithMeta).sessionId === sessionId)
      .map((m) => ({
        kind: m.kind,
        text: m.text,
        tools: m.tools || [],
      }));
  }

  /**
   * Append a message to storage for the current session.
   */
  function appendStored(
    kind: 'user' | 'assistant',
    text: string,
    tools?: string[],
  ): void {
    const stored = loadStoredMessages(storageKey) as StoredMessageWithMeta[];
    stored.push({
      sessionId,
      kind,
      text: String(text || ''),
      tools: tools || [],
      ts: Date.now(),
    });

    // Prune if too many messages
    const filtered = stored.filter((m) => m.sessionId === sessionId);
    if (filtered.length > 100) {
      const extra = filtered.length - 100;
      let removed = 0;
      const pruned = stored.filter((m) => {
        if (m.sessionId === sessionId && removed < extra) {
          removed++;
          return false;
        }
        return true;
      });
      saveStoredMessages(storageKey, pruned);
      return;
    }

    saveStoredMessages(storageKey, stored);
  }

  return { readStored, appendStored };
}

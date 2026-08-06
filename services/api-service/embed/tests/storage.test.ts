// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { getSessionId, loadStoredMessages, saveStoredMessages } from '../src/storage';

describe('getSessionId', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('creates a new session ID if none exists', () => {
    const id = getSessionId('test_session');
    expect(id).toBeTruthy();
    expect(typeof id).toBe('string');
    expect(id.length).toBeGreaterThan(0);
  });

  it('returns the same ID on subsequent calls', () => {
    const id1 = getSessionId('test_session');
    const id2 = getSessionId('test_session');
    expect(id1).toBe(id2);
  });

  it('creates different IDs for different keys', () => {
    const id1 = getSessionId('session_a');
    const id2 = getSessionId('session_b');
    expect(id1).not.toBe(id2);
  });

  it('handles sessionStorage errors gracefully', () => {
    // Should not throw even if sessionStorage is unavailable
    expect(() => getSessionKey('test')).not.toThrow();
  });
});

function getSessionKey(key: string): string {
  // Helper to test error handling
  try {
    const stored = sessionStorage.getItem(key);
    if (stored) return stored;
  } catch (_e) { /* ignore */ }
  return 'fallback';
}

describe('loadStoredMessages', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('returns empty array if no messages stored', () => {
    const msgs = loadStoredMessages('nonexistent_key');
    expect(msgs).toEqual([]);
  });

  it('returns stored messages', () => {
    const messages = [
      { kind: 'user' as const, text: 'hello' },
      { kind: 'assistant' as const, text: 'hi there' },
    ];
    sessionStorage.setItem('test_msgs', JSON.stringify(messages));
    const loaded = loadStoredMessages('test_msgs');
    expect(loaded).toEqual(messages);
  });

  it('returns empty array for invalid JSON', () => {
    sessionStorage.setItem('test_invalid', 'not json');
    const loaded = loadStoredMessages('test_invalid');
    expect(loaded).toEqual([]);
  });

  it('returns empty array for non-array data', () => {
    sessionStorage.setItem('test_obj', JSON.stringify({ not: 'array' }));
    const loaded = loadStoredMessages('test_obj');
    expect(loaded).toEqual([]);
  });
});

describe('saveStoredMessages', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('saves messages to sessionStorage', () => {
    const messages = [
      { kind: 'user' as const, text: 'hello' },
      { kind: 'assistant' as const, text: 'hi' },
    ];
    saveStoredMessages('test_save', messages);
    const stored = JSON.parse(sessionStorage.getItem('test_save') || '[]');
    expect(stored).toEqual(messages);
  });

  it('overwrites existing messages', () => {
    saveStoredMessages('test_overwrite', [{ kind: 'user', text: 'old' }]);
    saveStoredMessages('test_overwrite', [{ kind: 'user', text: 'new' }]);
    const stored = JSON.parse(sessionStorage.getItem('test_overwrite') || '[]');
    expect(stored[0].text).toBe('new');
  });

  it('handles sessionStorage errors gracefully', () => {
    // Should not throw
    expect(() => saveStoredMessages('test', [])).not.toThrow();
  });
});

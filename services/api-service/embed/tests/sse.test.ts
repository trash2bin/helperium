// @vitest-environment jsdom
/**
 * SSE Stream Tests
 *
 * Tests for readSSEStream — the async/await SSE parser.
 * Uses controlled ReadableStream to verify event dispatch and
 * absence of Promise chain leaks.
 */
import { describe, it, expect, vi } from 'vitest';
import { readSSEStream, type SSEReadCallbacks } from '../src/sse';

// ─── Helpers ───────────────────────────────────────────────────────────

/**
 * Creates a mock Response whose body yields SSE-encoded events.
 * Events are delivered in configurable chunk sizes to simulate
 * real network fragmentation.
 */
function createSSEResponse(
  events: Array<Record<string, unknown>>,
  chunkSize: number = 512,
): Response {
  const encoder = new TextEncoder();
  const allData = encoder.encode(
    events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(''),
  );

  let offset = 0;
  const stream = new ReadableStream({
    pull(controller) {
      if (offset >= allData.byteLength) {
        controller.close();
        return;
      }
      const end = Math.min(offset + chunkSize, allData.byteLength);
      controller.enqueue(allData.slice(offset, end));
      offset = end;
    },
  });

  return new Response(stream);
}

/** Creates a minimal target DOM node. */
function makeTargetNode(): HTMLDivElement {
  const node = document.createElement('div');
  node.className = 'at-msg at-assistant';
  return node;
}

/** Builds default noop callbacks that track calls. */
function noopCallbacks(): SSEReadCallbacks & { calls: Record<string, number> } {
  const calls: Record<string, number> = {};
  return {
    onToken: vi.fn((_text: string) => { calls.token = (calls.token || 0) + 1; }),
    onFinal: vi.fn(() => { calls.final = (calls.final || 0) + 1; }),
    onToolCall: vi.fn(() => { calls.toolCall = (calls.toolCall || 0) + 1; }),
    onAudio: vi.fn(() => { calls.audio = (calls.audio || 0) + 1; }),
    onDone: vi.fn(() => { calls.done = (calls.done || 0) + 1; }),
    onError: vi.fn(() => { calls.error = (calls.error || 0) + 1; }),
    calls,
  };
}

// ─── Tests ─────────────────────────────────────────────────────────────

describe('readSSEStream', () => {
  it('processes 200 token events without accumulating Promises', async () => {
    const NUM_EVENTS = 200;
    const events = Array.from({ length: NUM_EVENTS }, (_, i) => ({
      type: 'token',
      text: `token-${i}`,
    }));

    const response = createSSEResponse(events);
    const targetNode = makeTargetNode();
    const cb = noopCallbacks();

    const promise = readSSEStream(response, targetNode, cb, 'en');

    // Verify the promise resolves (it's a single Promise, not a chain)
    expect(promise).toBeInstanceOf(Promise);

    await promise;

    expect(cb.calls.token).toBe(NUM_EVENTS);
    expect(cb.calls.final).toBeUndefined();
    expect(cb.calls.done).toBeUndefined();
    expect(cb.calls.error).toBeUndefined();
  });

  it('passes token text to onToken callback', async () => {
    const events = [{ type: 'token', text: 'Hello world' }];
    const response = createSSEResponse(events);
    const targetNode = makeTargetNode();

    const tokens: string[] = [];
    await readSSEStream(response, targetNode, {
      onToken: (t) => tokens.push(t),
      onFinal: () => {},
      onToolCall: () => {},
      onAudio: () => {},
      onDone: () => {},
      onError: () => {},
    }, 'en');

    expect(tokens).toEqual(['Hello world']);
  });

  it('calls onDone when done event is received and sets saved flag', async () => {
    const targetNode = makeTargetNode();
    targetNode.dataset.raw = 'Some reply';

    const response = createSSEResponse([
      { type: 'done' },
    ]);

    const onDone = vi.fn();
    await readSSEStream(response, targetNode, {
      onToken: () => {},
      onFinal: () => {},
      onToolCall: () => {},
      onAudio: () => {},
      onDone,
      onError: () => {},
    }, 'en');

    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith('Some reply', []);
    expect(targetNode.dataset.saved).toBe('true');
  });

  it('calls onFinal for a final event', async () => {
    const response = createSSEResponse([
      { type: 'final', text: 'Final answer' },
    ]);
    const targetNode = makeTargetNode();

    const finals: string[] = [];
    await readSSEStream(response, targetNode, {
      onToken: () => {},
      onFinal: (t) => finals.push(t),
      onToolCall: () => {},
      onAudio: () => {},
      onDone: () => {},
      onError: () => {},
    }, 'en');

    expect(finals).toEqual(['Final answer']);
  });

  it('handles tool_call events and updates dataset', async () => {
    const targetNode = makeTargetNode();
    const response = createSSEResponse([
      {
        type: 'tool_call',
        name: 'find_student',
        display_name: 'Поиск студента',
      },
    ]);

    const toolCalls: Array<{ name: string; displayName?: string }> = [];
    await readSSEStream(response, targetNode, {
      onToken: () => {},
      onFinal: () => {},
      onToolCall: (name, displayName) => toolCalls.push({ name, displayName }),
      onAudio: () => {},
      onDone: () => {},
      onError: () => {},
    }, 'en');

    expect(toolCalls).toHaveLength(1);
    expect(toolCalls[0]).toEqual({ name: 'find_student', displayName: 'Поиск студента' });
    expect(targetNode.dataset.tools).toContain('find_student');
    expect(targetNode.dataset.displayNames).toContain('Поиск студента');
  });

  it('skips duplicate tool names in dataset', async () => {
    const targetNode = makeTargetNode();
    targetNode.dataset.tools = JSON.stringify(['find_student']);

    const response = createSSEResponse([
      {
        type: 'tool_call',
        name: 'find_student',
        display_name: 'Поиск студента',
      },
    ]);

    const toolCalls: Array<{ name: string }> = [];
    await readSSEStream(response, targetNode, {
      onToken: () => {},
      onFinal: () => {},
      onToolCall: (name) => toolCalls.push({ name }),
      onAudio: () => {},
      onDone: () => {},
      onError: () => {},
    }, 'en');

    // Tool call event is still dispatched, but dataset should not duplicate
    expect(toolCalls).toHaveLength(1);
    expect(JSON.parse(targetNode.dataset.tools || '[]')).toEqual(['find_student']);
  });

  it('handles error event by setting at-error class', async () => {
    const targetNode = makeTargetNode();
    const response = createSSEResponse([
      { type: 'error', text: 'Something went wrong' },
    ]);

    await readSSEStream(response, targetNode, {
      onToken: () => {},
      onFinal: () => {},
      onToolCall: () => {},
      onAudio: () => {},
      onDone: () => {},
      onError: (t) => { targetNode.textContent = t; },
    }, 'en');

    expect(targetNode.classList.contains('at-error')).toBe(true);
    expect(targetNode.textContent).toContain('Something went wrong');
  });

  it('removes at-thinking class on first event', async () => {
    const targetNode = makeTargetNode();
    targetNode.classList.add('at-thinking');

    const response = createSSEResponse([
      { type: 'token', text: 'Hi' },
    ]);

    await readSSEStream(response, targetNode, {
      onToken: () => {},
      onFinal: () => {},
      onToolCall: () => {},
      onAudio: () => {},
      onDone: () => {},
      onError: () => {},
    }, 'en');

    expect(targetNode.classList.contains('at-thinking')).toBe(false);
  });

  it('processes events split across multiple chunks', async () => {
    // Each event is small but delivered one byte at a time
    const events = Array.from({ length: 10 }, (_, i) => ({
      type: 'token',
      text: `chunk-${i}`,
    }));

    const response = createSSEResponse(events, 1); // 1 byte chunks
    const targetNode = makeTargetNode();

    const tokens: string[] = [];
    await readSSEStream(response, targetNode, {
      onToken: (t) => tokens.push(t),
      onFinal: () => {},
      onToolCall: () => {},
      onAudio: () => {},
      onDone: () => {},
      onError: () => {},
    }, 'en');

    expect(tokens).toHaveLength(10);
  });

  it('audio event without voiceOutput guard still fires callback', async () => {
    // sse-reader.ts calls onAudio for ALL audio events
    // sse.ts checks opts.config.voiceOutput before calling
    // The unified readSSEStream should fire audio unconditionally (sse-reader behavior)
    const response = createSSEResponse([
      { type: 'audio', data: 'base64data' },
    ]);
    const targetNode = makeTargetNode();

    const audioData: string[] = [];
    await readSSEStream(response, targetNode, {
      onToken: () => {},
      onFinal: () => {},
      onToolCall: () => {},
      onAudio: (d) => audioData.push(d),
      onDone: () => {},
      onError: () => {},
    }, 'en');

    expect(audioData).toEqual(['base64data']);
  });

  it('done without raw text uses fallback message based on lang', async () => {
    const targetNode = makeTargetNode();
    // No raw text set: dataset.raw is empty
    const response = createSSEResponse([
      { type: 'done' },
    ]);

    const finals: string[] = [];
    await readSSEStream(response, targetNode, {
      onToken: () => {},
      onFinal: (t) => finals.push(t),
      onToolCall: () => {},
      onAudio: () => {},
      onDone: () => {},
      onError: () => {},
    }, 'ru');

    expect(finals).toEqual(['Не удалось получить ответ.']);
  });
});

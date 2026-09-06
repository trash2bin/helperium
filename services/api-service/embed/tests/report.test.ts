// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  attachReportButton,
  buildReportPayload,
  collectReportContext,
  isReported,
  markReported,
  type ReportDeps,
  reportedKey,
} from '../src/report';
import type { WidgetConfig } from '../src/types';

function makeConfig(lang: 'ru' | 'en' = 'ru'): WidgetConfig {
  return {
    agent: 'autoparts-assistant',
    apiBase: 'http://api.test',
    title: 'Assistant',
    greeting: 'Hello',
    accent: '#0f766e',
    position: 'right',
    lang,
    placeholder: 'Type…',
    width: '380px',
    height: '600px',
    triggerOffsetBottom: '20px',
    headerColor: '',
    showHeader: true,
    botBubbleColor: '',
    botBubbleText: '',
    voiceInput: false,
    voiceOutput: false,
    voiceToggle: 'classic',
  };
}

function makeDeps(lang: 'ru' | 'en' = 'ru'): ReportDeps {
  return {
    config: makeConfig(lang),
    getSessionId: () => 'session-1',
    getTranscript: () => [
      { kind: 'user', text: 'Сколько стоит EXT-01392?', tools: [], ts: 1700000000000 },
      { kind: 'assistant', text: 'Датчик ABS стоит 1546.00', tools: ['db_search'], ts: 1700000001000 },
    ],
  };
}

/** Builds a mounted assistant row the way messages.ts does. */
function makeRow(
  text: string,
  messages: HTMLDivElement = document.createElement('div'),
): { row: HTMLDivElement; node: HTMLDivElement; messages: HTMLDivElement } {
  messages.className = 'at-messages';
  const row = document.createElement('div');
  row.className = 'at-msg-row';
  const node = document.createElement('div');
  node.className = 'at-msg at-assistant';
  node.dataset.raw = text;
  node.textContent = text;
  row.appendChild(node);
  messages.appendChild(row);
  document.body.appendChild(messages);
  return { row, node, messages };
}

describe('report state persistence', () => {
  it('reportedKey is stable for the same content', () => {
    expect(reportedKey('assistant', 'answer')).toBe(reportedKey('assistant', 'answer'));
    expect(reportedKey('assistant', 'answer')).not.toBe(reportedKey('assistant', 'other'));
  });

  it('markReported → isReported round trip per agent', () => {
    const config = makeConfig();
    const key = reportedKey('assistant', 'answer');
    expect(isReported(config, key)).toBe(false);
    markReported(config, key);
    expect(isReported(config, key)).toBe(true);
    expect(isReported(makeConfig('en'), key)).toBe(true); // lang is not part of the key
  });

  it('caps the reported list', () => {
    const config = makeConfig();
    for (let i = 0; i < 60; i++) markReported(config, 'k' + i);
    expect(isReported(config, 'k0')).toBe(false);
    expect(isReported(config, 'k59')).toBe(true);
  });
});

describe('collectReportContext', () => {
  it('reads dataset.raw for assistant bubbles', () => {
    const node = document.createElement('div');
    node.className = 'at-msg at-assistant';
    node.dataset.raw = 'Ответ';
    node.dataset.tools = JSON.stringify(['db_search']);
    node.dataset.displayNames = JSON.stringify({ db_search: 'Поиск по базе' });

    const ctx = collectReportContext(node);
    expect(ctx.kind).toBe('assistant');
    expect(ctx.text).toBe('Ответ');
    expect(ctx.tools).toEqual(['db_search']);
    expect(ctx.displayNames).toEqual(['Поиск по базе']);
    expect(ctx.correlationId).toBe('');
  });

  it('reads textContent for error bubbles and tolerates broken dataset JSON', () => {
    const node = document.createElement('div');
    node.className = 'at-msg at-assistant at-error';
    node.textContent = 'Не удалось получить ответ.';
    node.dataset.tools = 'not-json';
    node.dataset.correlationId = 'corr-1';

    const ctx = collectReportContext(node);
    expect(ctx.kind).toBe('error');
    expect(ctx.text).toBe('Не удалось получить ответ.');
    expect(ctx.tools).toEqual([]);
    expect(ctx.correlationId).toBe('corr-1');
  });
});

describe('buildReportPayload', () => {
  it('includes agent, session, message context and capped transcript', () => {
    const deps = makeDeps();
    const ctx = collectReportContext(Object.assign(document.createElement('div'), {}));
    ctx.kind = 'assistant';
    ctx.text = 'Датчик ABS стоит 1546.00';
    ctx.tools = ['db_search'];

    const payload = buildReportPayload(deps, ctx, '  цена не та  ') as Record<string, unknown>;

    expect(payload.agent).toBe('autoparts-assistant');
    expect(payload.session_id).toBe('session-1');
    expect(payload.lang).toBe('ru');
    expect(payload.message).toEqual({
      kind: 'assistant',
      text: 'Датчик ABS стоит 1546.00',
      tools: ['db_search'],
      display_names: [],
    });
    const transcript = payload.transcript as Array<Record<string, unknown>>;
    expect(transcript).toHaveLength(2);
    expect(transcript[0].ts).toBe(new Date(1700000000000).toISOString());
    expect(payload.comment).toBe('цена не та');
    expect(payload.last_error).toBeUndefined();
  });

  it('includes last_error for error bubbles with correlation id', () => {
    const deps = makeDeps();
    const ctx = collectReportContext(document.createElement('div'));
    ctx.kind = 'error';
    ctx.text = 'Не удалось получить ответ.';
    ctx.correlationId = 'corr-9';

    const payload = buildReportPayload(deps, ctx, '') as Record<string, unknown>;

    expect(payload.last_error).toEqual({
      text: 'Не удалось получить ответ.',
      correlation_id: 'corr-9',
    });
    expect(payload.comment).toBeUndefined();
  });
});

describe('attachReportButton', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = '';
    sessionStorage.clear();
  });

  it('creates an aria-labelled flag inside a bubble line', () => {
    const deps = makeDeps();
    const { row, node } = makeRow('Ответ');

    attachReportButton(deps, node, row);

    const btn = row.querySelector('button.at-report-btn') as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.getAttribute('aria-label')).toBe('Пожаловаться на этот ответ');
    expect(node.closest('.at-bubble-line')).not.toBeNull();
    expect(btn.closest('.at-bubble-line')).toBe(node.closest('.at-bubble-line'));
    // Flag is a sibling of the bubble, not a child — error text overwrites stay safe.
    expect(node.contains(btn)).toBe(false);
  });

  it('uses the English label for lang=en', () => {
    const deps = makeDeps('en');
    const { row, node } = makeRow('Answer');

    attachReportButton(deps, node, row);

    const btn = row.querySelector('button.at-report-btn') as HTMLButtonElement;
    expect(btn.getAttribute('aria-label')).toBe('Report this answer');
  });

  it('submit posts the payload, dims the flag and survives re-attach', async () => {
    const deps = makeDeps();
    const { row, node, messages } = makeRow('Ответ 1');
    attachReportButton(deps, node, row);
    const btn = row.querySelector('button.at-report-btn') as HTMLButtonElement;

    fetchMock.mockResolvedValue(new Response(JSON.stringify({ status: 'accepted', id: 'r1' }), { status: 201 }));

    btn.click();
    const form = messages.querySelector('.at-report-form') as HTMLElement;
    const input = form.querySelector('.at-report-input') as HTMLTextAreaElement;
    input.value = 'цена не та';
    (form.querySelector('.at-report-submit') as HTMLButtonElement).click();

    await vi.waitFor(() => {
      expect(btn.classList.contains('at-reported')).toBe(true);
    });
    expect(btn.disabled).toBe(true);
    expect(document.querySelector('.at-report-form')).toBeNull();
    expect(document.querySelector('.at-report-done')).not.toBeNull();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/reports');
    expect(init.method).toBe('POST');
    const payload = JSON.parse(init.body);
    expect(payload.message.text).toBe('Ответ 1');
    expect(payload.comment).toBe('цена не та');
    expect(payload.session_id).toBe('session-1');

    // Reported state survives re-attach (restore path).
    const restored = makeRow('Ответ 1');
    attachReportButton(deps, restored.node, restored.row);
    const restoredBtn = restored.row.querySelector('button.at-report-btn') as HTMLButtonElement;
    expect(restoredBtn.classList.contains('at-reported')).toBe(true);
    expect(restoredBtn.disabled).toBe(true);
  });

  it('a fresh live answer is not dimmed by an earlier reported live answer', async () => {
    // Regression: live rows attach in the thinking state with empty raw text,
    // so a stale attach-time key made every new live answer share one
    // "reported" mark and go dim right after any earlier report.
    const deps = makeDeps();
    const live = (text: string) => {
      const messages = document.createElement('div');
      messages.className = 'at-messages';
      const row = document.createElement('div');
      row.className = 'at-msg-row';
      const node = document.createElement('div');
      node.className = 'at-msg at-assistant at-thinking';
      node.dataset.raw = '';
      row.appendChild(node);
      messages.appendChild(row);
      document.body.appendChild(messages);
      attachReportButton(deps, node, row);
      // Stream finalizes: the bubble gets its final raw text.
      node.classList.remove('at-thinking');
      node.dataset.raw = text;
      return { row, node };
    };

    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ status: 'accepted', id: 'r1' }), { status: 201 }),
    );

    const first = live('Первый ответ');
    const firstBtn = first.row.querySelector('button.at-report-btn') as HTMLButtonElement;
    firstBtn.click();
    const form = document.querySelector('.at-report-form') as HTMLElement;
    (form.querySelector('.at-report-submit') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(firstBtn.classList.contains('at-reported')).toBe(true));

    const second = live('Второй ответ');
    const secondBtn = second.row.querySelector('button.at-report-btn') as HTMLButtonElement;
    expect(secondBtn.classList.contains('at-reported')).toBe(false);
    expect(secondBtn.disabled).toBe(false);
  });

  it('recalculates the reported state when a live answer finalizes as an error', async () => {
    const deps = makeDeps();
    markReported(deps.config, reportedKey('error', 'Сервис недоступен.'));

    const messages = document.createElement('div');
    messages.className = 'at-messages';
    const row = document.createElement('div');
    row.className = 'at-msg-row';
    const node = document.createElement('div');
    node.className = 'at-msg at-assistant at-thinking';
    node.dataset.raw = '';
    row.appendChild(node);
    messages.appendChild(row);
    document.body.appendChild(messages);
    attachReportButton(deps, node, row);
    const btn = row.querySelector('button.at-report-btn') as HTMLButtonElement;
    expect(btn.disabled).toBe(false);

    // SSE error handler finalizes the bubble as an error bubble.
    node.classList.remove('at-thinking');
    node.classList.add('at-error');
    node.textContent = 'Сервис недоступен.';
    await vi.waitFor(() => expect(btn.disabled).toBe(true));
  });

  it('opening the form on another message closes the previous form', () => {
    const deps = makeDeps();
    const shared = document.createElement('div');
    const first = makeRow('Ответ 1', shared);
    attachReportButton(deps, first.node, first.row);
    const second = makeRow('Ответ 2', shared);
    attachReportButton(deps, second.node, second.row);

    (first.row.querySelector('button.at-report-btn') as HTMLButtonElement).click();
    expect(document.querySelectorAll('.at-report-form').length).toBe(1);

    (second.row.querySelector('button.at-report-btn') as HTMLButtonElement).click();
    expect(document.querySelectorAll('.at-report-form').length).toBe(1);
  });

  it('keeps the form open with an error note when the endpoint fails', async () => {
    const deps = makeDeps();
    const { row, node, messages } = makeRow('Ответ');
    attachReportButton(deps, node, row);
    const btn = row.querySelector('button.at-report-btn') as HTMLButtonElement;

    fetchMock.mockResolvedValue(new Response('', { status: 500 }));

    btn.click();
    (messages.querySelector('.at-report-submit') as HTMLButtonElement).click();

    await vi.waitFor(() => {
      expect(document.querySelector('.at-report-error')).not.toBeNull();
    });
    expect(messages.querySelector('.at-report-form')).not.toBeNull();
    expect(btn.classList.contains('at-reported')).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('cancel removes the form without sending', () => {
    const deps = makeDeps();
    const { row, node, messages } = makeRow('Ответ');
    attachReportButton(deps, node, row);

    (row.querySelector('button.at-report-btn') as HTMLButtonElement).click();
    (messages.querySelector('.at-report-cancel') as HTMLButtonElement).click();

    expect(messages.querySelector('.at-report-form')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

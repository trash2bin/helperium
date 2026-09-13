// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { parseConfig } from '../src/config';

// Regression (2026-09-12, live demo): the autoparts storefront embeds the
// widget with data-title/data-accent only — no data-greeting, no
// data-placeholder. On a Russian page the chrome (Open/Close chat, mic,
// send) correctly switches via config.lang, but the greeting bubble and the
// input placeholder fell back to hardcoded English ('How can I help?',
// 'Ask a question...') because greeting/placeholder were the only strings
// without a language branch.

function createScript(attrs: Record<string, string>): HTMLScriptElement {
  const script = document.createElement('script');
  for (const [key, value] of Object.entries(attrs)) {
    script.setAttribute(key, value);
  }
  return script;
}

describe('parseConfig — language-aware greeting/placeholder defaults', () => {
  it('data-lang=ru with no explicit strings → Russian greeting and placeholder', () => {
    const config = parseConfig(
      createScript({ 'data-agent': 'test', 'data-lang': 'ru' }),
    );

    expect(config.greeting).toBe('Чем могу помочь?');
    expect(config.placeholder).toBe('Задайте вопрос…');
  });

  it('ru browser locale with no explicit strings → Russian greeting and placeholder', () => {
    const original = navigator.language;
    Object.defineProperty(navigator, 'language', { value: 'ru-RU', configurable: true });
    try {
      const config = parseConfig(createScript({ 'data-agent': 'test' }));

      expect(config.lang).toBe('ru');
      expect(config.greeting).toBe('Чем могу помочь?');
      expect(config.placeholder).toBe('Задайте вопрос…');
    } finally {
      Object.defineProperty(navigator, 'language', { value: original, configurable: true });
    }
  });

  it('explicit data-greeting/data-placeholder still win over language defaults', () => {
    const config = parseConfig(
      createScript({
        'data-agent': 'test',
        'data-lang': 'ru',
        'data-greeting': 'Привет!',
        'data-placeholder': 'Напишите артикул…',
      }),
    );

    expect(config.greeting).toBe('Привет!');
    expect(config.placeholder).toBe('Напишите артикул…');
  });

  it('English defaults stay English when lang resolves to en', () => {
    const original = navigator.language;
    Object.defineProperty(navigator, 'language', { value: 'en-US', configurable: true });
    try {
      const config = parseConfig(createScript({ 'data-agent': 'test' }));

      expect(config.lang).toBe('en');
      expect(config.greeting).toBe('How can I help?');
      expect(config.placeholder).toBe('Ask a question...');
    } finally {
      Object.defineProperty(navigator, 'language', { value: original, configurable: true });
    }
  });
});

// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { parseConfig } from '../src/config';

// Mock document.currentScript
function createScript(attrs: Record<string, string>): HTMLScriptElement {
  const script = document.createElement('script');
  for (const [key, value] of Object.entries(attrs)) {
    script.setAttribute(key, value);
  }
  return script;
}

describe('parseConfig', () => {
  it('parses basic attributes', () => {
    const script = createScript({
      'data-agent': 'test-agent',
      'data-api-base': 'https://api.example.com',
      'data-title': 'Test Bot',
      'data-greeting': 'Hello!',
    });
    const config = parseConfig(script);

    expect(config.agent).toBe('test-agent');
    expect(config.apiBase).toBe('https://api.example.com');
    expect(config.title).toBe('Test Bot');
    expect(config.greeting).toBe('Hello!');
  });

  it('uses defaults for missing attributes', () => {
    const script = createScript({ 'data-agent': 'test' });
    const config = parseConfig(script);

    expect(config.apiBase).toBe(window.location.origin);
    expect(config.position).toBe('right');
    expect(config.accent).toBe('#0f766e');
    expect(config.showHeader).toBe(true);
    expect(config.voiceInput).toBe(true);
    expect(config.voiceOutput).toBe(true);
  });

  it('parses position left', () => {
    const script = createScript({
      'data-agent': 'test',
      'data-position': 'left',
    });
    const config = parseConfig(script);
    expect(config.position).toBe('left');
  });

  it('defaults position to right for invalid values', () => {
    const script = createScript({
      'data-agent': 'test',
      'data-position': 'center',
    });
    const config = parseConfig(script);
    expect(config.position).toBe('right');
  });

  it('parses boolean false values', () => {
    const script = createScript({
      'data-agent': 'test',
      'data-show-header': 'false',
      'data-voice-input': 'false',
      'data-voice-output': 'false',
    });
    const config = parseConfig(script);

    expect(config.showHeader).toBe(false);
    expect(config.voiceInput).toBe(false);
    expect(config.voiceOutput).toBe(false);
  });

  it('auto-detects Russian language', () => {
    const original = navigator.language;
    Object.defineProperty(navigator, 'language', { value: 'ru-RU', configurable: true });

    const script = createScript({ 'data-agent': 'test' });
    const config = parseConfig(script);
    expect(config.lang).toBe('ru');

    Object.defineProperty(navigator, 'language', { value: original, configurable: true });
  });

  it('defaults to English for non-Russian', () => {
    const original = navigator.language;
    Object.defineProperty(navigator, 'language', { value: 'en-US', configurable: true });

    const script = createScript({ 'data-agent': 'test' });
    const config = parseConfig(script);
    expect(config.lang).toBe('en');

    Object.defineProperty(navigator, 'language', { value: original, configurable: true });
  });

  it('returns empty agent if not specified', () => {
    const script = createScript({});
    const config = parseConfig(script);
    expect(config.agent).toBe('');
  });
});

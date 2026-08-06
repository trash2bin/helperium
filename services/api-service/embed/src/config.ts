/**
 * Helperium Embed Widget — Configuration Parser
 *
 * Parses data-* attributes from the <script> tag into a typed WidgetConfig.
 */

import type { WidgetConfig } from './types';

/**
 * Reads the <script> tag that loaded this widget and parses its
 * data-* attributes into a fully-typed config object.
 *
 * Falls back to sensible defaults when attributes are missing.
 *
 * @param script - The <script> element that loaded the widget.
 * @returns A readonly WidgetConfig.
 */
export function parseConfig(script: HTMLScriptElement | null): WidgetConfig {
  const attr = (name: string): string =>
    script?.getAttribute(name) ?? '';

  // Support window.__EMBED_CONFIG or window.EMBED_CONFIG fallback
  const g = window as unknown as Record<string, unknown>;
  const wc = (g.__EMBED_CONFIG ?? g.EMBED_CONFIG) as
    | Record<string, string>
    | undefined;
  const fromWindow = (key: string, dataAttr: string): string =>
    attr(dataAttr) || (wc ? String(wc[key] ?? '') : '');

  const agent = fromWindow('agent', 'data-agent');
  if (!agent) {
    console.error('[Helperium Widget] Missing data-agent attribute');
  }

  const rawLang = fromWindow('lang', 'data-lang');
  const detectedLang = navigator.language.startsWith('ru') ? 'ru' : 'en';

  return Object.freeze({
    agent,
    apiBase: fromWindow('apiBase', 'data-api-base') || window.location.origin,
    title: fromWindow('title', 'data-title') || 'Assistant',
    greeting: fromWindow('greeting', 'data-greeting') || 'How can I help?',
    accent: fromWindow('accent', 'data-accent') || '#0f766e',
    position: fromWindow('position', 'data-position') === 'left' ? 'left' : 'right',
    lang: (rawLang === 'ru' || rawLang === 'en') ? rawLang : detectedLang,
    placeholder: fromWindow('placeholder', 'data-placeholder') || 'Ask a question...',
    width: fromWindow('width', 'data-width') || 'min(380px, calc(100vw - 28px))',
    height: fromWindow('height', 'data-height') || 'min(620px, calc(100vh - 44px))',
    triggerOffsetBottom: fromWindow('triggerOffsetBottom', 'data-trigger-offset-bottom') || '16px',
    headerColor: fromWindow('headerColor', 'data-header-color'),
    showHeader: fromWindow('showHeader', 'data-show-header') !== 'false',
    botBubbleColor: fromWindow('botBubbleColor', 'data-bot-bubble-color') || '#eef3f4',
    botBubbleText: fromWindow('botBubbleText', 'data-bot-bubble-text') || 'var(--ink)',
    voiceInput: fromWindow('voiceInput', 'data-voice-input') !== 'false',
    voiceOutput: fromWindow('voiceOutput', 'data-voice-output') !== 'false',
    voiceToggle: fromWindow('voiceToggle', 'data-voice-toggle') === 'classic' ? 'classic' : 'telegram',
  });
}

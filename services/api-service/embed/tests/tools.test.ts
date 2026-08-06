// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { getToolIcon, makeToolStrip } from '../src/tools';

describe('getToolIcon', () => {
  it('returns search icon for find/search tools', () => {
    expect(getToolIcon('find_product')).toBe('🔍');
    expect(getToolIcon('Search users')).toBe('🔍');
    expect(getToolIcon('поиск студентов')).toBe('🔍');
    expect(getToolIcon('найти товар')).toBe('🔍');
  });

  it('returns list icon for list/get tools', () => {
    expect(getToolIcon('list_students')).toBe('📋');
    expect(getToolIcon('get_user')).toBe('📋');
    expect(getToolIcon('получение данных')).toBe('📋');
    expect(getToolIcon('список товаров')).toBe('📋');
  });

  it('returns chart icon for query tools', () => {
    expect(getToolIcon('run_query')).toBe('📊');
    expect(getToolIcon('запрос к базе')).toBe('📊');
  });

  it('returns default icon for unknown tools', () => {
    expect(getToolIcon('custom_tool')).toBe('⚡');
    expect(getToolIcon('')).toBe('⚡');
  });
});

describe('makeToolStrip', () => {
  it('returns null for empty array', () => {
    expect(makeToolStrip([])).toBeNull();
  });

  it('creates a strip with one tool', () => {
    const strip = makeToolStrip(['find_product']);
    expect(strip).not.toBeNull();
    expect(strip!.className).toBe('at-tool-strip');
    expect(strip!.children.length).toBe(1);
    expect(strip!.textContent).toContain('find_product');
  });

  it('creates a strip with multiple tools', () => {
    const strip = makeToolStrip(['find_product', 'get_user']);
    expect(strip!.children.length).toBe(2);
  });

  it('deduplicates tool names', () => {
    const strip = makeToolStrip(['find', 'find', 'get']);
    expect(strip!.children.length).toBe(2);
  });

  it('uses display names when provided', () => {
    const strip = makeToolStrip(['find_product'], { find_product: 'Найти товар' });
    expect(strip!.textContent).toContain('Найти товар');
  });

  it('escapes HTML in display names', () => {
    const strip = makeToolStrip(['tool'], { tool: '<script>alert("xss")</script>' });
    expect(strip!.innerHTML).not.toContain('<script>');
    expect(strip!.innerHTML).toContain('&lt;script&gt;');
  });
});

// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { findMsgNode, removeMsgRow, scrollToBottom, isScrolledNearBottom } from '../src/dom';

describe('findMsgNode', () => {
  it('returns the node itself if it has class at-msg', () => {
    const div = document.createElement('div');
    div.className = 'at-msg at-assistant';
    expect(findMsgNode(div)).toBe(div);
  });

  it('finds .at-msg inside .at-msg-row', () => {
    const row = document.createElement('div');
    row.className = 'at-msg-row';
    const bubble = document.createElement('div');
    bubble.className = 'at-msg at-assistant';
    row.appendChild(bubble);
    expect(findMsgNode(row)).toBe(bubble);
  });

  it('returns the node if it is not in a row and not a msg', () => {
    const div = document.createElement('div');
    div.className = 'something-else';
    expect(findMsgNode(div)).toBe(div);
  });
});

describe('removeMsgRow', () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement('div');
  });

  it('removes the parent row if target is inside one', () => {
    const row = document.createElement('div');
    row.className = 'at-msg-row';
    const bubble = document.createElement('div');
    bubble.className = 'at-msg at-assistant';
    row.appendChild(bubble);
    container.appendChild(row);

    removeMsgRow(bubble);
    expect(container.children.length).toBe(0);
  });

  it('removes the node itself if not in a row', () => {
    const div = document.createElement('div');
    container.appendChild(div);

    removeMsgRow(div);
    expect(container.children.length).toBe(0);
  });
});

describe('scrollToBottom', () => {
  it('sets scrollTop to scrollHeight', () => {
    const el = document.createElement('div');
    Object.defineProperty(el, 'scrollHeight', { value: 500, writable: true });
    el.scrollTop = 0;
    scrollToBottom(el);
    expect(el.scrollTop).toBe(500);
  });

  it('handles null gracefully', () => {
    expect(() => scrollToBottom(null)).not.toThrow();
  });
});

describe('isScrolledNearBottom', () => {
  it('returns true when near bottom (within 48px)', () => {
    const el = document.createElement('div');
    Object.defineProperty(el, 'scrollHeight', { value: 1000 });
    Object.defineProperty(el, 'clientHeight', { value: 400 });
    el.scrollTop = 560; // 1000 - 560 - 400 = 40 < 48
    expect(isScrolledNearBottom(el)).toBe(true);
  });

  it('returns false when far from bottom', () => {
    const el = document.createElement('div');
    Object.defineProperty(el, 'scrollHeight', { value: 1000 });
    Object.defineProperty(el, 'clientHeight', { value: 400 });
    el.scrollTop = 400; // 1000 - 400 - 400 = 200 > 48
    expect(isScrolledNearBottom(el)).toBe(false);
  });

  it('returns false for null', () => {
    expect(isScrolledNearBottom(null)).toBe(false);
  });
});

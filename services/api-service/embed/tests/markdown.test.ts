import { describe, it, expect } from 'vitest';
import { renderMarkdown } from '../src/markdown';

describe('renderMarkdown', () => {
  describe('inline formatting', () => {
    it('renders bold text', () => {
      expect(renderMarkdown('**bold**')).toContain('<strong>bold</strong>');
    });

    it('renders italic text', () => {
      expect(renderMarkdown('*italic*')).toContain('<em>italic</em>');
    });

    it('renders inline code', () => {
      expect(renderMarkdown('`code`')).toContain('<code>code</code>');
    });

    it('renders links', () => {
      const result = renderMarkdown('[link](http://example.com)');
      expect(result).toContain('<a href="http://example.com"');
      expect(result).toContain('link</a>');
    });

    it('converts line breaks to <br>', () => {
      const result = renderMarkdown('line1\nline2');
      expect(result).toContain('<br>');
    });
  });

  describe('block elements', () => {
    it('renders paragraphs', () => {
      const result = renderMarkdown('Hello world');
      expect(result).toContain('<p>');
      expect(result).toContain('Hello world');
    });

    it('renders unordered lists', () => {
      const result = renderMarkdown('- item1\n- item2');
      expect(result).toContain('<ul>');
      expect(result).toContain('<li>item1</li>');
      expect(result).toContain('<li>item2</li>');
    });

    it('renders ordered lists', () => {
      const result = renderMarkdown('1. first\n2. second');
      expect(result).toContain('<ol>');
      expect(result).toContain('<li>first</li>');
      expect(result).toContain('<li>second</li>');
    });

    it('renders tables', () => {
      const md = '| H1 | H2 |\n|---|---|\n| A | B |';
      const result = renderMarkdown(md);
      expect(result).toContain('<table>');
    });
  });

  describe('edge cases', () => {
    it('handles empty string', () => {
      expect(renderMarkdown('')).toBe('');
    });

    it('handles null/undefined', () => {
      expect(renderMarkdown(null as any)).toBe('');
      expect(renderMarkdown(undefined as any)).toBe('');
    });

    it('escapes HTML in markdown', () => {
      const result = renderMarkdown('<script>alert("xss")</script>');
      expect(result).not.toContain('<script>');
      expect(result).toContain('&lt;script&gt;');
    });

    it('handles multiple formatting in one line', () => {
      const result = renderMarkdown('**bold** and *italic*');
      expect(result).toContain('<strong>');
      expect(result).toContain('<em>');
    });
  });
});

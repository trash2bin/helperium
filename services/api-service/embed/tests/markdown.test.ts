import { describe, expect, it } from 'vitest';
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

  describe('link scheme safety', () => {
    it('blocks javascript: URLs (renders link text as plain text)', () => {
      const result = renderMarkdown('[click](javascript:alert(1))');
      expect(result).not.toContain('<a ');
      expect(result).not.toContain('javascript:');
      expect(result).toContain('click');
    });

    it('blocks data: URLs', () => {
      const result = renderMarkdown('[click](data:text/html;base64,PHNjcmlwdD4=)');
      expect(result).not.toContain('<a ');
      expect(result).not.toContain('data:');
      expect(result).toContain('click');
    });

    it('blocks vbscript: URLs', () => {
      const result = renderMarkdown('[click](vbscript:msgbox)');
      expect(result).not.toContain('<a ');
      expect(result).toContain('click');
    });

    it('blocks JAVASCRIPT: (case-insensitive scheme match)', () => {
      const result = renderMarkdown('[click](JAVASCRIPT:alert(1))');
      expect(result).not.toContain('<a ');
      expect(result).not.toContain('alert(1)');
      expect(result).toContain('click');
    });

    it('blocks java\\tscript: (tab inside scheme, browsers strip tabs from URLs)', () => {
      const result = renderMarkdown('[click](java\tscript:alert(1))');
      expect(result).not.toContain('<a ');
      expect(result).not.toContain('alert(1)');
      expect(result).toContain('click');
    });

    it('blocks java\\nscript: (newline inside scheme)', () => {
      const result = renderMarkdown('[click](java\nscript:alert(1))');
      expect(result).not.toContain('<a ');
      expect(result).not.toContain('alert(1)');
      expect(result).toContain('click');
    });

    it('blocks java script: (space inside scheme is not a valid scheme grammar)', () => {
      const result = renderMarkdown('[click](java script:alert(1))');
      expect(result).not.toContain('<a ');
      expect(result).toContain('click');
    });

    it('blocks scheme after leading whitespace', () => {
      const result = renderMarkdown('[click](   javascript:alert(1)   )');
      expect(result).not.toContain('<a ');
      expect(result).toContain('click');
    });

    it('blocks unknown schemes like tel:', () => {
      const result = renderMarkdown('[click](tel:+1234567890)');
      expect(result).not.toContain('<a ');
      expect(result).toContain('click');
    });

    it('still renders https links', () => {
      const result = renderMarkdown('[docs](https://example.com/docs?x=1)');
      expect(result).toContain('<a href="https://example.com/docs?x=1"');
      expect(result).toContain('docs</a>');
    });

    it('still renders http links', () => {
      const result = renderMarkdown('[link](http://example.com/x)');
      expect(result).toContain('<a href="http://example.com/x"');
    });

    it('still renders uppercase HTTPS links', () => {
      const result = renderMarkdown('[link](HTTPS://example.com)');
      expect(result).toContain('<a href="HTTPS://example.com"');
    });

    it('still renders mailto links', () => {
      const result = renderMarkdown('[mail](mailto:test@example.com)');
      expect(result).toContain('<a href="mailto:test@example.com"');
    });

    it('still renders root-relative links', () => {
      const result = renderMarkdown('[dash](/dashboard)');
      expect(result).toContain('<a href="/dashboard"');
    });

    it('still renders relative links', () => {
      const result = renderMarkdown('[doc](reports/2026/report.md)');
      expect(result).toContain('<a href="reports/2026/report.md"');
    });

    it('still renders fragment links', () => {
      const result = renderMarkdown('[jump](#section-2)');
      expect(result).toContain('<a href="#section-2"');
    });

    it('still renders query-only links', () => {
      const result = renderMarkdown('[search](?q=1)');
      expect(result).toContain('<a href="?q=1"');
    });

    it('still renders links where a colon appears only after a path separator', () => {
      const result = renderMarkdown('[file](reports/draft:v2/readme)');
      expect(result).toContain('<a href="reports/draft:v2/readme"');
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

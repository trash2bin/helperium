/**
 * Helperium Embed Widget — Lightweight Markdown Renderer
 *
 * Converts a subset of Markdown into safe HTML.
 * Supports: tables, unordered/ordered lists, paragraphs,
 * bold (**text**), italic (*text*), inline code (`code`),
 * links [text](url), and line breaks.
 */

import { escapeHtml } from './icons';

/**
 * Renders a markdown string to HTML.
 *
 * @param text - Raw markdown text.
 * @returns HTML string.
 */
export function renderMarkdown(text: string): string {
  const chunks: string[] = [];
  const lines = (text || '').split('\n');
  let i = 0;

  while (i < lines.length) {
    const current = lines[i]!;

    // Table
    if (isTableStart(lines, i)) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i]!.trim().charAt(0) === '|') {
        tableLines.push(lines[i]!);
        i++;
      }
      chunks.push(renderTable(tableLines));
      continue;
    }

    // Unordered list
    if (/^\s*[-*]\s+/.test(current)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i]!)) {
        items.push(lines[i]!.replace(/^\s*[-*]\s+/, ''));
        i++;
      }
      chunks.push(
        '<ul>' + items.map((item) => '<li>' + inlineMarkdown(item) + '</li>').join('') + '</ul>',
      );
      continue;
    }

    // Ordered list
    if (/^\s*\d+\.\s+/.test(current)) {
      const oitems: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i]!)) {
        oitems.push(lines[i]!.replace(/^\s*\d+\.\s+/, ''));
        i++;
      }
      chunks.push(
        '<ol>' + oitems.map((item) => '<li>' + inlineMarkdown(item) + '</li>').join('') + '</ol>',
      );
      continue;
    }

    // Paragraph (collects consecutive non-empty, non-special lines)
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i]!.trim() &&
      !isTableStart(lines, i) &&
      !/^\s*[-*]\s+/.test(lines[i]!) &&
      !/^\s*\d+\.\s+/.test(lines[i]!)
    ) {
      para.push(lines[i]!);
      i++;
    }
    if (para.length) {
      chunks.push('<p>' + inlineMarkdown(para.join('\n')).replace(/\n/g, '<br>') + '</p>');
    }

    // Empty line — skip
    if (i < lines.length && !lines[i]!.trim()) i++;
  }

  return chunks.join('');
}

/**
 * Checks whether lines[idx] and lines[idx+1] form a table header + separator.
 */
function isTableStart(lines: string[], idx: number): boolean {
  const line = lines[idx];
  const next = lines[idx + 1];
  if (!line || !next) return false;
  return (
    line.trim().charAt(0) === '|' &&
    /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(next)
  );
}

/**
 * Renders a list of raw table lines (header + body rows) into an HTML table.
 */
function renderTable(lines: string[]): string {
  const dataRows: string[][] = [];

  for (let j = 0; j < lines.length; j++) {
    const line = lines[j]!;
    // Skip separator rows (|---|---|)
    if (/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(line)) continue;
    const cells = line
      .trim()
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((c) => c.trim());
    dataRows.push(cells);
  }

  if (!dataRows.length) return '';

  const head = dataRows[0]!;
  const body = dataRows.slice(1);

  return (
    '<div class="at-table-wrap"><table><thead><tr>' +
    head.map((c) => '<th>' + inlineMarkdown(c) + '</th>').join('') +
    '</tr></thead><tbody>' +
    body
      .map(
        (row) =>
          '<tr>' + row.map((c) => '<td>' + inlineMarkdown(c) + '</td>').join('') + '</tr>',
      )
      .join('') +
    '</tbody></table></div>'
  );
}

/**
 * URL schemes that may become live links. Tool results and assistant text are
 * untrusted input, so every other scheme (javascript:, data:, vbscript:, ...) is
 * rejected: browsers ignore most whitespace inside a URL scheme, so matching
 * must run on the scheme with all whitespace removed.
 */
const SAFE_LINK_SCHEMES = new Set(['http', 'https', 'mailto']);

/**
 * Decides whether a markdown link target can be rendered as an href.
 *
 * A scheme is everything before the first ':' that contains no '/', '?' or '#'
 * (those always start the path/query/fragment part of a scheme-less URL, so
 * `reports/draft:v2/x` or `?q=1` are relative targets, not schemes). Whitespace
 * inside the candidate scheme is stripped before comparison because browsers
 * strip tabs and newlines from URLs (`java\tscript:` executes as `javascript:`).
 * A candidate that matches the URL scheme grammar must be explicitly allowed;
 * candidates outside the grammar are treated as unsafe anyway — the widget
 * renders untrusted tool output, so this check stays conservative.
 */
function isSafeLinkHref(href: string): boolean {
  const head = href.replace(/^\s+/, '').split(/[/?#]/, 1)[0] ?? '';
  if (!head.includes(':')) return true; // relative, /root-relative, #fragment, ?query
  const scheme = head.replace(/\s+/g, '').split(':', 1)[0]!.toLowerCase();
  return /^[a-z][a-z0-9+.-]*$/.test(scheme) && SAFE_LINK_SCHEMES.has(scheme);
}

/**
 * Processes inline markdown formatting within a single line / cell.
 *
 * Supports: **bold**, *italic*, `code`, and [text](url) links. Unsafe link
 * targets are rendered as plain text so the link label stays readable.
 */
function inlineMarkdown(val: string): string {
  return escapeHtml(val)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, label: string, href: string) => {
      if (!isSafeLinkHref(href)) return label;
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
}

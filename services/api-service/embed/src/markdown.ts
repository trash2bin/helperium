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
 * Processes inline markdown formatting within a single line / cell.
 *
 * Supports: **bold**, *italic*, `code`, and [text](url) links.
 */
function inlineMarkdown(val: string): string {
  return escapeHtml(val)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

/**
 * Helperium Embed Widget — Icons
 *
 * SVG icons stored as separate files for maintainability.
 * esbuild imports them as text strings via --loader:.svg=text.
 */

import type { IconSet } from './types';

// SVG icons (imported as text strings by esbuild)
import chatSvg from './icons/chat.svg';
import closeSvg from './icons/close.svg';
import sendSvg from './icons/send.svg';
import micSvg from './icons/mic.svg';
import micOffSvg from './icons/mic-off.svg';

/** All widget SVG icons */
export const ICONS: IconSet = {
  chat: chatSvg,
  close: closeSvg,
  send: sendSvg,
  mic: micSvg,
  micOff: micOffSvg,
  thinking:
    '<div class="at-thinking-dots"><span></span><span></span><span></span></div>',
};

/**
 * Escapes HTML special characters to prevent XSS.
 */
export function escapeHtml(val: string): string {
  return String(val)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

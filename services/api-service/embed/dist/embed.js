"use strict";(()=>{var st=`/*
 * Design Tokens
 *
 * Glassmorphism palette with frosted glass surfaces.
 * All visual variables in one place.
 */
:host {
  all: initial;
  --accent: #0f766e;
  --accent-rgb: 15, 118, 110;
  --accent-strong: #0b5f59;
  --accent-soft: #14b8a6;
  --ink: #0f172a;
  --ink-light: #475569;
  --muted: #94a3b8;
  --line: rgba(0, 0, 0, 0.06);
  --panel: rgba(255, 255, 255, 0.92);
  --panel-solid: #ffffff;
  --glass-bg: rgba(255, 255, 255, 0.72);
  --glass-border: rgba(255, 255, 255, 0.45);
  --glass-blur: 20px;
  --rose: #e11d48;
  --blue: #2563eb;
  --shadow-panel:
    0 4px 6px -1px rgba(0, 0, 0, 0.05),
    0 10px 15px -3px rgba(0, 0, 0, 0.08),
    0 20px 50px -12px rgba(0, 0, 0, 0.15);
  --shadow-trigger:
    0 4px 14px rgba(var(--accent-rgb), 0.35),
    0 1px 3px rgba(0, 0, 0, 0.08);
  --radius: 12px;
  --radius-lg: 18px;
  --radius-xl: 20px;
  --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);
  --ease-smooth: cubic-bezier(0.25, 0.1, 0.25, 1);
  --font: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

.at-root {
  all: initial;
  display: block;
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.5;
  color: var(--ink);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
/*
 * Root Container
 *
 * Base styles for the Shadow DOM root element.
 */
.at-root {
  all: initial;
  display: block;
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.5;
  color: var(--ink);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
/*
 * Trigger Button
 *
 * Floating glass-morphic action button.
 * Spring-animated entrance, pulse ring, hover lift.
 */
.at-trigger {
  position: fixed;
  bottom: 20px;
  width: 58px;
  height: 58px;
  border: 0;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-soft));
  color: white;
  cursor: pointer;
  box-shadow: var(--shadow-trigger);
  z-index: 2147483647;
  font-size: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.35s var(--ease-spring), box-shadow 0.3s var(--ease-smooth);
  padding: 0;
  outline: none;
  animation: at-trigger-in 0.6s var(--ease-spring) both;
  animation-delay: 0.3s;
}

/* Pulse ring \u2014 subtle breathing */
.at-trigger::after {
  content: "";
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0;
  animation: at-pulse-ring 3s ease-out infinite;
  animation-delay: 2s;
}

/* Hover: lift + glow */
.at-trigger:hover {
  transform: scale(1.1) translateY(-2px);
  box-shadow:
    0 6px 20px rgba(var(--accent-rgb), 0.4),
    0 2px 6px rgba(0, 0, 0, 0.1);
}

/* Active: press down */
.at-trigger:active {
  transform: scale(0.92);
  transition-duration: 0.1s;
}

/* Position variants */
.at-trigger.at-right { right: 20px; }
.at-trigger.at-left { left: 20px; }

/* SVG icon */
.at-trigger svg {
  width: 26px;
  height: 26px;
  position: relative;
  z-index: 1;
  transition: transform 0.2s var(--ease-spring);
}

.at-trigger:hover svg {
  transform: scale(1.05);
}
/*
 * Chat Panel
 *
 * Frosted glass panel with backdrop-blur.
 * Spring-animated open/close from bottom corner.
 */
.at-panel {
  position: fixed;
  bottom: 20px;
  width: min(400px, calc(100vw - 24px));
  height: min(640px, calc(100vh - 40px));
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--panel);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-panel);
  z-index: 2147483646;
  transition: opacity 0.3s var(--ease-smooth),
              transform 0.4s var(--ease-spring);
  transform-origin: bottom right;
}

/* Position variants */
.at-panel.at-right {
  right: 20px;
  transform-origin: bottom right;
}
.at-panel.at-left {
  left: 20px;
  transform-origin: bottom left;
}

/* Hidden state: scale down + fade */
.at-panel.at-hidden {
  opacity: 0;
  transform: translateY(16px) scale(0.92);
  pointer-events: none;
}

/* Visible state: spring up */
.at-panel:not(.at-hidden) {
  animation: at-panel-in 0.45s var(--ease-spring) both;
}
/*
 * Header
 *
 * Glass-morphic top bar with gradient accent.
 * Clean typography, subtle separator.
 */
.at-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 16px 16px 14px;
  background: linear-gradient(135deg,
    rgba(var(--accent-rgb), 0.95),
    rgba(var(--accent-rgb), 0.88));
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  color: white;
  flex-shrink: 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

/* Agent info */
.at-head-info strong {
  display: block;
  font-size: 15px;
  font-weight: 600;
  color: white;
  letter-spacing: -0.01em;
}

.at-head-info span {
  display: block;
  margin-top: 1px;
  font-size: 12px;
  opacity: 0.7;
  font-weight: 400;
}

/* Online status */
.at-head-status {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 2px;
  font-size: 11px;
  opacity: 0.8;
  font-weight: 500;
}

.at-head-status .at-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #34d399;
  box-shadow: 0 0 6px rgba(52, 211, 153, 0.5);
  animation: at-dot-pulse 2.5s ease-in-out infinite;
}

/* Close button */
.at-close {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(4px);
  color: white;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  padding: 0;
  transition: background 0.2s, transform 0.2s var(--ease-spring);
}

.at-close:hover {
  background: rgba(255, 255, 255, 0.25);
  transform: scale(1.08);
}

.at-close:active {
  transform: scale(0.92);
}

.at-close svg {
  width: 16px;
  height: 16px;
}
/*
 * Messages Area
 *
 * Scrollable container with frosted background.
 * Smooth message entrance, modern bubble design.
 */

/* Scrollable container */
.at-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: rgba(248, 250, 252, 0.5);
}

/* Custom scrollbar \u2014 thin, modern */
.at-messages::-webkit-scrollbar { width: 5px; }
.at-messages::-webkit-scrollbar-track { background: transparent; }
.at-messages::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.12);
  border-radius: 10px;
}
.at-messages::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.2);
}

/* \u2500\u2500\u2500 Message row (assistant with avatar) \u2500\u2500\u2500 */
.at-msg-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  margin-bottom: 2px;
  animation: at-msg-in 0.35s var(--ease-spring) both;
}

/* AI avatar \u2014 glass circle */
.at-avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-soft));
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 13px;
  font-weight: 700;
  margin-left: 14px;
  margin-top: -7px;
  position: relative;
  z-index: 1;
  border: 2px solid var(--panel-solid);
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.25);
}

/* \u2500\u2500\u2500 Base message bubble \u2500\u2500\u2500 */
.at-msg {
  min-width: 0;
  max-width: 88%;
  flex: 0 0 auto;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* User: right-aligned, accent gradient */
.at-msg.at-user {
  align-self: flex-end;
  background: linear-gradient(135deg, var(--accent), var(--accent-soft));
  color: white;
  border-radius: var(--radius) var(--radius) 4px var(--radius);
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.2);
  animation: at-msg-in 0.3s var(--ease-spring) both;
}

/* Assistant: left-aligned, frosted glass */
.at-msg.at-assistant {
  align-self: flex-start;
  position: relative;
  background: var(--glass-bg);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid var(--glass-border);
  color: var(--ink);
  white-space: normal;
  margin-top: -2px;
  border-radius: var(--radius) var(--radius) var(--radius) 4px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

/* Thinking state */
.at-msg.at-assistant.at-thinking {
  padding: 16px 20px 14px;
  min-height: 40px;
  display: flex;
  align-items: center;
}

/* Error message */
.at-msg.at-error {
  background: rgba(254, 242, 242, 0.9);
  backdrop-filter: blur(8px);
  color: var(--rose);
  border: 1px solid rgba(254, 202, 202, 0.6);
  border-radius: var(--radius);
  font-size: 13px;
}

/* \u2500\u2500\u2500 Markdown inside assistant messages \u2500\u2500\u2500 */
.at-msg.at-assistant p { margin: 0 0 6px; }
.at-msg.at-assistant p:last-child,
.at-msg.at-assistant ul:last-child,
.at-msg.at-assistant ol:last-child { margin-bottom: 0; }
.at-msg.at-assistant ul,
.at-msg.at-assistant ol { margin: 0 0 10px; padding-left: 22px; }
.at-msg.at-assistant li { margin: 3px 0; }
.at-msg.at-assistant strong { font-weight: 700; }

.at-msg.at-assistant code {
  padding: 2px 6px;
  border-radius: 6px;
  background: rgba(var(--accent-rgb), 0.08);
  color: var(--accent-strong);
  font-size: 0.9em;
  font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
}

.at-msg.at-assistant a {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.at-msg.at-assistant a:hover {
  color: var(--accent-strong);
}

/* \u2500\u2500\u2500 Thinking dots \u2500\u2500\u2500 */
.at-thinking-dots {
  display: flex;
  align-items: center;
  gap: 6px;
}

.at-thinking-dots span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent-soft);
  animation: at-dot-bounce 1.4s ease-in-out infinite both;
}

.at-thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
.at-thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
.at-thinking-dots span:nth-child(3) { animation-delay: 0s; }

/* Typing cursor */
.at-typing-cursor::after {
  content: "\u258C";
  display: inline;
  animation: at-cursor-blink 0.8s step-end infinite;
  color: var(--accent-soft);
  font-size: 14px;
  margin-left: 1px;
}
/*
 * Problem Report
 *
 * Per-message flag button plus the report dialog. The flag lives next to the
 * bubble inside .at-bubble-line so error-path textContent overwrites of the
 * bubble never wipe it. The dialog itself is a centred modal with a blurred
 * backdrop so users always know which answer they are flagging and what
 * feedback they can leave.
 */

/* \u2500\u2500\u2500 Flag button \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
 * Sibling inside the row, positioned absolutely against the assistant
 * bubble. Lives at the bubble's top-left corner \u2014 right next to the AI
 * avatar, always visible without hover, never competing with the answer
 * text. Survives error-path textContent overwrites because it is a
 * sibling of the bubble, not a descendant of its text-bearing node.
 *
 * The assistant bubble always carries position:relative (set in
 * messages.css alongside the other assistant bubble rules) so this flag
 * positions against the bubble regardless of the panel layout.
 */

.at-bubble-line {
  display: contents;
}

.at-report-btn {
  position: absolute;
  top: -7px;
  left: -7px;
  width: 22px;
  height: 22px;
  border: 1.5px solid #ffffff;
  border-radius: 50%;
  background: var(--rose);
  color: #ffffff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  box-shadow: 0 2px 6px rgba(225, 29, 72, 0.35);
  z-index: 2;
  transition: background 0.15s var(--ease-smooth),
              transform 0.15s var(--ease-spring),
              box-shadow 0.15s var(--ease-smooth);
}

.at-report-btn:hover {
  background: #be123c;
  transform: scale(1.08);
  box-shadow: 0 4px 10px rgba(225, 29, 72, 0.45);
}

.at-report-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px rgba(225, 29, 72, 0.35),
              0 2px 6px rgba(225, 29, 72, 0.35);
}

.at-report-btn svg {
  width: 12px;
  height: 12px;
}

.at-report-btn.at-reported {
  background: var(--muted);
  border-color: #ffffff;
  cursor: default;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
}

.at-report-btn.at-reported:hover {
  transform: none;
  background: var(--muted);
}

/* \u2500\u2500\u2500 Modal overlay & dialog \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500 */

.at-report-overlay {
  position: absolute;
  inset: 0;
  background: rgba(15, 23, 42, 0.42);
  backdrop-filter: blur(6px) saturate(0.9);
  -webkit-backdrop-filter: blur(6px) saturate(0.9);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
  animation: at-report-fade 0.18s var(--ease-smooth) both;
  border-radius: var(--radius-lg);
}

.at-report-modal {
  width: min(360px, calc(100% - 32px));
  background: var(--panel-solid);
  border-radius: 14px;
  box-shadow:
    0 1px 2px rgba(15, 23, 42, 0.08),
    0 10px 32px -8px rgba(15, 23, 42, 0.28);
  padding: 18px 18px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  animation: at-report-pop 0.22s var(--ease-spring) both;
  font-family: var(--font);
  color: var(--ink);
  box-sizing: border-box;
}

.at-report-modal-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.at-report-modal-icon {
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: rgba(var(--accent-rgb), 0.12);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
}

.at-report-modal-icon svg {
  width: 16px;
  height: 16px;
}

.at-report-modal-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.3;
  color: var(--ink);
}

.at-report-modal-hint {
  margin: 2px 0 0;
  font-size: 12px;
  line-height: 1.4;
  color: var(--ink-light);
}

/* Quoted snippet of the answer being reported \u2014 anchors the user's feedback
 * to the exact assistant message they were looking at. */
.at-report-modal-quote {
  margin: 0;
  padding: 10px 12px;
  background: rgba(15, 23, 42, 0.04);
  border-left: 3px solid var(--accent);
  border-radius: 6px;
  font-size: 12.5px;
  line-height: 1.45;
  color: var(--ink-light);
  max-height: 96px;
  overflow: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.at-report-modal-quote-label {
  margin: 0 0 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
}

/* \u2500\u2500\u2500 Form controls (kept on the legacy class names for contract stability) */

.at-report-form {
  /* Used inside .at-report-modal; the standalone inline mini-form is gone.
   * Kept as a class hook for legacy tests/contract checks; visual layout
   * now lives on the modal. */
  display: contents;
}

.at-report-input {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.4;
  font-family: var(--font);
  resize: vertical;
  min-height: 64px;
  max-height: 160px;
  box-sizing: border-box;
  background: var(--panel-solid);
  color: var(--ink);
  transition: border-color 0.15s var(--ease-smooth),
              box-shadow 0.15s var(--ease-smooth);
}

.at-report-input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.16);
}

.at-report-input::placeholder {
  color: var(--muted);
}

.at-report-counter {
  margin: -6px 2px 0;
  font-size: 11px;
  color: var(--muted);
  text-align: right;
}

.at-report-counter.at-report-counter-warn {
  color: var(--rose);
}

.at-report-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 2px;
}

.at-report-actions button {
  border: none;
  border-radius: 8px;
  padding: 7px 14px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  font-family: var(--font);
  transition: background 0.15s var(--ease-smooth),
              color 0.15s var(--ease-smooth),
              transform 0.1s var(--ease-smooth);
}

.at-report-actions button:active:not(:disabled) {
  transform: translateY(1px);
}

.at-report-cancel {
  background: transparent;
  color: var(--ink-light);
}

.at-report-cancel:hover {
  background: rgba(15, 23, 42, 0.05);
  color: var(--ink);
}

.at-report-submit {
  background: var(--accent);
  color: #ffffff;
}

.at-report-submit:hover:not(:disabled) {
  background: var(--accent-strong);
}

.at-report-submit:disabled {
  opacity: 0.55;
  cursor: wait;
}

.at-report-error {
  margin: -4px 2px 0;
  font-size: 12px;
  color: var(--rose);
}

.at-report-done {
  align-self: flex-start;
  margin: 2px 0 2px 14px;
  font-size: 12px;
  color: var(--ink-light);
  animation: at-msg-in 0.25s var(--ease-spring) both;
}

@keyframes at-report-fade {
  from { opacity: 0; }
  to   { opacity: 1; }
}

@keyframes at-report-pop {
  from { opacity: 0; transform: translateY(6px) scale(0.97); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
/*
 * Input Form
 *
 * Glass-morphic bottom bar with frosted textarea.
 * Smooth focus transitions, modern button design.
 */

/* Form container */
.at-form {
  padding: 8px 12px 12px;
  border-top: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.6);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  flex-shrink: 0;
}

/* Horizontal row */
.at-form-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}

/* Textarea \u2014 glass input, matches button height */
.at-form textarea {
  flex: 1;
  resize: none;
  min-height: 38px;
  max-height: 120px;
  border: 1px solid var(--line);
  border-radius: 19px;
  padding: 8px 14px;
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.45;
  outline: none;
  color: var(--ink);
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
  box-sizing: border-box;
}

.at-form textarea:focus {
  border-color: rgba(var(--accent-rgb), 0.5);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.08);
  background: rgba(255, 255, 255, 0.95);
}

.at-form textarea::placeholder {
  color: var(--muted);
  font-weight: 400;
}

/* Mic button */
.at-mic-btn {
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.04);
  color: var(--ink-light);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  flex-shrink: 0;
  transition: background 0.2s, color 0.2s, transform 0.25s var(--ease-spring);
}

.at-mic-btn:hover {
  background: rgba(0, 0, 0, 0.08);
  color: var(--ink);
  transform: scale(1.08);
}

/* Recording state */
.at-mic-btn.at-mic-recording {
  background: var(--rose);
  color: white;
  transform: scale(1.1);
  animation: at-mic-pulse 1.2s ease-in-out infinite;
}

/* Disabled */
.at-mic-btn.at-mic-disabled {
  opacity: 0.25;
  cursor: not-allowed;
  transform: none;
}

/* Send button \u2014 gradient accent */
.at-send-btn {
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-soft));
  color: white;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  flex-shrink: 0;
  transition: transform 0.25s var(--ease-spring), opacity 0.2s, box-shadow 0.2s;
  box-shadow: 0 2px 8px rgba(var(--accent-rgb), 0.25);
}

.at-send-btn:hover {
  transform: scale(1.08);
  box-shadow: 0 4px 12px rgba(var(--accent-rgb), 0.35);
}

.at-send-btn:active {
  transform: scale(0.9);
  transition-duration: 0.1s;
}

.at-send-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.at-send-btn svg { width: 18px; height: 18px; }

/* Mic recording timer */
.at-mic-timer {
  text-align: center;
  color: var(--rose);
  font-size: 12px;
  font-weight: 700;
  padding: 4px 0 2px;
  letter-spacing: 0.03em;
  display: none;
}

.at-mic-timer-visible { display: block; }

/* \u2500\u2500\u2500 Telegram-style animated button swap \u2500\u2500\u2500 */

/* Container for the morphing mic/send button */
.at-swap-btn {
  position: relative;
  width: 38px;
  height: 38px;
  flex-shrink: 0;
}

/* Both buttons stacked absolutely */
.at-swap-btn .at-mic-btn,
.at-swap-btn .at-send-btn {
  position: absolute;
  inset: 0;
  transition: transform 0.3s var(--ease-spring), opacity 0.25s ease;
}

/* Mic visible, send hidden */
.at-swap-btn.at-show-mic .at-mic-btn {
  transform: scale(1) rotate(0deg);
  opacity: 1;
  pointer-events: auto;
}
.at-swap-btn.at-show-mic .at-send-btn {
  transform: scale(0.3) rotate(-90deg);
  opacity: 0;
  pointer-events: none;
}

/* Send visible, mic hidden */
.at-swap-btn.at-show-send .at-send-btn {
  transform: scale(1) rotate(0deg);
  opacity: 1;
  pointer-events: auto;
}
.at-swap-btn.at-show-send .at-mic-btn {
  transform: scale(0.3) rotate(90deg);
  opacity: 0;
  pointer-events: none;
}

/* Hold-to-record visual feedback */
.at-swap-btn .at-mic-btn.at-mic-holding {
  transform: scale(1.15);
  background: var(--rose);
  color: white;
}

/* Legacy mode: both visible side by side (no swap, overrides at-show-*) */
.at-swap-btn.at-legacy .at-mic-btn,
.at-swap-btn.at-legacy .at-send-btn {
  position: static;
  transform: none;
  opacity: 1;
  pointer-events: auto;
}
.at-swap-btn.at-legacy {
  display: contents;
}
.at-swap-btn.at-legacy .at-mic-btn {
  display: flex;
}
.at-swap-btn.at-legacy .at-send-btn {
  display: flex;
}
/*
 * Tool Strip & Tables
 *
 * Tool call pills with glass effect.
 * Modern table styling with subtle borders.
 */

/* Tool call pills */
.at-tool-strip {
  align-self: flex-start;
  max-width: 92%;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 2px;
}

.at-tool-strip span {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 3px 10px;
  border-radius: 999px;
  background: rgba(var(--accent-rgb), 0.06);
  border: 1px solid rgba(var(--accent-rgb), 0.1);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.01em;
  animation: at-msg-in 0.3s var(--ease-spring) both;
}

/* Markdown table */
.at-table-wrap {
  max-width: 100%;
  overflow-x: auto;
  margin: 10px 0 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.6);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.at-table-wrap table {
  min-width: 520px;
  width: 100%;
  border-collapse: collapse;
}

.at-table-wrap th,
.at-table-wrap td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
  line-height: 1.4;
}

.at-table-wrap th {
  background: rgba(0, 0, 0, 0.02);
  color: var(--muted);
  font-weight: 600;
  text-align: left;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.at-table-wrap tr:last-child td { border-bottom: 0; }
.at-table-wrap tr:hover td { background: rgba(0, 0, 0, 0.015); }

/* Retry button */
.at-retry-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding: 8px 18px;
  border: 1px solid rgba(var(--accent-rgb), 0.3);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.8);
  backdrop-filter: blur(8px);
  color: var(--accent);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  font-family: var(--font);
  outline: none;
  transition: all 0.2s var(--ease-smooth);
}

.at-retry-btn:hover {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(var(--accent-rgb), 0.25);
}

.at-retry-btn:active {
  transform: translateY(0);
}

/* Retry countdown */
.at-msg.at-retry-countdown {
  align-self: center;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
  max-width: 100%;
  font-weight: 500;
}
/*
 * Animations & Keyframes
 *
 * Spring-based, smooth animations for all interactive elements.
 */

/* Trigger button entrance \u2014 spring from below */
@keyframes at-trigger-in {
  0% {
    opacity: 0;
    transform: translateY(20px) scale(0.6);
  }
  100% {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* Panel entrance \u2014 spring from corner */
@keyframes at-panel-in {
  0% {
    opacity: 0;
    transform: translateY(16px) scale(0.92);
  }
  100% {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* Trigger pulse ring \u2014 slow breathing */
@keyframes at-pulse-ring {
  0% { transform: scale(1); opacity: 0.25; }
  50% { transform: scale(1.2); opacity: 0; }
  100% { transform: scale(1.2); opacity: 0; }
}

/* Status dot pulse */
@keyframes at-dot-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.8); }
}

/* Message entrance \u2014 slide up + fade */
@keyframes at-msg-in {
  0% {
    opacity: 0;
    transform: translateY(8px);
  }
  100% {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Thinking dots \u2014 wave bounce */
@keyframes at-dot-bounce {
  0%, 80%, 100% {
    transform: translateY(0) scale(0.75);
    opacity: 0.35;
  }
  40% {
    transform: translateY(-7px) scale(1);
    opacity: 0.85;
  }
}

/* Typing cursor blink */
@keyframes at-cursor-blink {
  50% { opacity: 0; }
}

/* Mic recording pulse */
@keyframes at-mic-pulse {
  0% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0.3); }
  70% { box-shadow: 0 0 0 10px rgba(225, 29, 72, 0); }
  100% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0); }
}
/*
 * Responsive
 *
 * Full-screen takeover on mobile devices.
 */

@media (max-width: 480px) {
  .at-panel {
    width: 100vw !important;
    height: 100vh !important;
    bottom: 0 !important;
    right: 0 !important;
    left: 0 !important;
    border-radius: 0 !important;
    border: 0 !important;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .at-trigger { bottom: 14px; }
  .at-trigger.at-right { right: 14px; }
  .at-trigger.at-left { left: 14px; }

  .at-head { padding: 14px 14px 12px; }
  .at-messages { padding: 14px; }
  .at-form { padding: 8px 10px 10px; }
}
`;function lt(e){var h;let t=u=>{var f;return(f=e==null?void 0:e.getAttribute(u))!=null?f:""},n=window,a=(h=n.__EMBED_CONFIG)!=null?h:n.EMBED_CONFIG,r=(u,f)=>{var b;return t(f)||(a?String((b=a[u])!=null?b:""):"")},i=r("agent","data-agent");i||console.error("[Helperium Widget] Missing data-agent attribute");let o=r("lang","data-lang"),l=navigator.language.startsWith("ru")?"ru":"en",p=o==="ru"||o==="en"?o:l;return{agent:i,apiBase:r("apiBase","data-api-base")||window.location.origin,title:r("title","data-title")||"Assistant",lang:p,greeting:r("greeting","data-greeting")||(p==="ru"?"\u0427\u0435\u043C \u043C\u043E\u0433\u0443 \u043F\u043E\u043C\u043E\u0447\u044C?":"How can I help?"),accent:r("accent","data-accent")||"#0f766e",position:r("position","data-position")==="left"?"left":"right",placeholder:r("placeholder","data-placeholder")||(p==="ru"?"\u0417\u0430\u0434\u0430\u0439\u0442\u0435 \u0432\u043E\u043F\u0440\u043E\u0441\u2026":"Ask a question..."),width:r("width","data-width")||"min(380px, calc(100vw - 28px))",height:r("height","data-height")||"min(620px, calc(100vh - 44px))",triggerOffsetBottom:r("triggerOffsetBottom","data-trigger-offset-bottom")||"16px",headerColor:r("headerColor","data-header-color"),showHeader:r("showHeader","data-show-header")!=="false",botBubbleColor:r("botBubbleColor","data-bot-bubble-color")||"#eef3f4",botBubbleText:r("botBubbleText","data-bot-bubble-text")||"var(--ink)",voiceInput:r("voiceInput","data-voice-input")!=="false",voiceOutput:r("voiceOutput","data-voice-output")!=="false",voiceToggle:r("voiceToggle","data-voice-toggle")==="classic"?"classic":"telegram"}}var ct=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
</svg>
`;var dt=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <line x1="6" y1="12" x2="18" y2="12"/>
</svg>
`;var pt=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/>
  <line x1="4" y1="22" x2="4" y2="15"/>
</svg>
`;var gt=`<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="3" width="4" height="8" rx="2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M7 11a5 5 0 0 0 10 0" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M12 16v3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M9 19h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
</svg>
`;var mt=`<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="3" width="4" height="8" rx="2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M7 11a5 5 0 0 0 10 0" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M12 16v3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M9 19h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <line x1="3" y1="3" x2="21" y2="21" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
</svg>
`;var ut=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <line x1="22" y1="2" x2="11" y2="13"/>
  <polygon points="22 2 15 22 11 13 2 9 22 2"/>
</svg>
`;var L={chat:ct,close:dt,flag:pt,send:ut,mic:gt,micOff:mt,thinking:'<div class="at-thinking-dots"><span></span><span></span><span></span></div>'};function O(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;")}function H(e,t,n){let a=document.createElement(e);return a.className=t,n!==void 0&&(a.innerHTML=n),a}function Y(){return!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia)}function ft(e,t){let n=t.position==="left"?"at-left":"at-right",a=H("button","at-trigger "+n,L.chat);a.setAttribute("aria-label",t.lang==="ru"?"\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u0447\u0430\u0442":"Open chat"),a.title=a.getAttribute("aria-label"),e.appendChild(a);let r=H("div","at-panel "+n+" at-hidden");e.appendChild(r);let i=H("div","at-head"),o=H("div","at-head-info");o.innerHTML="<strong>"+O(t.title)+"</strong><span>"+O(t.agent)+"</span>";let l=H("div","at-head-status");l.innerHTML='<span class="at-dot"></span> '+(t.lang==="ru","Online"),o.appendChild(l);let p=H("button","at-close",L.close);p.setAttribute("aria-label",t.lang==="ru"?"\u0417\u0430\u043A\u0440\u044B\u0442\u044C \u0447\u0430\u0442":"Close chat"),i.appendChild(o),i.appendChild(p),r.appendChild(i);let h=H("div","at-messages");r.appendChild(h);let u=document.createElement("form");u.className="at-form";let f=document.createElement("textarea");f.rows=1,f.placeholder=t.placeholder,f.setAttribute("aria-label",t.placeholder),f.style.height="38px";let b=H("button","at-mic-btn",L.mic);b.type="button",b.setAttribute("aria-label",t.lang==="ru"?"\u0417\u0430\u0436\u043C\u0438\u0442\u0435 \u0434\u043B\u044F \u0437\u0430\u043F\u0438\u0441\u0438":"Hold to record"),b.title=b.getAttribute("aria-label");let k=H("button","at-send-btn",L.send);k.type="submit",k.setAttribute("aria-label",t.lang==="ru"?"\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C":"Send");let T=t.voiceToggle==="telegram"&&t.voiceInput&&Y(),M=H("div","at-swap-btn "+(T?"at-show-mic":"at-show-send"));M.appendChild(b),M.appendChild(k),!T&&t.voiceInput&&Y()&&M.classList.add("at-legacy"),(!t.voiceInput||!Y())&&(b.style.display="none");let S=H("div","at-form-row");S.appendChild(f),S.appendChild(M),u.appendChild(S);let g=H("div","at-mic-timer");return u.insertBefore(g,S),r.appendChild(u),{trigger:a,panel:r,messages:h,form:u,textarea:f,closeBtn:p,sendBtn:k,head:i,micBtn:b,micTimer:g,swapBtn:M}}function X(e){return e.classList.contains("at-msg-row")?e.querySelector(".at-msg")||e:(e.classList.contains("at-msg"),e)}function ht(e){let t=e.closest(".at-msg-row");if(t){t.remove();return}e.remove()}function A(e){e&&(e.scrollTop=e.scrollHeight)}function bt(e){return!!(e&&e.scrollHeight-e.scrollTop-e.clientHeight<48)}function W(e){let t=[],n=(e||"").split(`
`),a=0;for(;a<n.length;){let r=n[a];if(xt(n,a)){let o=[];for(;a<n.length&&n[a].trim().charAt(0)==="|";)o.push(n[a]),a++;t.push(Ut(o));continue}if(/^\s*[-*]\s+/.test(r)){let o=[];for(;a<n.length&&/^\s*[-*]\s+/.test(n[a]);)o.push(n[a].replace(/^\s*[-*]\s+/,"")),a++;t.push("<ul>"+o.map(l=>"<li>"+$(l)+"</li>").join("")+"</ul>");continue}if(/^\s*\d+\.\s+/.test(r)){let o=[];for(;a<n.length&&/^\s*\d+\.\s+/.test(n[a]);)o.push(n[a].replace(/^\s*\d+\.\s+/,"")),a++;t.push("<ol>"+o.map(l=>"<li>"+$(l)+"</li>").join("")+"</ol>");continue}let i=[];for(;a<n.length&&n[a].trim()&&!xt(n,a)&&!/^\s*[-*]\s+/.test(n[a])&&!/^\s*\d+\.\s+/.test(n[a]);)i.push(n[a]),a++;i.length&&t.push("<p>"+$(i.join(`
`)).replace(/\n/g,"<br>")+"</p>"),a<n.length&&!n[a].trim()&&a++}return t.join("")}function xt(e,t){let n=e[t],a=e[t+1];return!n||!a?!1:n.trim().charAt(0)==="|"&&/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(a)}function Ut(e){let t=[];for(let r=0;r<e.length;r++){let i=e[r];if(/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(i))continue;let o=i.trim().replace(/^\|/,"").replace(/\|$/,"").split("|").map(l=>l.trim());t.push(o)}if(!t.length)return"";let n=t[0],a=t.slice(1);return'<div class="at-table-wrap"><table><thead><tr>'+n.map(r=>"<th>"+$(r)+"</th>").join("")+"</tr></thead><tbody>"+a.map(r=>"<tr>"+r.map(i=>"<td>"+$(i)+"</td>").join("")+"</tr>").join("")+"</tbody></table></div>"}var Pt=new Set(["http","https","mailto"]);function qt(e){var a;let t=(a=e.replace(/^\s+/,"").split(/[/?#]/,1)[0])!=null?a:"";if(!t.includes(":"))return!0;let n=t.replace(/\s+/g,"").split(":",1)[0].toLowerCase();return/^[a-z][a-z0-9+.-]*$/.test(n)&&Pt.has(n)}function $(e){return O(e).replace(/\*\*([^*]+)\*\*/g,"<strong>$1</strong>").replace(/\*([^*]+)\*/g,"<em>$1</em>").replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\[([^\]]+)\]\(([^)]+)\)/g,(t,n,a)=>qt(a)?`<a href="${a}" target="_blank" rel="noopener noreferrer">${n}</a>`:n)}function vt(e){let t=e.toLowerCase();return/поиск|найти|find|search/i.test(t)?"\u{1F50D}":/чтение|get|получени/i.test(t)?"\u{1F4CB}":/запрос|query/i.test(t)?"\u{1F4CA}":/list|список/i.test(t)?"\u{1F4CB}":"\u26A1"}function P(e,t){let n=t||{},a=[...new Set(e)];if(!a.length)return null;let r=document.createElement("div");return r.className="at-tool-strip",r.innerHTML=a.map(i=>{let o=n[i]||i;return`<span>${vt(o)} ${O(o)}</span>`}).join(""),r}function K(e,t,n,a){var p;let r=[...new Set(t)];if(!r.length)return;let i=((p=e.closest)==null?void 0:p.call(e,".at-msg-row"))||e,o=i.previousElementSibling;if(o&&o.className==="at-tool-strip"){o.innerHTML=r.map(h=>{let u=n[h]||h;return`<span>${vt(u)} ${O(u)}</span>`}).join("");return}let l=P(r,n);l&&a.insertBefore(l,i)}function wt(e,t){let n=document.createElement("div");n.className="at-msg-row";let a=document.createElement("div");a.className="at-msg at-assistant",t.thinking?(a.dataset.raw="",a.innerHTML=L.thinking):(a.dataset.raw=e||"",a.innerHTML=W(e||""));let r=document.createElement("div");return r.className="at-avatar",r.textContent="AI",n.appendChild(a),n.appendChild(r),t.report!==!1&&t.onAssistantRow&&t.onAssistantRow(n,a),{row:n,node:a}}function yt(e,t,n,a){let r=a||{};if(e==="assistant"){let{row:i}=wt(t,r);r.before?n.insertBefore(i,r.before):n.appendChild(i)}else{let i=document.createElement("div");i.className="at-msg at-user",i.textContent=t||"",r.before?n.insertBefore(i,r.before):n.appendChild(i)}return r.scroll!==!1&&A(n),n.lastElementChild}function G(e,t,n,a,r){let i=n();if(!i.length){a("assistant",e.greeting,{persist:!1,scroll:!1,report:!1});return}t.innerHTML="";let o=[];for(let l of i)if(l.kind==="user")a("user",l.text,{persist:!1,scroll:!1});else if(l.kind==="assistant"){let p=(l.tools||[]).filter(Boolean),h=String(l.text||"");if(!h.trim()&&p.length>0){o=o.concat(p);continue}let u=o.concat(p);if(o=[],u.length>0){let b=P(u);b&&t.appendChild(b)}let{row:f}=wt(h,r!=null&&r.onAssistantRow?{onAssistantRow:r.onAssistantRow}:{});t.appendChild(f)}if(o.length>0){let l=P(o);l&&t.appendChild(l)}A(t)}var U=1e3,Jt=4e3,Yt=500,Xt=2048,Kt=20,Gt=2e3,Q=20,Qt=50;function Z(e){return"at_reported_"+e}function kt(e,t){let n=e+":"+String(t||"").slice(0,200),a=5381;for(let r=0;r<n.length;r++)a=(a<<5)+a+n.charCodeAt(r)|0;return(a>>>0).toString(36)}function Zt(e,t){try{let n=sessionStorage.getItem(Z(e.agent)),a=n?JSON.parse(n):[];return Array.isArray(a)&&a.includes(t)}catch(n){return!1}}function te(e,t){try{let n=sessionStorage.getItem(Z(e.agent)),a=n?JSON.parse(n):[],r=Array.isArray(a)?a.filter(i=>typeof i=="string"):[];for(r.includes(t)||r.push(t);r.length>Qt;)r.shift();sessionStorage.setItem(Z(e.agent),JSON.stringify(r))}catch(n){}}function Tt(e){let t=e.classList.contains("at-error")?"error":"assistant",n=t==="error"?e.textContent||"":e.dataset.raw||"",a=[];try{let i=JSON.parse(e.dataset.tools||"[]");Array.isArray(i)&&(a=i.filter(o=>typeof o=="string"))}catch(i){a=[]}let r=[];try{let i=JSON.parse(e.dataset.displayNames||"{}");i&&typeof i=="object"&&!Array.isArray(i)&&(r=Object.values(i).map(String))}catch(i){r=[]}return{kind:t,text:String(n||"").slice(0,Jt),tools:a,displayNames:r,correlationId:e.dataset.correlationId||""}}function ee(e,t,n){let a=e.getTranscript().slice(-Kt).map(o=>({kind:o.kind,text:String(o.text||"").slice(0,Gt),tools:(o.tools||[]).slice(0,Q),ts:typeof o.ts=="number"?new Date(o.ts).toISOString():null})),r={agent:e.config.agent,session_id:e.getSessionId(),lang:e.config.lang,message:{kind:t.kind,text:t.text,tools:t.tools.slice(0,Q),display_names:t.displayNames.slice(0,Q)},transcript:a},i=n.trim().slice(0,U);i&&(r.comment=i),(t.kind==="error"||t.correlationId)&&(r.last_error={text:t.kind==="error"?t.text.slice(0,Yt):"",correlation_id:t.correlationId||null});try{let o=window.location.href.slice(0,Xt);o&&(r.page_url=o)}catch(o){}return r}async function ne(e,t,n){let a=ee(e,t,n),r=await fetch(e.config.apiBase+"/api/reports",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(a)});if(!r.ok)throw new Error("report_failed_"+r.status)}function St(e,t,n){let{config:a}=e;if(n.querySelector(".at-report-btn"))return;let r=document.createElement("button");r.type="button",r.className="at-report-btn",r.innerHTML=L.flag;let i=a.lang==="ru"?"\u041F\u043E\u0436\u0430\u043B\u043E\u0432\u0430\u0442\u044C\u0441\u044F \u043D\u0430 \u044D\u0442\u043E\u0442 \u043E\u0442\u0432\u0435\u0442":"Report this answer";r.setAttribute("aria-label",i),r.title=i;let o=()=>{let l=Tt(t);Zt(a,kt(l.kind,l.text))?(r.classList.add("at-reported"),r.disabled=!0):(r.classList.remove("at-reported"),r.disabled=!1)};o(),new MutationObserver(o).observe(t,{attributes:!0}),r.addEventListener("click",()=>{r.classList.contains("at-reported")||re(e,t,n,r)}),n.firstChild?n.insertBefore(r,n.firstChild):n.appendChild(r)}function re(e,t,n,a){var F;let r=e.config.lang==="ru",i=n.closest(".at-panel"),o=n.closest(".at-messages"),l=(i==null?void 0:i.querySelector(".at-report-overlay"))||(o==null?void 0:o.querySelector(".at-report-overlay"))||((F=n.closest(".at-root"))==null?void 0:F.querySelector(".at-report-overlay"));l&&l.remove();let p=Tt(t),h=document.createElement("div");h.className="at-report-overlay",h.setAttribute("role","presentation");let u=document.createElement("div");u.className="at-report-modal",u.setAttribute("role","dialog"),u.setAttribute("aria-modal","true"),u.setAttribute("aria-labelledby","at-report-modal-title");let f=document.createElement("div");f.className="at-report-modal-head";let b=document.createElement("div");b.className="at-report-modal-icon",b.innerHTML=L.flag;let k=document.createElement("div"),T=document.createElement("h3");T.className="at-report-modal-title",T.id="at-report-modal-title",T.textContent=r?"\u041F\u043E\u0436\u0430\u043B\u043E\u0432\u0430\u0442\u044C\u0441\u044F \u043D\u0430 \u044D\u0442\u043E\u0442 \u043E\u0442\u0432\u0435\u0442":"Report this answer";let M=document.createElement("p");M.className="at-report-modal-hint",M.textContent=r?"\u041E\u043F\u0438\u0448\u0438\u0442\u0435 \u043F\u0440\u043E\u0431\u043B\u0435\u043C\u0443 \u2014 \u043C\u044B \u043F\u0435\u0440\u0435\u0434\u0430\u0434\u0438\u043C \u043E\u0442\u0437\u044B\u0432 \u043E\u043F\u0435\u0440\u0430\u0442\u043E\u0440\u0443. \u041E\u0442\u0432\u0435\u0442 \u043C\u043E\u0434\u0435\u043B\u0438 \u0438 \u043A\u043E\u043D\u0442\u0435\u043A\u0441\u0442 \u0434\u0438\u0430\u043B\u043E\u0433\u0430 \u0443\u0439\u0434\u0443\u0442 \u0432\u043C\u0435\u0441\u0442\u0435 \u0441 \u0436\u0430\u043B\u043E\u0431\u043E\u0439.":"Tell us what went wrong \u2014 we pass the report to the operator along with the answer and surrounding chat.",k.appendChild(T),k.appendChild(M),f.appendChild(b),f.appendChild(k);let S=document.createElement("p");S.className="at-report-modal-quote-label",S.textContent=r?"\u041E\u0442\u0432\u0435\u0442 \u0430\u0441\u0441\u0438\u0441\u0442\u0435\u043D\u0442\u0430":"Assistant answer";let g=document.createElement("blockquote");g.className="at-report-modal-quote";let c=String(p.text||"").trim();g.textContent=c.length>280?c.slice(0,277)+"\u2026":c;let x=document.createElement("div");x.className="at-report-form";let y=document.createElement("textarea");y.className="at-report-input",y.rows=3,y.maxLength=U,y.placeholder=r?"\u0427\u0442\u043E \u043D\u0435 \u0442\u0430\u043A \u0441 \u044D\u0442\u0438\u043C \u043E\u0442\u0432\u0435\u0442\u043E\u043C? (\u043D\u0435\u043E\u0431\u044F\u0437\u0430\u0442\u0435\u043B\u044C\u043D\u043E)":"What's wrong with this answer? (optional)",y.setAttribute("aria-label",y.placeholder);let R=document.createElement("div");R.className="at-report-counter";let v=()=>{let E=U-y.value.length;R.textContent=r?`\u041E\u0441\u0442\u0430\u043B\u043E\u0441\u044C ${E} \u0441\u0438\u043C\u0432\u043E\u043B\u043E\u0432`:`${E} characters left`,R.classList.toggle("at-report-counter-warn",E<U*.1)};y.addEventListener("input",v),v();let D=document.createElement("div");D.className="at-report-actions";let B=document.createElement("button");B.type="button",B.className="at-report-cancel",B.textContent=r?"\u041E\u0442\u043C\u0435\u043D\u0430":"Cancel";let C=document.createElement("button");C.type="button",C.className="at-report-submit",C.textContent=r?"\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C":"Send",D.appendChild(B),D.appendChild(C),x.appendChild(y),x.appendChild(R),x.appendChild(D),u.appendChild(f),u.appendChild(S),u.appendChild(g),u.appendChild(x),h.appendChild(u);let j=i||o||n.closest(".at-root");if(!j)return;j.appendChild(h),y.focus({preventScroll:!0});let _=()=>{h.remove(),document.removeEventListener("keydown",N)},N=E=>{E.key==="Escape"&&(E.preventDefault(),_())};B.addEventListener("click",_),h.addEventListener("click",E=>{E.target===h&&_()}),document.addEventListener("keydown",N),C.addEventListener("click",()=>{var V;let E=y.value.slice(0,U);C.disabled=!0,C.textContent=r?"\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0430\u2026":"Sending\u2026",B.disabled=!0,y.disabled=!0,(V=u.querySelector(".at-report-error"))==null||V.remove(),ne(e,p,E).then(()=>{var m;_(),a.classList.add("at-reported"),a.disabled=!0,a.setAttribute("aria-label",r?"\u0416\u0430\u043B\u043E\u0431\u0430 \u043E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u0430":"Report sent"),te(e.config,kt(p.kind,p.text));let s=document.createElement("div");s.className="at-report-done",s.textContent=r?"\u0421\u043F\u0430\u0441\u0438\u0431\u043E, \u043E\u0442\u0447\u0451\u0442 \u043E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D.":"Thanks, your report was sent.",(m=n.parentNode)==null||m.insertBefore(s,n.nextSibling),window.setTimeout(()=>s.remove(),4e3)}).catch(()=>{C.disabled=!1,B.disabled=!1,y.disabled=!1,C.textContent=r?"\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C":"Send";let s=document.createElement("div");s.className="at-report-error",s.textContent=r?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C. \u041F\u043E\u043F\u0440\u043E\u0431\u0443\u0439\u0442\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437.":"Failed to send. Please try again.",x.appendChild(s)})})}async function tt(e,t,n,a="en"){var f;let r=e.body.getReader(),i=new TextDecoder,o="",l=!1,p=e.headers.get("x-correlation-id")||"",h=!1,u=b=>{var k;!b||h||(h=!0,(k=n.onSessionToken)==null||k.call(n,b))};for(;;){let{done:b,value:k}=await r.read();if(b)break;o+=i.decode(k,{stream:!0});let T=o.split(`

`);o=T.pop();for(let M of T){let S=M.split(`
`).find(c=>c.startsWith("data:"));if(!S)continue;let g;try{g=JSON.parse(S.slice(5).trim())}catch(c){continue}switch(t.classList.contains("at-thinking")&&t.classList.remove("at-thinking"),g.type){case"session":u(g.session_token);break;case"token":n.onToken(g.text||"");break;case"final":l=!0,n.onFinal(g.text||"");break;case"tool_call":{let x=JSON.parse(t.dataset.tools||"[]"),y=JSON.parse(t.dataset.displayNames||"{}");g.name&&!x.includes(g.name)&&(x.push(g.name),t.dataset.tools=JSON.stringify(x)),g.display_name&&g.name&&!y[g.name]&&(y[g.name]=g.display_name,t.dataset.displayNames=JSON.stringify(y)),n.onToolCall(g.name||"",g.display_name);break}case"audio":g.data&&n.onAudio(g.data);break;case"done":if(l=!0,t.classList.contains("at-error"))return;(f=t.dataset.raw)!=null&&f.trim()||n.onFinal(a==="ru"?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u043E\u043B\u0443\u0447\u0438\u0442\u044C \u043E\u0442\u0432\u0435\u0442.":"No response.");let c=[];try{c=JSON.parse(t.dataset.tools||"[]")}catch(x){}n.onDone(t.dataset.raw||"",c),t.dataset.saved="true";break;case"error":l=!0,t.classList.remove("at-thinking"),t.classList.add("at-error"),t.textContent=g.text||(a==="ru"?"\u041F\u0440\u043E\u0438\u0437\u043E\u0448\u043B\u0430 \u043E\u0448\u0438\u0431\u043A\u0430.":"An error occurred."),t.dataset.correlationId=g.correlation_id||p;break}}}l||(p&&(t.dataset.correlationId=p),n.onError(a==="ru"?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u043E\u043B\u0443\u0447\u0438\u0442\u044C \u043E\u0442\u0432\u0435\u0442.":"No response.")),u(e.headers.get("x-session-token"))}function et(e){let{message:t,targetNode:n,config:a,sessionId:r,sessionToken:i,onUnauthorized:o,messagesEl:l,retryAttempts:p,maxRetries:h,callbacks:u,removeMsgRow:f,scheduleRetry:b,retryChat:k,scrollToBottom:T}=e;n.classList.add("at-thinking");let M=a.apiBase+"/api/chat/"+encodeURIComponent(a.agent),S={"Content-Type":"application/json"};i&&(S["X-Session-Token"]=i),fetch(M,{method:"POST",headers:S,body:JSON.stringify({message:t,session_id:r})}).then(g=>{if(g.status===401){n.classList.remove("at-thinking"),f(n),o==null||o();return}if(g.status===429){n.classList.remove("at-thinking"),f(n);let c=g.headers.get("Retry-After"),x=5;if(c){let v=parseInt(c,10);!isNaN(v)&&v>0&&(x=v)}if(p.set(t,(p.get(t)||0)+1),p.get(t)>=h){let v=document.createElement("div");v.className="at-msg at-assistant at-error",v.innerHTML="\u26A0\uFE0F Server overloaded.";let D=document.createElement("button");D.className="at-retry-btn",D.textContent="Retry",v.appendChild(D),l.appendChild(v),T(l),D.addEventListener("click",()=>{p.delete(t),v.remove(),k(t)});return}let R=document.createElement("div");R.className="at-msg at-assistant",R.textContent="\u26A0\uFE0F "+(a.lang==="ru"?"\u0421\u0435\u0440\u0432\u0435\u0440 \u043F\u0435\u0440\u0435\u0433\u0440\u0443\u0436\u0435\u043D. \u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Server overloaded. Retry in")+" "+x+"s.",l.appendChild(R),T(l),b(t,x*1e3);return}if(!g.ok){n.classList.remove("at-thinking"),n.classList.add("at-error"),n.textContent="Error: "+g.status;let c=g.headers.get("x-correlation-id");c&&(n.dataset.correlationId=c);return}return tt(g,n,u,a.lang)}).catch(()=>{n.classList.remove("at-thinking"),n.classList.add("at-error"),n.innerHTML="\u26A0\uFE0F "+(a.lang==="ru"?"\u041D\u0435\u0442 \u0441\u043E\u0435\u0434\u0438\u043D\u0435\u043D\u0438\u044F \u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C.":"No connection to server.")+'<br><button class="at-retry-btn">'+(a.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u044C":"Retry")+"</button>";let g=n.querySelector(".at-retry-btn");g&&g.addEventListener("click",()=>{n.classList.remove("at-error"),n.innerHTML=L.thinking,et(e)})})}function q(e){try{let n=sessionStorage.getItem(e);if(n)return n}catch(n){}let t=typeof crypto!="undefined"&&crypto.randomUUID?crypto.randomUUID():"sess-"+Date.now()+"-"+Math.random().toString(36).slice(2,10);try{sessionStorage.setItem(e,t)}catch(n){}return t}function Lt(e){try{return sessionStorage.getItem(e)}catch(t){return null}}function Et(e,t){try{sessionStorage.setItem(e,t)}catch(n){}}function Rt(e,t){try{sessionStorage.removeItem(e),t&&sessionStorage.removeItem(t)}catch(n){}}function Mt(e){try{let t=sessionStorage.getItem(e);if(!t)return[];let n=JSON.parse(t);return Array.isArray(n)?n:[]}catch(t){return[]}}function Ct(e,t){try{sessionStorage.setItem(e,JSON.stringify(t))}catch(n){}}function nt(e,t){function n(){let r=Mt(e),i=[];for(let o of r){if(o.sessionId!==t)continue;let l={kind:o.kind,text:o.text,tools:o.tools||[]},p=o.ts;typeof p=="number"&&(l.ts=p),i.push(l)}return i}function a(r,i,o){let l=Mt(e);l.push({sessionId:t,kind:r,text:String(i||""),tools:o||[],ts:Date.now()});let p=l.filter(h=>h.sessionId===t);if(p.length>100){let h=p.length-100,u=0,f=l.filter(b=>b.sessionId===t&&u<h?(u++,!1):!0);Ct(e,f);return}Ct(e,l)}return{readStored:n,appendStored:a}}var ae=20,oe=3;function rt(e,t,n){let a=e.dataset.raw||"";e.dataset.raw=a+t,e.dataset.typewriterRunning?e.dataset.typewriterBuffer=(e.dataset.typewriterBuffer||"")+t:(e.dataset.typewriterRunning="1",e.dataset.typewriterBuffer=a+t,e.dataset.typewriterDisplayed=a,Ht(e,n))}function Ht(e,t){let n=e.dataset.typewriterBuffer||"",a=e.dataset.typewriterDisplayed||"";if(a.length>=n.length){e.dataset.typewriterRunning="",e.innerHTML=W(n),A(t);return}let r=Math.min(oe,n.length-a.length),i=n.slice(0,a.length+r);e.dataset.typewriterDisplayed=i;let o=W(i);if(i.length<n.length)e.classList.add("at-typing-cursor"),e.innerHTML=o;else{e.classList.remove("at-typing-cursor"),e.innerHTML=o,e.dataset.typewriterRunning="",A(t);return}bt(t)&&A(t),setTimeout(()=>Ht(e,t),ae)}function at(e,t){e.classList.remove("at-thinking"),e.dataset.raw=t,e.innerHTML=W(t)}var At=120,ie="audio/webm;codecs=opus",se="audio/webm";function Bt(){return{mediaRecorder:null,micChunks:[],micStream:null,micStartTime:0,micDuration:0,micTimerInterval:null}}function Dt(e,t){e.micDuration=Math.floor((Date.now()-e.micStartTime)/1e3);let n=Math.floor(e.micDuration/60),a=e.micDuration%60;t.textContent=(n>0?n+"m ":"")+a+"s"}function J(e,t){if(!(e.mediaRecorder&&e.mediaRecorder.state==="recording")){if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){t.micBtn.classList.add("at-mic-disabled");return}navigator.mediaDevices.getUserMedia({audio:!0}).then(n=>{e.micStream=n,e.micChunks=[];let a=ie;MediaRecorder.isTypeSupported(a)||(a=se),e.mediaRecorder=new MediaRecorder(n,{mimeType:a}),e.mediaRecorder.ondataavailable=r=>{r.data.size>0&&e.micChunks.push(r.data)},e.mediaRecorder.onstop=()=>{n.getTracks().forEach(l=>l.stop()),e.micStream=null,t.micBtn.innerHTML=L.mic,t.micBtn.classList.remove("at-mic-recording"),t.micTimer.classList.remove("at-mic-timer-visible"),e.micTimerInterval&&(clearInterval(e.micTimerInterval),e.micTimerInterval=null);let r=new Blob(e.micChunks,{type:a});if(r.size===0)return;let i=t.micTimer.textContent||e.micDuration+"s";t.addMessage("user","\u{1F3A4} "+i,{persist:!0});let o=t.addMessage("assistant","",{thinking:!0,persist:!1,scroll:!1});t.onStreamVoice(r,o)},e.mediaRecorder.start(),t.micBtn.innerHTML=L.micOff,t.micBtn.classList.add("at-mic-recording"),t.micTimer.classList.add("at-mic-timer-visible"),e.micStartTime=Date.now(),e.micDuration=0,Dt(e,t.micTimer),e.micTimerInterval=window.setInterval(()=>Dt(e,t.micTimer),1e3),At>0&&setTimeout(()=>{e.mediaRecorder&&e.mediaRecorder.state==="recording"&&e.mediaRecorder.stop()},At*1e3)}).catch(n=>{n.name==="NotAllowedError"||n.name==="PermissionDeniedError"?t.addMessage("assistant",t.config.lang==="ru"?"\u274C \u0420\u0430\u0437\u0440\u0435\u0448\u0438\u0442\u0435 \u0434\u043E\u0441\u0442\u0443\u043F \u043A \u043C\u0438\u043A\u0440\u043E\u0444\u043E\u043D\u0443 \u0432 \u043D\u0430\u0441\u0442\u0440\u043E\u0439\u043A\u0430\u0445 \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u0430":"\u274C Please allow microphone access in browser settings",{persist:!1}):t.micBtn.classList.add("at-mic-disabled")})}}function ot(e){e.mediaRecorder&&e.mediaRecorder.state==="recording"&&e.mediaRecorder.stop()}function It(e,t,n){t.classList.add("at-thinking");let a=n.config.apiBase+"/api/chat/voice",r=new FormData;r.append("audio",e,"voice.webm"),r.append("session_id",n.sessionId),r.append("agent",n.config.agent),r.append("lang",n.config.lang);let i={};n.sessionToken&&(i["X-Session-Token"]=n.sessionToken),fetch(a,{method:"POST",headers:i,body:r}).then(o=>{var l;if(o.status===401){t.classList.remove("at-thinking"),t.remove(),(l=n.onUnauthorized)==null||l.call(n);return}if(o.status===429){t.classList.remove("at-thinking"),t.remove();let p=document.createElement("div");p.className="at-msg at-assistant",p.textContent=n.config.lang==="ru"?"\u26A0\uFE0F \u0421\u0435\u0440\u0432\u0435\u0440 \u043F\u0435\u0440\u0435\u0433\u0440\u0443\u0436\u0435\u043D. \u041F\u043E\u043F\u0440\u043E\u0431\u0443\u0439\u0442\u0435 \u043F\u043E\u0437\u0436\u0435.":"\u26A0\uFE0F Server overloaded. Try again later.",n.messagesEl.appendChild(p),n.scrollToBottom(n.messagesEl);return}if(!o.ok){t.classList.remove("at-thinking"),t.classList.add("at-error"),t.textContent="Error: "+o.status;return}return tt(o,t,n.callbacks,n.config.lang)}).catch(()=>{t.classList.remove("at-thinking"),t.classList.add("at-error"),t.innerHTML="\u26A0\uFE0F "+(n.config.lang==="ru"?"\u041E\u0448\u0438\u0431\u043A\u0430 \u0441\u043E\u0435\u0434\u0438\u043D\u0435\u043D\u0438\u044F.":"Connection error.")})}function it(e){try{let t=atob(e),n=new Uint8Array(t.length);for(let o=0;o<t.length;o++)n[o]=t.charCodeAt(o);let a=new Blob([n],{type:"audio/mpeg"}),r=URL.createObjectURL(a);new Audio(r).play().catch(()=>{})}catch(t){}}var Ot=3;function le(){let e=document.currentScript;if(e&&e instanceof HTMLScriptElement)return e;let t=document.querySelector("script[data-agent]");if(t)return t;let n=document.querySelector('script[src*="embed.js"]');return n||null}function ce(e){let t=e.replace("#",""),n=parseInt(t.length===3?t.split("").map(a=>a+a).join(""):t,16);return`${n>>16&255}, ${n>>8&255}, ${n&255}`}function de(e){let t=e.headerColor||e.accent;return`:host {
  --accent: ${e.accent};
  --accent-rgb: ${ce(e.accent)};
  --accent-strong: ${e.accent};
  --trigger-offset-bottom: ${e.triggerOffsetBottom};
  --panel-width: ${e.width};
  --panel-height: ${e.height};
  --header-bg: ${t};
  --bot-bubble-bg: ${e.botBubbleColor};
  --bot-bubble-text: ${e.botBubbleText};
}
${e.showHeader?"":".at-head { display: none; }"}`}function _t(){var E,V;let e=le(),t=lt(e);if(!t.agent)return;let n,a,r,i="at_messages_"+t.agent,o="at_session_"+t.agent;r=q(o),{readStored:n,appendStored:a}=nt(i,r);let l="at_session_token_"+t.agent,p=()=>Lt(l),h=s=>Et(l,s),u=()=>{Rt(o,l),r=q(o)},f=Bt(),b=new Map,k=document.createElement("div");k.id="helperium-widget-"+t.agent.replace(/[^a-zA-Z0-9_-]/g,"");let T=k.attachShadow({mode:"open"}),M=document.createElement("style");M.textContent=st,T.appendChild(M);let S=document.createElement("style");S.textContent=de(t),T.appendChild(S);let g=document.createElement("div");g.className="at-root",T.appendChild(g);let c=ft(g,t),x=c.messages,y={config:t,getSessionId:()=>r,getTranscript:()=>n()},R=(s,m)=>St(y,m,s);function v(s,m,d){let w=s==="assistant"?{...d,onAssistantRow:R}:{...d},I=yt(s,m,x,w);return d!=null&&d.persist&&a(s,m,d.tools),I}function D(s,m){let d=Math.ceil(m/1e3),w=document.createElement("div");w.className="at-msg at-retry-countdown",w.textContent=(t.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Retry in")+" "+d+"s...",x.appendChild(w),A(x);let I=setInterval(()=>{d--,d<=0?(clearInterval(I),w.remove(),B(s)):w.textContent=(t.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Retry in")+" "+d+"s..."},1e3)}function B(s){let m=v("assistant","",{thinking:!0,persist:!1,scroll:!1}),d=X(m);C(s,d)}function C(s,m){et({message:s,targetNode:m,config:t,sessionId:r,sessionToken:p(),onUnauthorized:()=>{let d=(b.get(s)||0)+1;if(b.set(s,d),d>=Ot){b.delete(s),v("assistant",t.lang==="ru"?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u0432\u043E\u0441\u0441\u0442\u0430\u043D\u043E\u0432\u0438\u0442\u044C \u0441\u0435\u0441\u0441\u0438\u044E. \u041E\u0431\u043D\u043E\u0432\u0438\u0442\u0435 \u0441\u0442\u0440\u0430\u043D\u0438\u0446\u0443 \u0438 \u043F\u043E\u043F\u0440\u043E\u0431\u0443\u0439\u0442\u0435 \u0441\u043D\u043E\u0432\u0430.":"Could not restore the session. Reload the page and try again.",{persist:!1});return}u(),B(s)},messagesEl:x,retryAttempts:b,maxRetries:Ot,callbacks:{onToken:d=>rt(m,d,x),onFinal:d=>at(m,d),onToolCall:(d,w)=>{let I=JSON.parse(m.dataset.tools||"[]"),z=JSON.parse(m.dataset.displayNames||"{}");I.includes(d)||(I.push(d),m.dataset.tools=JSON.stringify(I)),w&&!z[d]&&(z[d]=w,m.dataset.displayNames=JSON.stringify(z)),K(m,I,z,x)},onAudio:d=>it(d),onDone:(d,w)=>{a("assistant",d,w)},onError:d=>{m.classList.remove("at-thinking"),m.classList.add("at-error"),m.textContent=d},onSessionToken:h},addMessage:v,removeMsgRow:ht,scheduleRetry:(d,w)=>D(d,w),retryChat:B,scrollToBottom:A})}c.trigger.addEventListener("click",()=>{c.panel.classList.remove("at-hidden"),c.trigger.style.display="none",c.textarea.focus(),A(x)}),c.closeBtn.addEventListener("click",()=>{c.panel.classList.add("at-hidden"),c.trigger.style.display="flex"});function j(){let s=c.textarea.value.trim();if(!s)return;c.textarea.value="",c.textarea.style.height="auto",_(),v("user",s,{persist:!0});let m=v("assistant","",{thinking:!0,persist:!1,scroll:!1}),d=X(m);C(s,d)}c.textarea.addEventListener("input",function(){this.style.height="auto",this.style.height=Math.min(this.scrollHeight,120)+"px",_()}),c.textarea.addEventListener("keydown",s=>{s.key==="Enter"&&!s.shiftKey&&(s.preventDefault(),j())}),c.form.addEventListener("submit",s=>{s.preventDefault(),j()});function _(){if(t.voiceToggle!=="telegram"||!N)return;c.textarea.value.trim().length>0?(c.swapBtn.classList.remove("at-show-mic"),c.swapBtn.classList.add("at-show-send")):(c.swapBtn.classList.remove("at-show-send"),c.swapBtn.classList.add("at-show-mic"))}let N=t.voiceInput&&typeof((E=navigator.mediaDevices)==null?void 0:E.getUserMedia)=="function";if(t.voiceToggle==="telegram"&&N){let s=!1;c.micBtn.addEventListener("mousedown",d=>{d.preventDefault(),!s&&(s=!0,c.micBtn.classList.add("at-mic-holding"),J(f,{micBtn:c.micBtn,micTimer:c.micTimer,config:t,sessionId:r,addMessage:v,onStreamVoice:F(),onStreamChat:C}))});let m=()=>{s&&(s=!1,c.micBtn.classList.remove("at-mic-holding"),ot(f))};c.micBtn.addEventListener("mouseup",m),c.micBtn.addEventListener("mouseleave",m),c.micBtn.addEventListener("touchstart",d=>{d.preventDefault(),!s&&(s=!0,c.micBtn.classList.add("at-mic-holding"),J(f,{micBtn:c.micBtn,micTimer:c.micTimer,config:t,sessionId:r,addMessage:v,onStreamVoice:F(),onStreamChat:C}))},{passive:!1}),c.micBtn.addEventListener("touchend",m),c.micBtn.addEventListener("touchcancel",m)}else N&&(c.micBtn.style.display="flex",c.micBtn.addEventListener("mousedown",s=>{s.preventDefault(),c.micBtn.classList.contains("at-mic-recording")?ot(f):J(f,{micBtn:c.micBtn,micTimer:c.micTimer,config:t,sessionId:r,addMessage:v,onStreamVoice:F(),onStreamChat:C})}));function F(){return(s,m)=>{It(s,m,{config:t,sessionId:r,sessionToken:p(),onSessionToken:h,onUnauthorized:()=>{u(),v("assistant",t.lang==="ru"?"\u0421\u0435\u0441\u0441\u0438\u044F \u0438\u0441\u0442\u0435\u043A\u043B\u0430 \u2014 \u043F\u043E\u043F\u0440\u043E\u0431\u0443\u0439\u0442\u0435 \u043E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C \u0441\u043E\u043E\u0431\u0449\u0435\u043D\u0438\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437.":"Session expired \u2014 please send your message again.",{persist:!1})},messagesEl:x,callbacks:{onToken:d=>rt(m,d,x),onFinal:d=>at(m,d),onToolCall:(d,w)=>{K(m,[d],w?{[d]:w}:{},x)},onAudio:d=>it(d),onDone:(d,w)=>{a("assistant",d,w)},onError:d=>{m.classList.remove("at-thinking"),m.classList.add("at-error"),m.textContent=d}},scrollToBottom:A})}}G(t,x,n,v,{onAssistantRow:R}),document.body.appendChild(k),window.__agentTutorSetAgent=s=>{if(!s)return;t.agent=s;let m="at_messages_"+s,d="at_session_"+s,w=q(d),I=nt(m,w);n=I.readStored,a=I.appendStored,r=w,l="at_session_token_"+s;let z=c.head.querySelector(".at-head-info");z&&(z.innerHTML="<strong>"+O(t.title)+"</strong><span>"+O(s)+"</span>"),x.innerHTML="",G(t,x,n,v,{onAssistantRow:R});try{localStorage.setItem("agentTutorAgentId",s)}catch(pe){}};try{let s=localStorage.getItem("agentTutorAgentId");s&&t.agent!==s&&((V=window.__agentTutorSetAgent)==null||V.call(window,s))}catch(s){}}document.readyState==="loading"?document.addEventListener("DOMContentLoaded",_t):_t();})();
//# sourceMappingURL=embed.js.map

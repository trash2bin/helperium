"use strict";(()=>{var nt=`/*
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
`;function at(e){var f;let t=u=>{var m;return(m=e==null?void 0:e.getAttribute(u))!=null?m:""},r=window,a=(f=r.__EMBED_CONFIG)!=null?f:r.EMBED_CONFIG,n=(u,m)=>{var b;return t(m)||(a?String((b=a[u])!=null?b:""):"")},i=n("agent","data-agent");i||console.error("[Helperium Widget] Missing data-agent attribute");let o=n("lang","data-lang"),c=navigator.language.startsWith("ru")?"ru":"en";return{agent:i,apiBase:n("apiBase","data-api-base")||window.location.origin,title:n("title","data-title")||"Assistant",greeting:n("greeting","data-greeting")||"How can I help?",accent:n("accent","data-accent")||"#0f766e",position:n("position","data-position")==="left"?"left":"right",lang:o==="ru"||o==="en"?o:c,placeholder:n("placeholder","data-placeholder")||"Ask a question...",width:n("width","data-width")||"min(380px, calc(100vw - 28px))",height:n("height","data-height")||"min(620px, calc(100vh - 44px))",triggerOffsetBottom:n("triggerOffsetBottom","data-trigger-offset-bottom")||"16px",headerColor:n("headerColor","data-header-color"),showHeader:n("showHeader","data-show-header")!=="false",botBubbleColor:n("botBubbleColor","data-bot-bubble-color")||"#eef3f4",botBubbleText:n("botBubbleText","data-bot-bubble-text")||"var(--ink)",voiceInput:n("voiceInput","data-voice-input")!=="false",voiceOutput:n("voiceOutput","data-voice-output")!=="false",voiceToggle:n("voiceToggle","data-voice-toggle")==="classic"?"classic":"telegram"}}var ot=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
</svg>
`;var it=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <line x1="6" y1="12" x2="18" y2="12"/>
</svg>
`;var st=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/>
  <line x1="4" y1="22" x2="4" y2="15"/>
</svg>
`;var lt=`<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="3" width="4" height="8" rx="2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M7 11a5 5 0 0 0 10 0" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M12 16v3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M9 19h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
</svg>
`;var ct=`<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="3" width="4" height="8" rx="2" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M7 11a5 5 0 0 0 10 0" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M12 16v3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M9 19h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
  <line x1="3" y1="3" x2="21" y2="21" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
</svg>
`;var dt=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <line x1="22" y1="2" x2="11" y2="13"/>
  <polygon points="22 2 15 22 11 13 2 9 22 2"/>
</svg>
`;var C={chat:ot,close:it,flag:st,send:dt,mic:lt,micOff:ct,thinking:'<div class="at-thinking-dots"><span></span><span></span><span></span></div>'};function A(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;")}function L(e,t,r){let a=document.createElement(e);return a.className=t,r!==void 0&&(a.innerHTML=r),a}function $(){return!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia)}function pt(e,t){let r=t.position==="left"?"at-left":"at-right",a=L("button","at-trigger "+r,C.chat);a.setAttribute("aria-label",t.lang==="ru"?"\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u0447\u0430\u0442":"Open chat"),a.title=a.getAttribute("aria-label"),e.appendChild(a);let n=L("div","at-panel "+r+" at-hidden");e.appendChild(n);let i=L("div","at-head"),o=L("div","at-head-info");o.innerHTML="<strong>"+A(t.title)+"</strong><span>"+A(t.agent)+"</span>";let c=L("div","at-head-status");c.innerHTML='<span class="at-dot"></span> '+(t.lang==="ru","Online"),o.appendChild(c);let f=L("button","at-close",C.close);f.setAttribute("aria-label",t.lang==="ru"?"\u0417\u0430\u043A\u0440\u044B\u0442\u044C \u0447\u0430\u0442":"Close chat"),i.appendChild(o),i.appendChild(f),n.appendChild(i);let u=L("div","at-messages");n.appendChild(u);let m=document.createElement("form");m.className="at-form";let b=document.createElement("textarea");b.rows=1,b.placeholder=t.placeholder,b.setAttribute("aria-label",t.placeholder),b.style.height="38px";let h=L("button","at-mic-btn",C.mic);h.type="button",h.setAttribute("aria-label",t.lang==="ru"?"\u0417\u0430\u0436\u043C\u0438\u0442\u0435 \u0434\u043B\u044F \u0437\u0430\u043F\u0438\u0441\u0438":"Hold to record"),h.title=h.getAttribute("aria-label");let S=L("button","at-send-btn",C.send);S.type="submit",S.setAttribute("aria-label",t.lang==="ru"?"\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C":"Send");let l=t.voiceToggle==="telegram"&&t.voiceInput&&$(),d=L("div","at-swap-btn "+(l?"at-show-mic":"at-show-send"));d.appendChild(h),d.appendChild(S),!l&&t.voiceInput&&$()&&d.classList.add("at-legacy"),(!t.voiceInput||!$())&&(h.style.display="none");let y=L("div","at-form-row");y.appendChild(b),y.appendChild(d),m.appendChild(y);let M=L("div","at-mic-timer");return m.insertBefore(M,y),n.appendChild(m),{trigger:a,panel:n,messages:u,form:m,textarea:b,closeBtn:f,sendBtn:S,head:i,micBtn:h,micTimer:M,swapBtn:d}}function P(e){return e.classList.contains("at-msg-row")?e.querySelector(".at-msg")||e:(e.classList.contains("at-msg"),e)}function gt(e){let t=e.closest(".at-msg-row");if(t){t.remove();return}e.remove()}function R(e){e&&(e.scrollTop=e.scrollHeight)}function mt(e){return!!(e&&e.scrollHeight-e.scrollTop-e.clientHeight<48)}function _(e){let t=[],r=(e||"").split(`
`),a=0;for(;a<r.length;){let n=r[a];if(ut(r,a)){let o=[];for(;a<r.length&&r[a].trim().charAt(0)==="|";)o.push(r[a]),a++;t.push(_t(o));continue}if(/^\s*[-*]\s+/.test(n)){let o=[];for(;a<r.length&&/^\s*[-*]\s+/.test(r[a]);)o.push(r[a].replace(/^\s*[-*]\s+/,"")),a++;t.push("<ul>"+o.map(c=>"<li>"+F(c)+"</li>").join("")+"</ul>");continue}if(/^\s*\d+\.\s+/.test(n)){let o=[];for(;a<r.length&&/^\s*\d+\.\s+/.test(r[a]);)o.push(r[a].replace(/^\s*\d+\.\s+/,"")),a++;t.push("<ol>"+o.map(c=>"<li>"+F(c)+"</li>").join("")+"</ol>");continue}let i=[];for(;a<r.length&&r[a].trim()&&!ut(r,a)&&!/^\s*[-*]\s+/.test(r[a])&&!/^\s*\d+\.\s+/.test(r[a]);)i.push(r[a]),a++;i.length&&t.push("<p>"+F(i.join(`
`)).replace(/\n/g,"<br>")+"</p>"),a<r.length&&!r[a].trim()&&a++}return t.join("")}function ut(e,t){let r=e[t],a=e[t+1];return!r||!a?!1:r.trim().charAt(0)==="|"&&/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(a)}function _t(e){let t=[];for(let n=0;n<e.length;n++){let i=e[n];if(/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(i))continue;let o=i.trim().replace(/^\|/,"").replace(/\|$/,"").split("|").map(c=>c.trim());t.push(o)}if(!t.length)return"";let r=t[0],a=t.slice(1);return'<div class="at-table-wrap"><table><thead><tr>'+r.map(n=>"<th>"+F(n)+"</th>").join("")+"</tr></thead><tbody>"+a.map(n=>"<tr>"+n.map(i=>"<td>"+F(i)+"</td>").join("")+"</tr>").join("")+"</tbody></table></div>"}var Nt=new Set(["http","https","mailto"]);function zt(e){var a;let t=(a=e.replace(/^\s+/,"").split(/[/?#]/,1)[0])!=null?a:"";if(!t.includes(":"))return!0;let r=t.replace(/\s+/g,"").split(":",1)[0].toLowerCase();return/^[a-z][a-z0-9+.-]*$/.test(r)&&Nt.has(r)}function F(e){return A(e).replace(/\*\*([^*]+)\*\*/g,"<strong>$1</strong>").replace(/\*([^*]+)\*/g,"<em>$1</em>").replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\[([^\]]+)\]\(([^)]+)\)/g,(t,r,a)=>zt(a)?`<a href="${a}" target="_blank" rel="noopener noreferrer">${r}</a>`:r)}function ft(e){let t=e.toLowerCase();return/поиск|найти|find|search/i.test(t)?"\u{1F50D}":/чтение|get|получени/i.test(t)?"\u{1F4CB}":/запрос|query/i.test(t)?"\u{1F4CA}":/list|список/i.test(t)?"\u{1F4CB}":"\u26A1"}function j(e,t){let r=t||{},a=[...new Set(e)];if(!a.length)return null;let n=document.createElement("div");return n.className="at-tool-strip",n.innerHTML=a.map(i=>{let o=r[i]||i;return`<span>${ft(o)} ${A(o)}</span>`}).join(""),n}function q(e,t,r,a){var f;let n=[...new Set(t)];if(!n.length)return;let i=((f=e.closest)==null?void 0:f.call(e,".at-msg-row"))||e,o=i.previousElementSibling;if(o&&o.className==="at-tool-strip"){o.innerHTML=n.map(u=>{let m=r[u]||u;return`<span>${ft(m)} ${A(m)}</span>`}).join("");return}let c=j(n,r);c&&a.insertBefore(c,i)}function bt(e,t){let r=document.createElement("div");r.className="at-msg-row";let a=document.createElement("div");a.className="at-msg at-assistant",t.thinking?(a.dataset.raw="",a.innerHTML=C.thinking):(a.dataset.raw=e||"",a.innerHTML=_(e||""));let n=document.createElement("div");return n.className="at-avatar",n.textContent="AI",r.appendChild(a),r.appendChild(n),t.report!==!1&&t.onAssistantRow&&t.onAssistantRow(r,a),{row:r,node:a}}function ht(e,t,r,a){let n=a||{};if(e==="assistant"){let{row:i}=bt(t,n);n.before?r.insertBefore(i,n.before):r.appendChild(i)}else{let i=document.createElement("div");i.className="at-msg at-user",i.textContent=t||"",n.before?r.insertBefore(i,n.before):r.appendChild(i)}return n.scroll!==!1&&R(r),r.lastElementChild}function U(e,t,r,a,n){let i=r();if(!i.length){a("assistant",e.greeting,{persist:!1,scroll:!1,report:!1});return}t.innerHTML="";let o=[];for(let c of i)if(c.kind==="user")a("user",c.text,{persist:!1,scroll:!1});else if(c.kind==="assistant"){let f=(c.tools||[]).filter(Boolean),u=String(c.text||"");if(!u.trim()&&f.length>0){o=o.concat(f);continue}let m=o.concat(f);if(o=[],m.length>0){let h=j(m);h&&t.appendChild(h)}let{row:b}=bt(u,n!=null&&n.onAssistantRow?{onAssistantRow:n.onAssistantRow}:{});t.appendChild(b)}if(o.length>0){let c=j(o);c&&t.appendChild(c)}R(t)}var W=1e3,Ft=4e3,Wt=500,jt=2048,Vt=20,$t=2e3,J=20,Pt=50;function Y(e){return"at_reported_"+e}function xt(e,t){let r=e+":"+String(t||"").slice(0,200),a=5381;for(let n=0;n<r.length;n++)a=(a<<5)+a+r.charCodeAt(n)|0;return(a>>>0).toString(36)}function qt(e,t){try{let r=sessionStorage.getItem(Y(e.agent)),a=r?JSON.parse(r):[];return Array.isArray(a)&&a.includes(t)}catch(r){return!1}}function Ut(e,t){try{let r=sessionStorage.getItem(Y(e.agent)),a=r?JSON.parse(r):[],n=Array.isArray(a)?a.filter(i=>typeof i=="string"):[];for(n.includes(t)||n.push(t);n.length>Pt;)n.shift();sessionStorage.setItem(Y(e.agent),JSON.stringify(n))}catch(r){}}function vt(e){let t=e.classList.contains("at-error")?"error":"assistant",r=t==="error"?e.textContent||"":e.dataset.raw||"",a=[];try{let i=JSON.parse(e.dataset.tools||"[]");Array.isArray(i)&&(a=i.filter(o=>typeof o=="string"))}catch(i){a=[]}let n=[];try{let i=JSON.parse(e.dataset.displayNames||"{}");i&&typeof i=="object"&&!Array.isArray(i)&&(n=Object.values(i).map(String))}catch(i){n=[]}return{kind:t,text:String(r||"").slice(0,Ft),tools:a,displayNames:n,correlationId:e.dataset.correlationId||""}}function Jt(e,t,r){let a=e.getTranscript().slice(-Vt).map(o=>({kind:o.kind,text:String(o.text||"").slice(0,$t),tools:(o.tools||[]).slice(0,J),ts:typeof o.ts=="number"?new Date(o.ts).toISOString():null})),n={agent:e.config.agent,session_id:e.getSessionId(),lang:e.config.lang,message:{kind:t.kind,text:t.text,tools:t.tools.slice(0,J),display_names:t.displayNames.slice(0,J)},transcript:a},i=r.trim().slice(0,W);i&&(n.comment=i),(t.kind==="error"||t.correlationId)&&(n.last_error={text:t.kind==="error"?t.text.slice(0,Wt):"",correlation_id:t.correlationId||null});try{let o=window.location.href.slice(0,jt);o&&(n.page_url=o)}catch(o){}return n}async function Yt(e,t,r){let a=Jt(e,t,r),n=await fetch(e.config.apiBase+"/api/reports",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(a)});if(!n.ok)throw new Error("report_failed_"+n.status)}function wt(e,t,r){let{config:a}=e;if(r.querySelector(".at-report-btn"))return;let n=document.createElement("button");n.type="button",n.className="at-report-btn",n.innerHTML=C.flag;let i=a.lang==="ru"?"\u041F\u043E\u0436\u0430\u043B\u043E\u0432\u0430\u0442\u044C\u0441\u044F \u043D\u0430 \u044D\u0442\u043E\u0442 \u043E\u0442\u0432\u0435\u0442":"Report this answer";n.setAttribute("aria-label",i),n.title=i;let o=()=>{let c=vt(t);qt(a,xt(c.kind,c.text))?(n.classList.add("at-reported"),n.disabled=!0):(n.classList.remove("at-reported"),n.disabled=!1)};o(),new MutationObserver(o).observe(t,{attributes:!0}),n.addEventListener("click",()=>{n.classList.contains("at-reported")||Xt(e,t,r,n)}),r.firstChild?r.insertBefore(n,r.firstChild):r.appendChild(n)}function Xt(e,t,r,a){var g;let n=e.config.lang==="ru",i=r.closest(".at-panel"),o=r.closest(".at-messages"),c=(i==null?void 0:i.querySelector(".at-report-overlay"))||(o==null?void 0:o.querySelector(".at-report-overlay"))||((g=r.closest(".at-root"))==null?void 0:g.querySelector(".at-report-overlay"));c&&c.remove();let f=vt(t),u=document.createElement("div");u.className="at-report-overlay",u.setAttribute("role","presentation");let m=document.createElement("div");m.className="at-report-modal",m.setAttribute("role","dialog"),m.setAttribute("aria-modal","true"),m.setAttribute("aria-labelledby","at-report-modal-title");let b=document.createElement("div");b.className="at-report-modal-head";let h=document.createElement("div");h.className="at-report-modal-icon",h.innerHTML=C.flag;let S=document.createElement("div"),l=document.createElement("h3");l.className="at-report-modal-title",l.id="at-report-modal-title",l.textContent=n?"\u041F\u043E\u0436\u0430\u043B\u043E\u0432\u0430\u0442\u044C\u0441\u044F \u043D\u0430 \u044D\u0442\u043E\u0442 \u043E\u0442\u0432\u0435\u0442":"Report this answer";let d=document.createElement("p");d.className="at-report-modal-hint",d.textContent=n?"\u041E\u043F\u0438\u0448\u0438\u0442\u0435 \u043F\u0440\u043E\u0431\u043B\u0435\u043C\u0443 \u2014 \u043C\u044B \u043F\u0435\u0440\u0435\u0434\u0430\u0434\u0438\u043C \u043E\u0442\u0437\u044B\u0432 \u043E\u043F\u0435\u0440\u0430\u0442\u043E\u0440\u0443. \u041E\u0442\u0432\u0435\u0442 \u043C\u043E\u0434\u0435\u043B\u0438 \u0438 \u043A\u043E\u043D\u0442\u0435\u043A\u0441\u0442 \u0434\u0438\u0430\u043B\u043E\u0433\u0430 \u0443\u0439\u0434\u0443\u0442 \u0432\u043C\u0435\u0441\u0442\u0435 \u0441 \u0436\u0430\u043B\u043E\u0431\u043E\u0439.":"Tell us what went wrong \u2014 we pass the report to the operator along with the answer and surrounding chat.",S.appendChild(l),S.appendChild(d),b.appendChild(h),b.appendChild(S);let y=document.createElement("p");y.className="at-report-modal-quote-label",y.textContent=n?"\u041E\u0442\u0432\u0435\u0442 \u0430\u0441\u0441\u0438\u0441\u0442\u0435\u043D\u0442\u0430":"Assistant answer";let M=document.createElement("blockquote");M.className="at-report-modal-quote";let v=String(f.text||"").trim();M.textContent=v.length>280?v.slice(0,277)+"\u2026":v;let T=document.createElement("div");T.className="at-report-form";let w=document.createElement("textarea");w.className="at-report-input",w.rows=3,w.maxLength=W,w.placeholder=n?"\u0427\u0442\u043E \u043D\u0435 \u0442\u0430\u043A \u0441 \u044D\u0442\u0438\u043C \u043E\u0442\u0432\u0435\u0442\u043E\u043C? (\u043D\u0435\u043E\u0431\u044F\u0437\u0430\u0442\u0435\u043B\u044C\u043D\u043E)":"What's wrong with this answer? (optional)",w.setAttribute("aria-label",w.placeholder);let D=document.createElement("div");D.className="at-report-counter";let N=()=>{let s=W-w.value.length;D.textContent=n?`\u041E\u0441\u0442\u0430\u043B\u043E\u0441\u044C ${s} \u0441\u0438\u043C\u0432\u043E\u043B\u043E\u0432`:`${s} characters left`,D.classList.toggle("at-report-counter-warn",s<W*.1)};w.addEventListener("input",N),N();let I=document.createElement("div");I.className="at-report-actions";let H=document.createElement("button");H.type="button",H.className="at-report-cancel",H.textContent=n?"\u041E\u0442\u043C\u0435\u043D\u0430":"Cancel";let E=document.createElement("button");E.type="button",E.className="at-report-submit",E.textContent=n?"\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C":"Send",I.appendChild(H),I.appendChild(E),T.appendChild(w),T.appendChild(D),T.appendChild(I),m.appendChild(b),m.appendChild(y),m.appendChild(M),m.appendChild(T),u.appendChild(m);let z=i||o||r.closest(".at-root");if(!z)return;z.appendChild(u),w.focus({preventScroll:!0});let O=()=>{u.remove(),document.removeEventListener("keydown",p)},p=s=>{s.key==="Escape"&&(s.preventDefault(),O())};H.addEventListener("click",O),u.addEventListener("click",s=>{s.target===u&&O()}),document.addEventListener("keydown",p),E.addEventListener("click",()=>{var x;let s=w.value.slice(0,W);E.disabled=!0,E.textContent=n?"\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0430\u2026":"Sending\u2026",H.disabled=!0,w.disabled=!0,(x=m.querySelector(".at-report-error"))==null||x.remove(),Yt(e,f,s).then(()=>{var B;O(),a.classList.add("at-reported"),a.disabled=!0,a.setAttribute("aria-label",n?"\u0416\u0430\u043B\u043E\u0431\u0430 \u043E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u0430":"Report sent"),Ut(e.config,xt(f.kind,f.text));let k=document.createElement("div");k.className="at-report-done",k.textContent=n?"\u0421\u043F\u0430\u0441\u0438\u0431\u043E, \u043E\u0442\u0447\u0451\u0442 \u043E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D.":"Thanks, your report was sent.",(B=r.parentNode)==null||B.insertBefore(k,r.nextSibling),window.setTimeout(()=>k.remove(),4e3)}).catch(()=>{E.disabled=!1,H.disabled=!1,w.disabled=!1,E.textContent=n?"\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C":"Send";let k=document.createElement("div");k.className="at-report-error",k.textContent=n?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C. \u041F\u043E\u043F\u0440\u043E\u0431\u0443\u0439\u0442\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437.":"Failed to send. Please try again.",T.appendChild(k)})})}async function X(e,t,r,a="en"){var u;let n=e.body.getReader(),i=new TextDecoder,o="",c=!1,f=e.headers.get("x-correlation-id")||"";for(;;){let{done:m,value:b}=await n.read();if(m)break;o+=i.decode(b,{stream:!0});let h=o.split(`

`);o=h.pop();for(let S of h){let l=S.split(`
`).find(y=>y.startsWith("data:"));if(!l)continue;let d;try{d=JSON.parse(l.slice(5).trim())}catch(y){continue}switch(t.classList.contains("at-thinking")&&t.classList.remove("at-thinking"),d.type){case"token":r.onToken(d.text||"");break;case"final":c=!0,r.onFinal(d.text||"");break;case"tool_call":{let M=JSON.parse(t.dataset.tools||"[]"),v=JSON.parse(t.dataset.displayNames||"{}");d.name&&!M.includes(d.name)&&(M.push(d.name),t.dataset.tools=JSON.stringify(M)),d.display_name&&d.name&&!v[d.name]&&(v[d.name]=d.display_name,t.dataset.displayNames=JSON.stringify(v)),r.onToolCall(d.name||"",d.display_name);break}case"audio":d.data&&r.onAudio(d.data);break;case"done":if(c=!0,t.classList.contains("at-error"))return;(u=t.dataset.raw)!=null&&u.trim()||r.onFinal(a==="ru"?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u043E\u043B\u0443\u0447\u0438\u0442\u044C \u043E\u0442\u0432\u0435\u0442.":"No response.");let y=[];try{y=JSON.parse(t.dataset.tools||"[]")}catch(M){}r.onDone(t.dataset.raw||"",y),t.dataset.saved="true";break;case"error":c=!0,t.classList.remove("at-thinking"),t.classList.add("at-error"),t.textContent=d.text||(a==="ru"?"\u041F\u0440\u043E\u0438\u0437\u043E\u0448\u043B\u0430 \u043E\u0448\u0438\u0431\u043A\u0430.":"An error occurred."),t.dataset.correlationId=d.correlation_id||f;break}}}c||(f&&(t.dataset.correlationId=f),r.onError(a==="ru"?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u043E\u043B\u0443\u0447\u0438\u0442\u044C \u043E\u0442\u0432\u0435\u0442.":"No response."))}function K(e){let{message:t,targetNode:r,config:a,sessionId:n,messagesEl:i,retryAttempts:o,maxRetries:c,callbacks:f,removeMsgRow:u,scheduleRetry:m,retryChat:b,scrollToBottom:h}=e;r.classList.add("at-thinking");let S=a.apiBase+"/api/chat/"+encodeURIComponent(a.agent);fetch(S,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:t,session_id:n})}).then(l=>{if(l.status===429){r.classList.remove("at-thinking"),u(r);let d=l.headers.get("Retry-After"),y=5;if(d){let T=parseInt(d,10);!isNaN(T)&&T>0&&(y=T)}if(o.set(t,(o.get(t)||0)+1),o.get(t)>=c){let T=document.createElement("div");T.className="at-msg at-assistant at-error",T.innerHTML="\u26A0\uFE0F Server overloaded.";let w=document.createElement("button");w.className="at-retry-btn",w.textContent="Retry",T.appendChild(w),i.appendChild(T),h(i),w.addEventListener("click",()=>{o.delete(t),T.remove(),b(t)});return}let v=document.createElement("div");v.className="at-msg at-assistant",v.textContent="\u26A0\uFE0F "+(a.lang==="ru"?"\u0421\u0435\u0440\u0432\u0435\u0440 \u043F\u0435\u0440\u0435\u0433\u0440\u0443\u0436\u0435\u043D. \u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Server overloaded. Retry in")+" "+y+"s.",i.appendChild(v),h(i),m(t,y*1e3);return}if(!l.ok){r.classList.remove("at-thinking"),r.classList.add("at-error"),r.textContent="Error: "+l.status;let d=l.headers.get("x-correlation-id");d&&(r.dataset.correlationId=d);return}return X(l,r,f,a.lang)}).catch(()=>{r.classList.remove("at-thinking"),r.classList.add("at-error"),r.innerHTML="\u26A0\uFE0F "+(a.lang==="ru"?"\u041D\u0435\u0442 \u0441\u043E\u0435\u0434\u0438\u043D\u0435\u043D\u0438\u044F \u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C.":"No connection to server.")+'<br><button class="at-retry-btn">'+(a.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u044C":"Retry")+"</button>";let l=r.querySelector(".at-retry-btn");l&&l.addEventListener("click",()=>{r.classList.remove("at-error"),r.innerHTML=C.thinking,K(e)})})}function G(e){try{let r=sessionStorage.getItem(e);if(r)return r}catch(r){}let t=typeof crypto!="undefined"&&crypto.randomUUID?crypto.randomUUID():"sess-"+Date.now()+"-"+Math.random().toString(36).slice(2,10);try{sessionStorage.setItem(e,t)}catch(r){}return t}function yt(e){try{let t=sessionStorage.getItem(e);if(!t)return[];let r=JSON.parse(t);return Array.isArray(r)?r:[]}catch(t){return[]}}function kt(e,t){try{sessionStorage.setItem(e,JSON.stringify(t))}catch(r){}}function Q(e,t){function r(){let n=yt(e),i=[];for(let o of n){if(o.sessionId!==t)continue;let c={kind:o.kind,text:o.text,tools:o.tools||[]},f=o.ts;typeof f=="number"&&(c.ts=f),i.push(c)}return i}function a(n,i,o){let c=yt(e);c.push({sessionId:t,kind:n,text:String(i||""),tools:o||[],ts:Date.now()});let f=c.filter(u=>u.sessionId===t);if(f.length>100){let u=f.length-100,m=0,b=c.filter(h=>h.sessionId===t&&m<u?(m++,!1):!0);kt(e,b);return}kt(e,c)}return{readStored:r,appendStored:a}}var Kt=20,Gt=3;function Z(e,t,r){let a=e.dataset.raw||"";e.dataset.raw=a+t,e.dataset.typewriterRunning?e.dataset.typewriterBuffer=(e.dataset.typewriterBuffer||"")+t:(e.dataset.typewriterRunning="1",e.dataset.typewriterBuffer=a+t,e.dataset.typewriterDisplayed=a,Tt(e,r))}function Tt(e,t){let r=e.dataset.typewriterBuffer||"",a=e.dataset.typewriterDisplayed||"";if(a.length>=r.length){e.dataset.typewriterRunning="",e.innerHTML=_(r),R(t);return}let n=Math.min(Gt,r.length-a.length),i=r.slice(0,a.length+n);e.dataset.typewriterDisplayed=i;let o=_(i);if(i.length<r.length)e.classList.add("at-typing-cursor"),e.innerHTML=o;else{e.classList.remove("at-typing-cursor"),e.innerHTML=o,e.dataset.typewriterRunning="",R(t);return}mt(t)&&R(t),setTimeout(()=>Tt(e,t),Kt)}function tt(e,t){e.classList.remove("at-thinking"),e.dataset.raw=t,e.innerHTML=_(t)}var St=120,Qt="audio/webm;codecs=opus",Zt="audio/webm";function Ct(){return{mediaRecorder:null,micChunks:[],micStream:null,micStartTime:0,micDuration:0,micTimerInterval:null}}function Mt(e,t){e.micDuration=Math.floor((Date.now()-e.micStartTime)/1e3);let r=Math.floor(e.micDuration/60),a=e.micDuration%60;t.textContent=(r>0?r+"m ":"")+a+"s"}function V(e,t){if(!(e.mediaRecorder&&e.mediaRecorder.state==="recording")){if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){t.micBtn.classList.add("at-mic-disabled");return}navigator.mediaDevices.getUserMedia({audio:!0}).then(r=>{e.micStream=r,e.micChunks=[];let a=Qt;MediaRecorder.isTypeSupported(a)||(a=Zt),e.mediaRecorder=new MediaRecorder(r,{mimeType:a}),e.mediaRecorder.ondataavailable=n=>{n.data.size>0&&e.micChunks.push(n.data)},e.mediaRecorder.onstop=()=>{r.getTracks().forEach(c=>c.stop()),e.micStream=null,t.micBtn.innerHTML=C.mic,t.micBtn.classList.remove("at-mic-recording"),t.micTimer.classList.remove("at-mic-timer-visible"),e.micTimerInterval&&(clearInterval(e.micTimerInterval),e.micTimerInterval=null);let n=new Blob(e.micChunks,{type:a});if(n.size===0)return;let i=t.micTimer.textContent||e.micDuration+"s";t.addMessage("user","\u{1F3A4} "+i,{persist:!0});let o=t.addMessage("assistant","",{thinking:!0,persist:!1,scroll:!1});t.onStreamVoice(n,o)},e.mediaRecorder.start(),t.micBtn.innerHTML=C.micOff,t.micBtn.classList.add("at-mic-recording"),t.micTimer.classList.add("at-mic-timer-visible"),e.micStartTime=Date.now(),e.micDuration=0,Mt(e,t.micTimer),e.micTimerInterval=window.setInterval(()=>Mt(e,t.micTimer),1e3),St>0&&setTimeout(()=>{e.mediaRecorder&&e.mediaRecorder.state==="recording"&&e.mediaRecorder.stop()},St*1e3)}).catch(r=>{r.name==="NotAllowedError"||r.name==="PermissionDeniedError"?t.addMessage("assistant",t.config.lang==="ru"?"\u274C \u0420\u0430\u0437\u0440\u0435\u0448\u0438\u0442\u0435 \u0434\u043E\u0441\u0442\u0443\u043F \u043A \u043C\u0438\u043A\u0440\u043E\u0444\u043E\u043D\u0443 \u0432 \u043D\u0430\u0441\u0442\u0440\u043E\u0439\u043A\u0430\u0445 \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u0430":"\u274C Please allow microphone access in browser settings",{persist:!1}):t.micBtn.classList.add("at-mic-disabled")})}}function et(e){e.mediaRecorder&&e.mediaRecorder.state==="recording"&&e.mediaRecorder.stop()}function Et(e,t,r){t.classList.add("at-thinking");let a=r.config.apiBase+"/api/chat/voice",n=new FormData;n.append("audio",e,"voice.webm"),n.append("session_id",r.sessionId),n.append("agent",r.config.agent),n.append("lang",r.config.lang),fetch(a,{method:"POST",body:n}).then(i=>{if(i.status===429){t.classList.remove("at-thinking"),t.remove();let o=document.createElement("div");o.className="at-msg at-assistant",o.textContent=r.config.lang==="ru"?"\u26A0\uFE0F \u0421\u0435\u0440\u0432\u0435\u0440 \u043F\u0435\u0440\u0435\u0433\u0440\u0443\u0436\u0435\u043D. \u041F\u043E\u043F\u0440\u043E\u0431\u0443\u0439\u0442\u0435 \u043F\u043E\u0437\u0436\u0435.":"\u26A0\uFE0F Server overloaded. Try again later.",r.messagesEl.appendChild(o),r.scrollToBottom(r.messagesEl);return}if(!i.ok){t.classList.remove("at-thinking"),t.classList.add("at-error"),t.textContent="Error: "+i.status;return}return X(i,t,r.callbacks,r.config.lang)}).catch(()=>{t.classList.remove("at-thinking"),t.classList.add("at-error"),t.innerHTML="\u26A0\uFE0F "+(r.config.lang==="ru"?"\u041E\u0448\u0438\u0431\u043A\u0430 \u0441\u043E\u0435\u0434\u0438\u043D\u0435\u043D\u0438\u044F.":"Connection error.")})}function rt(e){try{let t=atob(e),r=new Uint8Array(t.length);for(let o=0;o<t.length;o++)r[o]=t.charCodeAt(o);let a=new Blob([r],{type:"audio/mpeg"}),n=URL.createObjectURL(a);new Audio(n).play().catch(()=>{})}catch(t){}}var te=3;function ee(){let e=document.currentScript;if(e&&e instanceof HTMLScriptElement)return e;let t=document.querySelector("script[data-agent]");if(t)return t;let r=document.querySelector('script[src*="embed.js"]');return r||null}function re(e){let t=e.replace("#",""),r=parseInt(t.length===3?t.split("").map(a=>a+a).join(""):t,16);return`${r>>16&255}, ${r>>8&255}, ${r&255}`}function ne(e){let t=e.headerColor||e.accent;return`:host {
  --accent: ${e.accent};
  --accent-rgb: ${re(e.accent)};
  --accent-strong: ${e.accent};
  --trigger-offset-bottom: ${e.triggerOffsetBottom};
  --panel-width: ${e.width};
  --panel-height: ${e.height};
  --header-bg: ${t};
  --bot-bubble-bg: ${e.botBubbleColor};
  --bot-bubble-text: ${e.botBubbleText};
}
${e.showHeader?"":".at-head { display: none; }"}`}function Lt(){var z,O;let e=ee(),t=at(e);if(!t.agent)return;let r,a,n,i="at_messages_"+t.agent,o="at_session_"+t.agent;n=G(o),{readStored:r,appendStored:a}=Q(i,n);let c=Ct(),f=new Map,u=document.createElement("div");u.id="helperium-widget-"+t.agent.replace(/[^a-zA-Z0-9_-]/g,"");let m=u.attachShadow({mode:"open"}),b=document.createElement("style");b.textContent=nt,m.appendChild(b);let h=document.createElement("style");h.textContent=ne(t),m.appendChild(h);let S=document.createElement("div");S.className="at-root",m.appendChild(S);let l=pt(S,t),d=l.messages,y={config:t,getSessionId:()=>n,getTranscript:()=>r()},M=(p,g)=>wt(y,g,p);function v(p,g,s){let x=p==="assistant"?{...s,onAssistantRow:M}:{...s},k=ht(p,g,d,x);return s!=null&&s.persist&&a(p,g,s.tools),k}function T(p,g){let s=Math.ceil(g/1e3),x=document.createElement("div");x.className="at-msg at-retry-countdown",x.textContent=(t.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Retry in")+" "+s+"s...",d.appendChild(x),R(d);let k=setInterval(()=>{s--,s<=0?(clearInterval(k),x.remove(),w(p)):x.textContent=(t.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Retry in")+" "+s+"s..."},1e3)}function w(p){let g=v("assistant","",{thinking:!0,persist:!1,scroll:!1}),s=P(g);D(p,s)}function D(p,g){K({message:p,targetNode:g,config:t,sessionId:n,messagesEl:d,retryAttempts:f,maxRetries:te,callbacks:{onToken:s=>Z(g,s,d),onFinal:s=>tt(g,s),onToolCall:(s,x)=>{let k=JSON.parse(g.dataset.tools||"[]"),B=JSON.parse(g.dataset.displayNames||"{}");k.includes(s)||(k.push(s),g.dataset.tools=JSON.stringify(k)),x&&!B[s]&&(B[s]=x,g.dataset.displayNames=JSON.stringify(B)),q(g,k,B,d)},onAudio:s=>rt(s),onDone:(s,x)=>{a("assistant",s,x)},onError:s=>{g.classList.remove("at-thinking"),g.classList.add("at-error"),g.textContent=s}},addMessage:v,removeMsgRow:gt,scheduleRetry:(s,x)=>T(s,x),retryChat:w,scrollToBottom:R})}l.trigger.addEventListener("click",()=>{l.panel.classList.remove("at-hidden"),l.trigger.style.display="none",l.textarea.focus(),R(d)}),l.closeBtn.addEventListener("click",()=>{l.panel.classList.add("at-hidden"),l.trigger.style.display="flex"});function N(){let p=l.textarea.value.trim();if(!p)return;l.textarea.value="",l.textarea.style.height="auto",I(),v("user",p,{persist:!0});let g=v("assistant","",{thinking:!0,persist:!1,scroll:!1}),s=P(g);D(p,s)}l.textarea.addEventListener("input",function(){this.style.height="auto",this.style.height=Math.min(this.scrollHeight,120)+"px",I()}),l.textarea.addEventListener("keydown",p=>{p.key==="Enter"&&!p.shiftKey&&(p.preventDefault(),N())}),l.form.addEventListener("submit",p=>{p.preventDefault(),N()});function I(){if(t.voiceToggle!=="telegram"||!H)return;l.textarea.value.trim().length>0?(l.swapBtn.classList.remove("at-show-mic"),l.swapBtn.classList.add("at-show-send")):(l.swapBtn.classList.remove("at-show-send"),l.swapBtn.classList.add("at-show-mic"))}let H=t.voiceInput&&typeof((z=navigator.mediaDevices)==null?void 0:z.getUserMedia)=="function";if(t.voiceToggle==="telegram"&&H){let p=!1;l.micBtn.addEventListener("mousedown",s=>{s.preventDefault(),!p&&(p=!0,l.micBtn.classList.add("at-mic-holding"),V(c,{micBtn:l.micBtn,micTimer:l.micTimer,config:t,sessionId:n,addMessage:v,onStreamVoice:E(),onStreamChat:D}))});let g=()=>{p&&(p=!1,l.micBtn.classList.remove("at-mic-holding"),et(c))};l.micBtn.addEventListener("mouseup",g),l.micBtn.addEventListener("mouseleave",g),l.micBtn.addEventListener("touchstart",s=>{s.preventDefault(),!p&&(p=!0,l.micBtn.classList.add("at-mic-holding"),V(c,{micBtn:l.micBtn,micTimer:l.micTimer,config:t,sessionId:n,addMessage:v,onStreamVoice:E(),onStreamChat:D}))},{passive:!1}),l.micBtn.addEventListener("touchend",g),l.micBtn.addEventListener("touchcancel",g)}else H&&(l.micBtn.style.display="flex",l.micBtn.addEventListener("mousedown",p=>{p.preventDefault(),l.micBtn.classList.contains("at-mic-recording")?et(c):V(c,{micBtn:l.micBtn,micTimer:l.micTimer,config:t,sessionId:n,addMessage:v,onStreamVoice:E(),onStreamChat:D})}));function E(){return(p,g)=>{Et(p,g,{config:t,sessionId:n,messagesEl:d,callbacks:{onToken:s=>Z(g,s,d),onFinal:s=>tt(g,s),onToolCall:(s,x)=>{q(g,[s],x?{[s]:x}:{},d)},onAudio:s=>rt(s),onDone:(s,x)=>{a("assistant",s,x)},onError:s=>{g.classList.remove("at-thinking"),g.classList.add("at-error"),g.textContent=s}},scrollToBottom:R})}}U(t,d,r,v,{onAssistantRow:M}),document.body.appendChild(u),window.__agentTutorSetAgent=p=>{if(!p)return;t.agent=p;let g="at_messages_"+p,s="at_session_"+p,x=G(s),k=Q(g,x);r=k.readStored,a=k.appendStored,n=x;let B=l.head.querySelector(".at-head-info");B&&(B.innerHTML="<strong>"+A(t.title)+"</strong><span>"+A(p)+"</span>"),d.innerHTML="",U(t,d,r,v,{onAssistantRow:M});try{localStorage.setItem("agentTutorAgentId",p)}catch(ae){}};try{let p=localStorage.getItem("agentTutorAgentId");p&&t.agent!==p&&((O=window.__agentTutorSetAgent)==null||O.call(window,p))}catch(p){}}document.readyState==="loading"?document.addEventListener("DOMContentLoaded",Lt):Lt();})();
//# sourceMappingURL=embed.js.map

"use strict";(()=>{var rt=`/*
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
 * Per-message flag button and the inline report mini-form.
 * The flag lives next to the bubble inside .at-bubble-line so error-path
 * textContent overwrites of the bubble never wipe it.
 */

.at-bubble-line {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  max-width: 100%;
  min-width: 0;
}

.at-report-btn {
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  margin-top: 4px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--ink-light);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  opacity: 0.45;
  transition: opacity 0.15s var(--ease-smooth), background 0.15s var(--ease-smooth);
}

.at-report-btn:hover {
  opacity: 1;
  background: var(--line);
}

.at-report-btn svg {
  width: 14px;
  height: 14px;
}

.at-report-btn.at-reported {
  opacity: 0.25;
  cursor: default;
}

.at-report-form {
  align-self: flex-start;
  width: min(320px, 88%);
  margin: 4px 0 2px;
  padding: 10px;
  border-radius: var(--radius);
  background: var(--glass-bg);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid var(--glass-border);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  gap: 8px;
  animation: at-msg-in 0.25s var(--ease-spring) both;
}

.at-report-input {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 13px;
  font-family: var(--font);
  resize: vertical;
  min-height: 40px;
  box-sizing: border-box;
  background: var(--panel-solid);
  color: var(--ink);
}

.at-report-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.at-report-actions button {
  border: none;
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: var(--font);
}

.at-report-cancel {
  background: transparent;
  color: var(--ink-light);
}

.at-report-submit {
  background: var(--accent);
  color: #ffffff;
}

.at-report-submit:disabled {
  opacity: 0.6;
  cursor: wait;
}

.at-report-error {
  color: var(--rose);
  font-size: 12px;
}

.at-report-done {
  align-self: flex-start;
  margin: 2px 0 2px 14px;
  font-size: 12px;
  color: var(--ink-light);
  animation: at-msg-in 0.25s var(--ease-spring) both;
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
`;function at(e){var g;let t=b=>{var u;return(u=e==null?void 0:e.getAttribute(b))!=null?u:""},n=window,a=(g=n.__EMBED_CONFIG)!=null?g:n.EMBED_CONFIG,r=(b,u)=>{var f;return t(u)||(a?String((f=a[b])!=null?f:""):"")},o=r("agent","data-agent");o||console.error("[Helperium Widget] Missing data-agent attribute");let i=r("lang","data-lang"),s=navigator.language.startsWith("ru")?"ru":"en";return{agent:o,apiBase:r("apiBase","data-api-base")||window.location.origin,title:r("title","data-title")||"Assistant",greeting:r("greeting","data-greeting")||"How can I help?",accent:r("accent","data-accent")||"#0f766e",position:r("position","data-position")==="left"?"left":"right",lang:i==="ru"||i==="en"?i:s,placeholder:r("placeholder","data-placeholder")||"Ask a question...",width:r("width","data-width")||"min(380px, calc(100vw - 28px))",height:r("height","data-height")||"min(620px, calc(100vh - 44px))",triggerOffsetBottom:r("triggerOffsetBottom","data-trigger-offset-bottom")||"16px",headerColor:r("headerColor","data-header-color"),showHeader:r("showHeader","data-show-header")!=="false",botBubbleColor:r("botBubbleColor","data-bot-bubble-color")||"#eef3f4",botBubbleText:r("botBubbleText","data-bot-bubble-text")||"var(--ink)",voiceInput:r("voiceInput","data-voice-input")!=="false",voiceOutput:r("voiceOutput","data-voice-output")!=="false",voiceToggle:r("voiceToggle","data-voice-toggle")==="classic"?"classic":"telegram"}}var it=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
</svg>
`;var ot=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
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
`;var T={chat:it,close:ot,flag:st,send:dt,mic:lt,micOff:ct,thinking:'<div class="at-thinking-dots"><span></span><span></span><span></span></div>'};function E(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;")}function S(e,t,n){let a=document.createElement(e);return a.className=t,n!==void 0&&(a.innerHTML=n),a}function z(){return!!(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia)}function pt(e,t){let n=t.position==="left"?"at-left":"at-right",a=S("button","at-trigger "+n,T.chat);a.setAttribute("aria-label",t.lang==="ru"?"\u041E\u0442\u043A\u0440\u044B\u0442\u044C \u0447\u0430\u0442":"Open chat"),a.title=a.getAttribute("aria-label"),e.appendChild(a);let r=S("div","at-panel "+n+" at-hidden");e.appendChild(r);let o=S("div","at-head"),i=S("div","at-head-info");i.innerHTML="<strong>"+E(t.title)+"</strong><span>"+E(t.agent)+"</span>";let s=S("div","at-head-status");s.innerHTML='<span class="at-dot"></span> '+(t.lang==="ru","Online"),i.appendChild(s);let g=S("button","at-close",T.close);g.setAttribute("aria-label",t.lang==="ru"?"\u0417\u0430\u043A\u0440\u044B\u0442\u044C \u0447\u0430\u0442":"Close chat"),o.appendChild(i),o.appendChild(g),r.appendChild(o);let b=S("div","at-messages");r.appendChild(b);let u=document.createElement("form");u.className="at-form";let f=document.createElement("textarea");f.rows=1,f.placeholder=t.placeholder,f.setAttribute("aria-label",t.placeholder),f.style.height="38px";let x=S("button","at-mic-btn",T.mic);x.type="button",x.setAttribute("aria-label",t.lang==="ru"?"\u0417\u0430\u0436\u043C\u0438\u0442\u0435 \u0434\u043B\u044F \u0437\u0430\u043F\u0438\u0441\u0438":"Hold to record"),x.title=x.getAttribute("aria-label");let k=S("button","at-send-btn",T.send);k.type="submit",k.setAttribute("aria-label",t.lang==="ru"?"\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C":"Send");let l=t.voiceToggle==="telegram"&&t.voiceInput&&z(),d=S("div","at-swap-btn "+(l?"at-show-mic":"at-show-send"));d.appendChild(x),d.appendChild(k),!l&&t.voiceInput&&z()&&d.classList.add("at-legacy"),(!t.voiceInput||!z())&&(x.style.display="none");let h=S("div","at-form-row");h.appendChild(f),h.appendChild(d),u.appendChild(h);let y=S("div","at-mic-timer");return u.insertBefore(y,h),r.appendChild(u),{trigger:a,panel:r,messages:b,form:u,textarea:f,closeBtn:g,sendBtn:k,head:o,micBtn:x,micTimer:y,swapBtn:d}}function F(e){return e.classList.contains("at-msg-row")?e.querySelector(".at-msg")||e:(e.classList.contains("at-msg"),e)}function gt(e){let t=e.closest(".at-msg-row");if(t){t.remove();return}e.remove()}function M(e){e&&(e.scrollTop=e.scrollHeight)}function mt(e){return!!(e&&e.scrollHeight-e.scrollTop-e.clientHeight<48)}function A(e){let t=[],n=(e||"").split(`
`),a=0;for(;a<n.length;){let r=n[a];if(ut(n,a)){let i=[];for(;a<n.length&&n[a].trim().charAt(0)==="|";)i.push(n[a]),a++;t.push(Ot(i));continue}if(/^\s*[-*]\s+/.test(r)){let i=[];for(;a<n.length&&/^\s*[-*]\s+/.test(n[a]);)i.push(n[a].replace(/^\s*[-*]\s+/,"")),a++;t.push("<ul>"+i.map(s=>"<li>"+D(s)+"</li>").join("")+"</ul>");continue}if(/^\s*\d+\.\s+/.test(r)){let i=[];for(;a<n.length&&/^\s*\d+\.\s+/.test(n[a]);)i.push(n[a].replace(/^\s*\d+\.\s+/,"")),a++;t.push("<ol>"+i.map(s=>"<li>"+D(s)+"</li>").join("")+"</ol>");continue}let o=[];for(;a<n.length&&n[a].trim()&&!ut(n,a)&&!/^\s*[-*]\s+/.test(n[a])&&!/^\s*\d+\.\s+/.test(n[a]);)o.push(n[a]),a++;o.length&&t.push("<p>"+D(o.join(`
`)).replace(/\n/g,"<br>")+"</p>"),a<n.length&&!n[a].trim()&&a++}return t.join("")}function ut(e,t){let n=e[t],a=e[t+1];return!n||!a?!1:n.trim().charAt(0)==="|"&&/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(a)}function Ot(e){let t=[];for(let r=0;r<e.length;r++){let o=e[r];if(/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(o))continue;let i=o.trim().replace(/^\|/,"").replace(/\|$/,"").split("|").map(s=>s.trim());t.push(i)}if(!t.length)return"";let n=t[0],a=t.slice(1);return'<div class="at-table-wrap"><table><thead><tr>'+n.map(r=>"<th>"+D(r)+"</th>").join("")+"</tr></thead><tbody>"+a.map(r=>"<tr>"+r.map(o=>"<td>"+D(o)+"</td>").join("")+"</tr>").join("")+"</tbody></table></div>"}var _t=new Set(["http","https","mailto"]);function Nt(e){var a;let t=(a=e.replace(/^\s+/,"").split(/[/?#]/,1)[0])!=null?a:"";if(!t.includes(":"))return!0;let n=t.replace(/\s+/g,"").split(":",1)[0].toLowerCase();return/^[a-z][a-z0-9+.-]*$/.test(n)&&_t.has(n)}function D(e){return E(e).replace(/\*\*([^*]+)\*\*/g,"<strong>$1</strong>").replace(/\*([^*]+)\*/g,"<em>$1</em>").replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\[([^\]]+)\]\(([^)]+)\)/g,(t,n,a)=>Nt(a)?`<a href="${a}" target="_blank" rel="noopener noreferrer">${n}</a>`:n)}function ft(e){let t=e.toLowerCase();return/поиск|найти|find|search/i.test(t)?"\u{1F50D}":/чтение|get|получени/i.test(t)?"\u{1F4CB}":/запрос|query/i.test(t)?"\u{1F4CA}":/list|список/i.test(t)?"\u{1F4CB}":"\u26A1"}function I(e,t){let n=t||{},a=[...new Set(e)];if(!a.length)return null;let r=document.createElement("div");return r.className="at-tool-strip",r.innerHTML=a.map(o=>{let i=n[o]||o;return`<span>${ft(i)} ${E(i)}</span>`}).join(""),r}function W(e,t,n,a){var g;let r=[...new Set(t)];if(!r.length)return;let o=((g=e.closest)==null?void 0:g.call(e,".at-msg-row"))||e,i=o.previousElementSibling;if(i&&i.className==="at-tool-strip"){i.innerHTML=r.map(b=>{let u=n[b]||b;return`<span>${ft(u)} ${E(u)}</span>`}).join("");return}let s=I(r,n);s&&a.insertBefore(s,o)}function bt(e,t){let n=document.createElement("div");n.className="at-msg-row";let a=document.createElement("div");a.className="at-msg at-assistant",t.thinking?(a.dataset.raw="",a.innerHTML=T.thinking):(a.dataset.raw=e||"",a.innerHTML=A(e||""));let r=document.createElement("div");return r.className="at-avatar",r.textContent="AI",n.appendChild(a),n.appendChild(r),!t.thinking&&t.report!==!1&&t.onAssistantRow&&t.onAssistantRow(n,a),{row:n,node:a}}function ht(e,t,n,a){let r=a||{};if(e==="assistant"){let{row:o}=bt(t,r);r.before?n.insertBefore(o,r.before):n.appendChild(o)}else{let o=document.createElement("div");o.className="at-msg at-user",o.textContent=t||"",r.before?n.insertBefore(o,r.before):n.appendChild(o)}return r.scroll!==!1&&M(n),n.lastElementChild}function j(e,t,n,a,r){let o=n();if(!o.length){a("assistant",e.greeting,{persist:!1,scroll:!1,report:!1});return}t.innerHTML="";let i=[];for(let s of o)if(s.kind==="user")a("user",s.text,{persist:!1,scroll:!1});else if(s.kind==="assistant"){let g=(s.tools||[]).filter(Boolean),b=String(s.text||"");if(!b.trim()&&g.length>0){i=i.concat(g);continue}let u=i.concat(g);if(i=[],u.length>0){let x=I(u);x&&t.appendChild(x)}let{row:f}=bt(b,r!=null&&r.onAssistantRow?{onAssistantRow:r.onAssistantRow}:{});t.appendChild(f)}if(i.length>0){let s=I(i);s&&t.appendChild(s)}M(t)}var $=1e3,zt=4e3,Ft=500,Wt=2048,jt=20,Vt=2e3,V=20,$t=50;function P(e){return"at_reported_"+e}function xt(e,t){let n=e+":"+String(t||"").slice(0,200),a=5381;for(let r=0;r<n.length;r++)a=(a<<5)+a+n.charCodeAt(r)|0;return(a>>>0).toString(36)}function Pt(e,t){try{let n=sessionStorage.getItem(P(e.agent)),a=n?JSON.parse(n):[];return Array.isArray(a)&&a.includes(t)}catch(n){return!1}}function Ut(e,t){try{let n=sessionStorage.getItem(P(e.agent)),a=n?JSON.parse(n):[],r=Array.isArray(a)?a.filter(o=>typeof o=="string"):[];for(r.includes(t)||r.push(t);r.length>$t;)r.shift();sessionStorage.setItem(P(e.agent),JSON.stringify(r))}catch(n){}}function Jt(e){let t=e.classList.contains("at-error")?"error":"assistant",n=t==="error"?e.textContent||"":e.dataset.raw||"",a=[];try{let o=JSON.parse(e.dataset.tools||"[]");Array.isArray(o)&&(a=o.filter(i=>typeof i=="string"))}catch(o){a=[]}let r=[];try{let o=JSON.parse(e.dataset.displayNames||"{}");o&&typeof o=="object"&&!Array.isArray(o)&&(r=Object.values(o).map(String))}catch(o){r=[]}return{kind:t,text:String(n||"").slice(0,zt),tools:a,displayNames:r,correlationId:e.dataset.correlationId||""}}function Xt(e,t,n){let a=e.getTranscript().slice(-jt).map(i=>({kind:i.kind,text:String(i.text||"").slice(0,Vt),tools:(i.tools||[]).slice(0,V),ts:typeof i.ts=="number"?new Date(i.ts).toISOString():null})),r={agent:e.config.agent,session_id:e.getSessionId(),lang:e.config.lang,message:{kind:t.kind,text:t.text,tools:t.tools.slice(0,V),display_names:t.displayNames.slice(0,V)},transcript:a},o=n.trim().slice(0,$);o&&(r.comment=o),(t.kind==="error"||t.correlationId)&&(r.last_error={text:t.kind==="error"?t.text.slice(0,Ft):"",correlation_id:t.correlationId||null});try{let i=window.location.href.slice(0,Wt);i&&(r.page_url=i)}catch(i){}return r}async function Yt(e,t,n){let a=Xt(e,t,n),r=await fetch(e.config.apiBase+"/api/reports",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(a)});if(!r.ok)throw new Error("report_failed_"+r.status)}function vt(e,t,n){let{config:a}=e;if(n.querySelector(".at-report-btn"))return;let r=document.createElement("button");r.type="button",r.className="at-report-btn",r.innerHTML=T.flag;let o=a.lang==="ru"?"\u041F\u043E\u0436\u0430\u043B\u043E\u0432\u0430\u0442\u044C\u0441\u044F \u043D\u0430 \u044D\u0442\u043E\u0442 \u043E\u0442\u0432\u0435\u0442":"Report this answer";r.setAttribute("aria-label",o),r.title=o;let i=xt("assistant",t.dataset.raw||"");Pt(a,i)&&(r.classList.add("at-reported"),r.disabled=!0),r.addEventListener("click",()=>{r.classList.contains("at-reported")||qt(e,t,n,r)});let s=document.createElement("div");s.className="at-bubble-line";let g=t.parentNode;g?(g.insertBefore(s,t),s.appendChild(t),s.appendChild(r)):(n.insertBefore(s,n.firstChild),s.appendChild(t),s.appendChild(r))}function qt(e,t,n,a){var x;let r=e.config.lang==="ru",o=n.closest(".at-messages"),i=o==null?void 0:o.querySelector(".at-report-form");i&&i.remove();let s=document.createElement("div");s.className="at-report-form";let g=document.createElement("textarea");g.className="at-report-input",g.rows=2,g.maxLength=$,g.placeholder=r?"\u0427\u0442\u043E \u043D\u0435 \u0442\u0430\u043A? (\u043D\u0435\u043E\u0431\u044F\u0437\u0430\u0442\u0435\u043B\u044C\u043D\u043E)":"What's wrong? (optional)",g.setAttribute("aria-label",g.placeholder);let b=document.createElement("div");b.className="at-report-actions";let u=document.createElement("button");u.type="button",u.className="at-report-cancel",u.textContent=r?"\u041E\u0442\u043C\u0435\u043D\u0430":"Cancel";let f=document.createElement("button");f.type="button",f.className="at-report-submit",f.textContent=r?"\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C":"Send",b.appendChild(u),b.appendChild(f),s.appendChild(g),s.appendChild(b),(x=n.parentNode)==null||x.insertBefore(s,n.nextSibling),g.focus(),u.addEventListener("click",()=>s.remove()),f.addEventListener("click",()=>{var d;let k=g.value.slice(0,$),l=Jt(t);f.disabled=!0,f.textContent=r?"\u041E\u0442\u043F\u0440\u0430\u0432\u043A\u0430\u2026":"Sending\u2026",(d=s.querySelector(".at-report-error"))==null||d.remove(),Yt(e,l,k).then(()=>{var y;s.remove(),a.classList.add("at-reported"),a.disabled=!0,a.setAttribute("aria-label",r?"\u0416\u0430\u043B\u043E\u0431\u0430 \u043E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D\u0430":"Report sent"),Ut(e.config,xt("assistant",t.dataset.raw||""));let h=document.createElement("div");h.className="at-report-done",h.textContent=r?"\u0421\u043F\u0430\u0441\u0438\u0431\u043E, \u043E\u0442\u0447\u0451\u0442 \u043E\u0442\u043F\u0440\u0430\u0432\u043B\u0435\u043D.":"Thanks, your report was sent.",(y=n.parentNode)==null||y.insertBefore(h,n.nextSibling),window.setTimeout(()=>h.remove(),4e3)}).catch(()=>{f.disabled=!1,f.textContent=r?"\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C":"Send";let h=document.createElement("div");h.className="at-report-error",h.textContent=r?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C. \u041F\u043E\u043F\u0440\u043E\u0431\u0443\u0439\u0442\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437.":"Failed to send. Please try again.",s.appendChild(h)})})}async function U(e,t,n,a="en"){var b;let r=e.body.getReader(),o=new TextDecoder,i="",s=!1,g=e.headers.get("x-correlation-id")||"";for(;;){let{done:u,value:f}=await r.read();if(u)break;i+=o.decode(f,{stream:!0});let x=i.split(`

`);i=x.pop();for(let k of x){let l=k.split(`
`).find(h=>h.startsWith("data:"));if(!l)continue;let d;try{d=JSON.parse(l.slice(5).trim())}catch(h){continue}switch(t.classList.contains("at-thinking")&&t.classList.remove("at-thinking"),d.type){case"token":n.onToken(d.text||"");break;case"final":s=!0,n.onFinal(d.text||"");break;case"tool_call":{let y=JSON.parse(t.dataset.tools||"[]"),w=JSON.parse(t.dataset.displayNames||"{}");d.name&&!y.includes(d.name)&&(y.push(d.name),t.dataset.tools=JSON.stringify(y)),d.display_name&&d.name&&!w[d.name]&&(w[d.name]=d.display_name,t.dataset.displayNames=JSON.stringify(w)),n.onToolCall(d.name||"",d.display_name);break}case"audio":d.data&&n.onAudio(d.data);break;case"done":if(s=!0,t.classList.contains("at-error"))return;(b=t.dataset.raw)!=null&&b.trim()||n.onFinal(a==="ru"?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u043E\u043B\u0443\u0447\u0438\u0442\u044C \u043E\u0442\u0432\u0435\u0442.":"No response.");let h=[];try{h=JSON.parse(t.dataset.tools||"[]")}catch(y){}n.onDone(t.dataset.raw||"",h),t.dataset.saved="true";break;case"error":s=!0,t.classList.remove("at-thinking"),t.classList.add("at-error"),t.textContent=d.text||(a==="ru"?"\u041F\u0440\u043E\u0438\u0437\u043E\u0448\u043B\u0430 \u043E\u0448\u0438\u0431\u043A\u0430.":"An error occurred."),t.dataset.correlationId=d.correlation_id||g;break}}}s||(g&&(t.dataset.correlationId=g),n.onError(a==="ru"?"\u041D\u0435 \u0443\u0434\u0430\u043B\u043E\u0441\u044C \u043F\u043E\u043B\u0443\u0447\u0438\u0442\u044C \u043E\u0442\u0432\u0435\u0442.":"No response."))}function J(e){let{message:t,targetNode:n,config:a,sessionId:r,messagesEl:o,retryAttempts:i,maxRetries:s,callbacks:g,removeMsgRow:b,scheduleRetry:u,retryChat:f,scrollToBottom:x}=e;n.classList.add("at-thinking");let k=a.apiBase+"/api/chat/"+encodeURIComponent(a.agent);fetch(k,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:t,session_id:r})}).then(l=>{if(l.status===429){n.classList.remove("at-thinking"),b(n);let d=l.headers.get("Retry-After"),h=5;if(d){let C=parseInt(d,10);!isNaN(C)&&C>0&&(h=C)}if(i.set(t,(i.get(t)||0)+1),i.get(t)>=s){let C=document.createElement("div");C.className="at-msg at-assistant at-error",C.innerHTML="\u26A0\uFE0F Server overloaded.";let R=document.createElement("button");R.className="at-retry-btn",R.textContent="Retry",C.appendChild(R),o.appendChild(C),x(o),R.addEventListener("click",()=>{i.delete(t),C.remove(),f(t)});return}let w=document.createElement("div");w.className="at-msg at-assistant",w.textContent="\u26A0\uFE0F "+(a.lang==="ru"?"\u0421\u0435\u0440\u0432\u0435\u0440 \u043F\u0435\u0440\u0435\u0433\u0440\u0443\u0436\u0435\u043D. \u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Server overloaded. Retry in")+" "+h+"s.",o.appendChild(w),x(o),u(t,h*1e3);return}if(!l.ok){n.classList.remove("at-thinking"),n.classList.add("at-error"),n.textContent="Error: "+l.status;let d=l.headers.get("x-correlation-id");d&&(n.dataset.correlationId=d);return}return U(l,n,g,a.lang)}).catch(()=>{n.classList.remove("at-thinking"),n.classList.add("at-error"),n.innerHTML="\u26A0\uFE0F "+(a.lang==="ru"?"\u041D\u0435\u0442 \u0441\u043E\u0435\u0434\u0438\u043D\u0435\u043D\u0438\u044F \u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u043E\u043C.":"No connection to server.")+'<br><button class="at-retry-btn">'+(a.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440\u0438\u0442\u044C":"Retry")+"</button>";let l=n.querySelector(".at-retry-btn");l&&l.addEventListener("click",()=>{n.classList.remove("at-error"),n.innerHTML=T.thinking,J(e)})})}function X(e){try{let n=sessionStorage.getItem(e);if(n)return n}catch(n){}let t=typeof crypto!="undefined"&&crypto.randomUUID?crypto.randomUUID():"sess-"+Date.now()+"-"+Math.random().toString(36).slice(2,10);try{sessionStorage.setItem(e,t)}catch(n){}return t}function wt(e){try{let t=sessionStorage.getItem(e);if(!t)return[];let n=JSON.parse(t);return Array.isArray(n)?n:[]}catch(t){return[]}}function yt(e,t){try{sessionStorage.setItem(e,JSON.stringify(t))}catch(n){}}function Y(e,t){function n(){let r=wt(e),o=[];for(let i of r){if(i.sessionId!==t)continue;let s={kind:i.kind,text:i.text,tools:i.tools||[]},g=i.ts;typeof g=="number"&&(s.ts=g),o.push(s)}return o}function a(r,o,i){let s=wt(e);s.push({sessionId:t,kind:r,text:String(o||""),tools:i||[],ts:Date.now()});let g=s.filter(b=>b.sessionId===t);if(g.length>100){let b=g.length-100,u=0,f=s.filter(x=>x.sessionId===t&&u<b?(u++,!1):!0);yt(e,f);return}yt(e,s)}return{readStored:n,appendStored:a}}var Kt=20,Gt=3;function q(e,t,n){let a=e.dataset.raw||"";e.dataset.raw=a+t,e.dataset.typewriterRunning?e.dataset.typewriterBuffer=(e.dataset.typewriterBuffer||"")+t:(e.dataset.typewriterRunning="1",e.dataset.typewriterBuffer=a+t,e.dataset.typewriterDisplayed=a,kt(e,n))}function kt(e,t){let n=e.dataset.typewriterBuffer||"",a=e.dataset.typewriterDisplayed||"";if(a.length>=n.length){e.dataset.typewriterRunning="",e.innerHTML=A(n),M(t);return}let r=Math.min(Gt,n.length-a.length),o=n.slice(0,a.length+r);e.dataset.typewriterDisplayed=o;let i=A(o);if(o.length<n.length)e.classList.add("at-typing-cursor"),e.innerHTML=i;else{e.classList.remove("at-typing-cursor"),e.innerHTML=i,e.dataset.typewriterRunning="",M(t);return}mt(t)&&M(t),setTimeout(()=>kt(e,t),Kt)}function K(e,t){e.classList.remove("at-thinking"),e.dataset.raw=t,e.innerHTML=A(t)}var Tt=120,Zt="audio/webm;codecs=opus",Qt="audio/webm";function Mt(){return{mediaRecorder:null,micChunks:[],micStream:null,micStartTime:0,micDuration:0,micTimerInterval:null}}function St(e,t){e.micDuration=Math.floor((Date.now()-e.micStartTime)/1e3);let n=Math.floor(e.micDuration/60),a=e.micDuration%60;t.textContent=(n>0?n+"m ":"")+a+"s"}function O(e,t){if(!(e.mediaRecorder&&e.mediaRecorder.state==="recording")){if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){t.micBtn.classList.add("at-mic-disabled");return}navigator.mediaDevices.getUserMedia({audio:!0}).then(n=>{e.micStream=n,e.micChunks=[];let a=Zt;MediaRecorder.isTypeSupported(a)||(a=Qt),e.mediaRecorder=new MediaRecorder(n,{mimeType:a}),e.mediaRecorder.ondataavailable=r=>{r.data.size>0&&e.micChunks.push(r.data)},e.mediaRecorder.onstop=()=>{n.getTracks().forEach(s=>s.stop()),e.micStream=null,t.micBtn.innerHTML=T.mic,t.micBtn.classList.remove("at-mic-recording"),t.micTimer.classList.remove("at-mic-timer-visible"),e.micTimerInterval&&(clearInterval(e.micTimerInterval),e.micTimerInterval=null);let r=new Blob(e.micChunks,{type:a});if(r.size===0)return;let o=t.micTimer.textContent||e.micDuration+"s";t.addMessage("user","\u{1F3A4} "+o,{persist:!0});let i=t.addMessage("assistant","",{thinking:!0,persist:!1,scroll:!1});t.onStreamVoice(r,i)},e.mediaRecorder.start(),t.micBtn.innerHTML=T.micOff,t.micBtn.classList.add("at-mic-recording"),t.micTimer.classList.add("at-mic-timer-visible"),e.micStartTime=Date.now(),e.micDuration=0,St(e,t.micTimer),e.micTimerInterval=window.setInterval(()=>St(e,t.micTimer),1e3),Tt>0&&setTimeout(()=>{e.mediaRecorder&&e.mediaRecorder.state==="recording"&&e.mediaRecorder.stop()},Tt*1e3)}).catch(n=>{n.name==="NotAllowedError"||n.name==="PermissionDeniedError"?t.addMessage("assistant",t.config.lang==="ru"?"\u274C \u0420\u0430\u0437\u0440\u0435\u0448\u0438\u0442\u0435 \u0434\u043E\u0441\u0442\u0443\u043F \u043A \u043C\u0438\u043A\u0440\u043E\u0444\u043E\u043D\u0443 \u0432 \u043D\u0430\u0441\u0442\u0440\u043E\u0439\u043A\u0430\u0445 \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u0430":"\u274C Please allow microphone access in browser settings",{persist:!1}):t.micBtn.classList.add("at-mic-disabled")})}}function G(e){e.mediaRecorder&&e.mediaRecorder.state==="recording"&&e.mediaRecorder.stop()}function Ct(e,t,n){t.classList.add("at-thinking");let a=n.config.apiBase+"/api/chat/voice",r=new FormData;r.append("audio",e,"voice.webm"),r.append("session_id",n.sessionId),r.append("agent",n.config.agent),r.append("lang",n.config.lang),fetch(a,{method:"POST",body:r}).then(o=>{if(o.status===429){t.classList.remove("at-thinking"),t.remove();let i=document.createElement("div");i.className="at-msg at-assistant",i.textContent=n.config.lang==="ru"?"\u26A0\uFE0F \u0421\u0435\u0440\u0432\u0435\u0440 \u043F\u0435\u0440\u0435\u0433\u0440\u0443\u0436\u0435\u043D. \u041F\u043E\u043F\u0440\u043E\u0431\u0443\u0439\u0442\u0435 \u043F\u043E\u0437\u0436\u0435.":"\u26A0\uFE0F Server overloaded. Try again later.",n.messagesEl.appendChild(i),n.scrollToBottom(n.messagesEl);return}if(!o.ok){t.classList.remove("at-thinking"),t.classList.add("at-error"),t.textContent="Error: "+o.status;return}return U(o,t,n.callbacks,n.config.lang)}).catch(()=>{t.classList.remove("at-thinking"),t.classList.add("at-error"),t.innerHTML="\u26A0\uFE0F "+(n.config.lang==="ru"?"\u041E\u0448\u0438\u0431\u043A\u0430 \u0441\u043E\u0435\u0434\u0438\u043D\u0435\u043D\u0438\u044F.":"Connection error.")})}function Z(e){try{let t=atob(e),n=new Uint8Array(t.length);for(let i=0;i<t.length;i++)n[i]=t.charCodeAt(i);let a=new Blob([n],{type:"audio/mpeg"}),r=URL.createObjectURL(a);new Audio(r).play().catch(()=>{})}catch(t){}}var te=3;function ee(){let e=document.currentScript;if(e&&e instanceof HTMLScriptElement)return e;let t=document.querySelector("script[data-agent]");if(t)return t;let n=document.querySelector('script[src*="embed.js"]');return n||null}function ne(e){let t=e.replace("#",""),n=parseInt(t.length===3?t.split("").map(a=>a+a).join(""):t,16);return`${n>>16&255}, ${n>>8&255}, ${n&255}`}function re(e){let t=e.headerColor||e.accent;return`:host {
  --accent: ${e.accent};
  --accent-rgb: ${ne(e.accent)};
  --accent-strong: ${e.accent};
  --trigger-offset-bottom: ${e.triggerOffsetBottom};
  --panel-width: ${e.width};
  --panel-height: ${e.height};
  --header-bg: ${t};
  --bot-bubble-bg: ${e.botBubbleColor};
  --bot-bubble-text: ${e.botBubbleText};
}
${e.showHeader?"":".at-head { display: none; }"}`}function Lt(){var et,nt;let e=ee(),t=at(e);if(!t.agent)return;let n,a,r,o="at_messages_"+t.agent,i="at_session_"+t.agent;r=X(i),{readStored:n,appendStored:a}=Y(o,r);let s=Mt(),g=new Map,b=document.createElement("div");b.id="helperium-widget-"+t.agent.replace(/[^a-zA-Z0-9_-]/g,"");let u=b.attachShadow({mode:"open"}),f=document.createElement("style");f.textContent=rt,u.appendChild(f);let x=document.createElement("style");x.textContent=re(t),u.appendChild(x);let k=document.createElement("div");k.className="at-root",u.appendChild(k);let l=pt(k,t),d=l.messages,h={config:t,getSessionId:()=>r,getTranscript:()=>n()},y=(p,m)=>vt(h,m,p);function w(p,m,c){let v=p==="assistant"?{...c,onAssistantRow:y}:{...c},L=ht(p,m,d,v);return c!=null&&c.persist&&a(p,m,c.tools),L}function C(p,m){let c=Math.ceil(m/1e3),v=document.createElement("div");v.className="at-msg at-retry-countdown",v.textContent=(t.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Retry in")+" "+c+"s...",d.appendChild(v),M(d);let L=setInterval(()=>{c--,c<=0?(clearInterval(L),v.remove(),R(p)):v.textContent=(t.lang==="ru"?"\u041F\u043E\u0432\u0442\u043E\u0440 \u0447\u0435\u0440\u0435\u0437":"Retry in")+" "+c+"s..."},1e3)}function R(p){let m=w("assistant","",{thinking:!0,persist:!1,scroll:!1}),c=F(m);B(p,c)}function B(p,m){J({message:p,targetNode:m,config:t,sessionId:r,messagesEl:d,retryAttempts:g,maxRetries:te,callbacks:{onToken:c=>q(m,c,d),onFinal:c=>K(m,c),onToolCall:(c,v)=>{let L=JSON.parse(m.dataset.tools||"[]"),H=JSON.parse(m.dataset.displayNames||"{}");L.includes(c)||(L.push(c),m.dataset.tools=JSON.stringify(L)),v&&!H[c]&&(H[c]=v,m.dataset.displayNames=JSON.stringify(H)),W(m,L,H,d)},onAudio:c=>Z(c),onDone:(c,v)=>{a("assistant",c,v)},onError:c=>{m.classList.remove("at-thinking"),m.classList.add("at-error"),m.textContent=c}},addMessage:w,removeMsgRow:gt,scheduleRetry:(c,v)=>C(c,v),retryChat:R,scrollToBottom:M})}l.trigger.addEventListener("click",()=>{l.panel.classList.remove("at-hidden"),l.trigger.style.display="none",l.textarea.focus(),M(d)}),l.closeBtn.addEventListener("click",()=>{l.panel.classList.add("at-hidden"),l.trigger.style.display="flex"});function Q(){let p=l.textarea.value.trim();if(!p)return;l.textarea.value="",l.textarea.style.height="auto",tt(),w("user",p,{persist:!0});let m=w("assistant","",{thinking:!0,persist:!1,scroll:!1}),c=F(m);B(p,c)}l.textarea.addEventListener("input",function(){this.style.height="auto",this.style.height=Math.min(this.scrollHeight,120)+"px",tt()}),l.textarea.addEventListener("keydown",p=>{p.key==="Enter"&&!p.shiftKey&&(p.preventDefault(),Q())}),l.form.addEventListener("submit",p=>{p.preventDefault(),Q()});function tt(){if(t.voiceToggle!=="telegram"||!_)return;l.textarea.value.trim().length>0?(l.swapBtn.classList.remove("at-show-mic"),l.swapBtn.classList.add("at-show-send")):(l.swapBtn.classList.remove("at-show-send"),l.swapBtn.classList.add("at-show-mic"))}let _=t.voiceInput&&typeof((et=navigator.mediaDevices)==null?void 0:et.getUserMedia)=="function";if(t.voiceToggle==="telegram"&&_){let p=!1;l.micBtn.addEventListener("mousedown",c=>{c.preventDefault(),!p&&(p=!0,l.micBtn.classList.add("at-mic-holding"),O(s,{micBtn:l.micBtn,micTimer:l.micTimer,config:t,sessionId:r,addMessage:w,onStreamVoice:N(),onStreamChat:B}))});let m=()=>{p&&(p=!1,l.micBtn.classList.remove("at-mic-holding"),G(s))};l.micBtn.addEventListener("mouseup",m),l.micBtn.addEventListener("mouseleave",m),l.micBtn.addEventListener("touchstart",c=>{c.preventDefault(),!p&&(p=!0,l.micBtn.classList.add("at-mic-holding"),O(s,{micBtn:l.micBtn,micTimer:l.micTimer,config:t,sessionId:r,addMessage:w,onStreamVoice:N(),onStreamChat:B}))},{passive:!1}),l.micBtn.addEventListener("touchend",m),l.micBtn.addEventListener("touchcancel",m)}else _&&(l.micBtn.style.display="flex",l.micBtn.addEventListener("mousedown",p=>{p.preventDefault(),l.micBtn.classList.contains("at-mic-recording")?G(s):O(s,{micBtn:l.micBtn,micTimer:l.micTimer,config:t,sessionId:r,addMessage:w,onStreamVoice:N(),onStreamChat:B})}));function N(){return(p,m)=>{Ct(p,m,{config:t,sessionId:r,messagesEl:d,callbacks:{onToken:c=>q(m,c,d),onFinal:c=>K(m,c),onToolCall:(c,v)=>{W(m,[c],v?{[c]:v}:{},d)},onAudio:c=>Z(c),onDone:(c,v)=>{a("assistant",c,v)},onError:c=>{m.classList.remove("at-thinking"),m.classList.add("at-error"),m.textContent=c}},scrollToBottom:M})}}j(t,d,n,w,{onAssistantRow:y}),document.body.appendChild(b),window.__agentTutorSetAgent=p=>{if(!p)return;t.agent=p;let m="at_messages_"+p,c="at_session_"+p,v=X(c),L=Y(m,v);n=L.readStored,a=L.appendStored,r=v;let H=l.head.querySelector(".at-head-info");H&&(H.innerHTML="<strong>"+E(t.title)+"</strong><span>"+E(p)+"</span>"),d.innerHTML="",j(t,d,n,w,{onAssistantRow:y});try{localStorage.setItem("agentTutorAgentId",p)}catch(ae){}};try{let p=localStorage.getItem("agentTutorAgentId");p&&t.agent!==p&&((nt=window.__agentTutorSetAgent)==null||nt.call(window,p))}catch(p){}}document.readyState==="loading"?document.addEventListener("DOMContentLoaded",Lt):Lt();})();
//# sourceMappingURL=embed.js.map

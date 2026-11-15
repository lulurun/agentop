/**
 * Everything is Ctrl+Shift, because while the terminal has focus plain keys
 * belong to it. Ctrl+B in particular is the tmux prefix.
 */
const CODES = new Set([
  "BracketLeft", "BracketRight", "KeyN", "KeyK", "KeyS", "KeyF",
  "Digit1", "Digit2", "Digit3", "Digit4", "Digit5", "Digit6", "Digit7", "Digit8", "Digit9",
]);

export function isAppShortcut(event: KeyboardEvent): boolean {
  return event.ctrlKey && event.shiftKey && !event.altKey && CODES.has(event.code);
}

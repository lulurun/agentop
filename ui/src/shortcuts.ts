/**
 * Everything is Ctrl+Shift, because while the terminal has focus plain keys
 * belong to it. Ctrl+B in particular is the tmux prefix.
 */
const CODES = new Set(["BracketLeft", "BracketRight", "KeyN", "KeyK", "KeyS", "KeyF"]);

export function isAppShortcut(event: KeyboardEvent): boolean {
  return event.ctrlKey && event.shiftKey && !event.altKey && CODES.has(event.code);
}

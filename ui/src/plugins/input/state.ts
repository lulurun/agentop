/**
 * What the Input bar keeps on this device: whether it shows, and each session's
 * unsent text. Both belong to the viewer, not the service, so they live in
 * localStorage and a failure to read or write only loses the convenience.
 */

export type Mode = "bar" | "terminal";

const MODE_KEY = "agentop.input.mode";
const DRAFT_KEY = "agentop.input.draft.";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Only a convenience.
  }
}

/** A touch screen shows the bar until told otherwise; a mouse and keyboard type in the terminal. */
export function defaultMode(coarse: boolean): Mode {
  return coarse ? "bar" : "terminal";
}

function touchScreen(): boolean {
  try {
    return window.matchMedia("(pointer: coarse)").matches;
  } catch {
    return false;
  }
}

export function loadMode(coarse = touchScreen()): Mode {
  const saved = read(MODE_KEY);
  return saved === "bar" || saved === "terminal" ? saved : defaultMode(coarse);
}

/** One mode for every bar on the page, so switching in one tab switches them all. */
let mode: Mode | null = null;
const listeners = new Set<() => void>();

export const modeStore = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  snapshot(): Mode {
    mode ??= loadMode();
    return mode;
  },
  set(next: Mode) {
    mode = next;
    write(MODE_KEY, next);
    for (const listener of listeners) listener();
  },
};

export function loadDraft(sessionId: string): string {
  return read(DRAFT_KEY + sessionId) ?? "";
}

export function saveDraft(sessionId: string, text: string) {
  write(DRAFT_KEY + sessionId, text ? text : null);
}

/**
 * The bar's key buttons: the plugin's key name, what the button shows, what it
 * says to a screen reader, and what a terminal's keyboard sends for it (used
 * after the tmux prefix, which goes through the terminal).
 */
export const KEYS: [key: string, label: string, name: string, bytes: string][] = [
  ["escape", "Esc", "Escape", "\x1b"],
  ["up", "↑", "Up arrow", "\x1b[A"],
  ["down", "↓", "Down arrow", "\x1b[B"],
  ["enter", "⏎", "Enter", "\r"],
  ["tab", "Tab", "Tab", "\t"],
  ["shift-tab", "⇧Tab", "Shift Tab", "\x1b[Z"],
  ["1", "1", "1", "1"],
  ["2", "2", "2", "2"],
  ["3", "3", "3", "3"],
];

/** Ctrl-B, tmux's default prefix. */
export const PREFIX = "\x02";

/** What to type through the terminal after the prefix: the text as it is, no Enter. */
export function afterPrefix(text: string): string {
  return PREFIX + text;
}

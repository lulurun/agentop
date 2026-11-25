/**
 * The layout's keys (docs/0_10_ui.md, Keys): Ctrl+Shift and a physical key, so plain
 * keys stay the terminal's (Ctrl+B is the tmux prefix) on any keyboard layout.
 */

export type PanelOrArea = "left-panel" | "left-area" | "right-area" | "right-panel";

export type LayoutAction =
  | { do: "toggle-left" }
  | { do: "connections" }
  | { do: "feature"; index: number }
  | { do: "settings" }
  | { do: "toggle-right" }
  | { do: "focus"; target: PanelOrArea }
  | { do: "tab"; index: number }
  | { do: "close-tab" }
  | { do: "toggle-input" };

/** How many features the nav's keys reach, Ctrl+Shift+3 to 8. */
export const FEATURE_KEYS = 6;

const FOCUS: Record<string, PanelOrArea> = { KeyQ: "left-panel", KeyW: "left-area", KeyE: "right-area", KeyR: "right-panel" };
const TABS = ["KeyA", "KeyS", "KeyD", "KeyF", "KeyG", "KeyH", "KeyJ", "KeyK"];

export function layoutAction(event: KeyboardEvent): LayoutAction | null {
  if (!event.ctrlKey || !event.shiftKey || event.altKey || event.metaKey) return null;
  const { code } = event;
  const digit = /^Digit(\d)$/.exec(code)?.[1];
  if (digit !== undefined) {
    const n = Number(digit);
    if (n === 1) return { do: "toggle-left" };
    if (n === 2) return { do: "connections" };
    if (n === 9) return { do: "settings" };
    if (n === 0) return { do: "toggle-right" };
    return { do: "feature", index: n - 3 };
  }
  if (FOCUS[code]) return { do: "focus", target: FOCUS[code] };
  const tab = TABS.indexOf(code);
  if (tab >= 0) return { do: "tab", index: tab };
  if (code === "KeyM") return { do: "toggle-input" };
  if (code === "KeyL") return { do: "close-tab" };
  return null;
}

/** "Ctrl+Shift+<key>", for a button's title. */
export const keyHint = (key: string) => `Ctrl+Shift+${key}`;

/** The key of the feature at `index` in the nav, if it has one. */
export const featureKey = (index: number) => (index < FEATURE_KEYS ? keyHint(String(index + 3)) : undefined);

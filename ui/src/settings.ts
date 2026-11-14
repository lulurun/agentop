/** Preferences for this browser or app. They belong to the viewer, not to a service. */
export interface Settings {
  theme: "system" | "light" | "dark";
  /** "auto" uses WebGL where it works. "dom" is slower but works everywhere. */
  renderer: "auto" | "dom";
  fontSize: number;
}

const THEME_KEY = "agentop.theme";
const RENDERER_KEY = "agentop.renderer";
const FONT_SIZE_KEY = "agentop.fontSize";

export const FONT_SIZES = { min: 10, max: 22, default: 13 };

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function loadSettings(): Settings {
  const theme = read(THEME_KEY);
  const fontSize = Number(read(FONT_SIZE_KEY));
  return {
    theme: theme === "light" || theme === "dark" ? theme : "system",
    renderer: read(RENDERER_KEY) === "dom" ? "dom" : "auto",
    fontSize: fontSize >= FONT_SIZES.min && fontSize <= FONT_SIZES.max ? fontSize : FONT_SIZES.default,
  };
}

export function saveSettings(settings: Settings) {
  try {
    localStorage.setItem(THEME_KEY, settings.theme);
    localStorage.setItem(RENDERER_KEY, settings.renderer);
    localStorage.setItem(FONT_SIZE_KEY, String(settings.fontSize));
  } catch {
    // Only a preference.
  }
}

export function applyTheme(theme: Settings["theme"]) {
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  syncThemeColor();
}

// The phone's status bar, when the app is installed, takes the page's own background.
let followingSystem = false;
function syncThemeColor() {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) return;
  meta.content = getComputedStyle(document.documentElement).getPropertyValue("--ink").trim() || meta.content;
  if (!followingSystem && window.matchMedia) {
    followingSystem = true;
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", syncThemeColor);
  }
}

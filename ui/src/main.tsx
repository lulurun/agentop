import { applyTheme, loadSettings } from "./settings";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/700.css";
import { createRoot } from "react-dom/client";
import { App } from "./components/App";
import "./styles.css";

applyTheme(loadSettings().theme);

// A UI build replaces every chunk, so a page loaded before it can't load the ones it
// loads lazily, such as the viewer's. Reload to get the new build; not in a loop.
const RELOADED_KEY = "agentop.chunkReload";
window.addEventListener("vite:preloadError", (event) => {
  try {
    if (Date.now() - Number(sessionStorage.getItem(RELOADED_KEY) ?? 0) < 30_000) return;
    sessionStorage.setItem(RELOADED_KEY, String(Date.now()));
  } catch {
    return; // without storage a missing chunk would reload forever
  }
  event.preventDefault();
  location.reload();
});

createRoot(document.getElementById("root")!).render(<App />);

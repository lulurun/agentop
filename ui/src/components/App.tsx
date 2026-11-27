import { modeStore } from "../plugins/input/state";
import { useEffect, useRef, useSyncExternalStore, type CSSProperties } from "react";
import { PluginPanel } from "../plugins/Slot";
import { layoutAction, type PanelOrArea } from "../hotkeys";
import { currentFeature, layoutOf, layoutReady, panelsOf, useApp, widthsOf } from "../store";
import type { Area } from "../tabs";
import { DEFAULT_LEFT, DEFAULT_RIGHT, fitSides, MAX_SIDE, MIN_MIDDLE, MIN_SIDE } from "../sizes";
import { Splitter } from "./Splitter";
import { pluginOf, featuresFor } from "../features";
import { DetailsPanel } from "./DetailsPanel";
import { AddServiceDialog } from "./ServiceDialogs";
import { AppOutdated, ServiceSwitcher, UpdateBadge } from "./ServiceSwitcher";
import { MainArea } from "./MainArea";
import { SettingsDialog } from "./SettingsDialog";
import { Sidebar } from "./Sidebar";
import { GearIcon, ToolbarIcon } from "./icons";
import { useFeatures, FeatureNav } from "./FeatureNav";

const FOCUSABLE = 'textarea:not([disabled]), input:not([disabled]), select:not([disabled]), button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

/** Give the keyboard to a panel or area: a terminal's input if it shows one, else its first control. */
function focusInside(target: PanelOrArea) {
  const root =
    target === "left-panel" ? document.querySelector<HTMLElement>(".sidebar")
    : target === "right-panel" ? document.querySelector<HTMLElement>(".details")
    : document.querySelector<HTMLElement>(`.workbench:not([hidden]) .tab-group[data-area="${target === "left-area" ? "left" : "right"}"] .tab-body`);
  if (!root) return;
  const visible = (el: HTMLElement) => !el.closest("[hidden]") && el.getClientRects().length > 0;
  const terminal = [...root.querySelectorAll<HTMLElement>(".xterm-helper-textarea")].find(visible);
  const first = terminal ?? [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].find(visible);
  (first ?? root).focus();
}

/** After the store's change is drawn. */
const afterDraw = (run: () => void) => requestAnimationFrame(() => requestAnimationFrame(run));

function runLayoutKey(event: KeyboardEvent) {
  const action = layoutAction(event);
  if (!action) return;
  event.preventDefault();
  event.stopPropagation();
  const app = useApp.getState();
  const linked = app.link.state === "connected" || app.link.state === "reconnecting";
  const features = featuresFor(app.plugins);
  const feature = currentFeature(app);
  const here = features.find((f) => f.id === feature);
  const layout = layoutOf(app);
  switch (action.do) {
    case "connections":
      app.setSwitcherOpen(!app.switcherOpen);
      return;
    case "settings":
      app.setDialog(app.dialog === "settings" ? null : "settings");
      return;
  }
  if (!linked) return;
  switch (action.do) {
    case "toggle-input":
      modeStore.set(modeStore.snapshot() === "bar" ? "terminal" : "bar");
      break;
    case "toggle-left":
      if (here?.left) app.toggleLeft();
      break;
    case "toggle-right":
      if (here?.right) app.toggleRight();
      break;
    case "feature": {
      const target = features[action.index];
      if (target) app.setFeature(target.id);
      break;
    }
    case "focus": {
      const { target } = action;
      if (target === "left-panel" || target === "right-panel") {
        const left = target === "left-panel";
        if (!(left ? here?.left : here?.right)) break;
        const panels = panelsOf(app);
        if (!(left ? panels.leftOpen : panels.rightOpen)) (left ? app.toggleLeft : app.toggleRight)();
      } else {
        const area = target === "left-area" ? "left" : "right";
        if (!layout[area]) break;
        app.focusArea(feature, area);
      }
      afterDraw(() => focusInside(target));
      break;
    }
    case "tab":
      if (!layout[layout.focused]?.tabs[action.index]) break;
      app.activateTabAt(action.index);
      afterDraw(() => focusInside(layout.focused === "left" ? "left-area" : "right-area"));
      break;
    case "close-tab":
      app.closeActiveTab();
      break;
  }
}

const onResize = (notify: () => void) => {
  window.addEventListener("resize", notify);
  return () => window.removeEventListener("resize", notify);
};

/** The window's width, drawn again as it changes. */
export function useWindowWidth(): number {
  return useSyncExternalStore(onResize, () => window.innerWidth);
}

/** Below this the side panels are overlays, at a width of their own, and one area shows (styles.css). */
const OVERLAYS = 900;

type AreaState = "absent" | "hidden" | "shown";

/** The middle panel's two areas as the header's toggles see them, as one string so the store's other changes don't draw them again. */
function useAreas(): { left: AreaState; right: AreaState; focused: Area } | null {
  const key = useApp((s) => {
    const feature = currentFeature(s);
    if (!layoutReady(s, feature)) return "";
    const layout = layoutOf(s, feature);
    const of = (area: Area) => (!layout[area] ? "absent" : layout[area]!.hidden ? "hidden" : "shown");
    return `${of("left")} ${of("right")} ${layout.focused}`;
  });
  if (!key) return null;
  const [left, right, focused] = key.split(" ") as [AreaState, AreaState, Area];
  return { left, right, focused };
}

/** The toggle of one area of the middle panel. Where only one area fits, it shows that area instead. */
function AreaToggle({ area, docked, linked }: { area: Area; docked: boolean; linked: boolean }) {
  const areas = useAreas();
  const toggleArea = useApp((s) => s.toggleArea);
  const focusArea = useApp((s) => s.focusArea);
  const feature = useApp(currentFeature);
  const name = area === "left" ? "left area" : "right area";
  const state = linked && areas ? areas[area] : "absent";
  const otherShown = !!areas && areas[area === "left" ? "right" : "left"] === "shown";
  // On a narrow screen the middle panel draws only the active area of two.
  const showing = state === "shown" && (docked || !otherShown || areas!.focused === area);
  const othersShowing = docked ? otherShown : otherShown && !showing;
  const disabled = state === "absent" || (showing && !othersShowing);
  const title =
    state === "absent" ? `No tabs in the ${name}`
    : disabled ? `The ${name} is the only one shown`
    : docked ? `${showing ? "Hide" : "Show"} the ${name}`
    : `Show the ${name}`;
  return (
    <button
      type="button"
      className="icon"
      aria-label={`Toggle ${name}`}
      aria-pressed={showing}
      title={title}
      disabled={disabled}
      onClick={() => (docked ? toggleArea(area) : focusArea(feature, area))}
    >
      <ToolbarIcon side={area === "left" ? "mid-left" : "mid-right"} filled={showing} />
    </button>
  );
}

export function App() {
  // Each field on its own, so a change elsewhere in the store, such as a session's, doesn't draw the app again.
  const leftOpen = useApp((s) => panelsOf(s).leftOpen);
  const rightOpen = useApp((s) => panelsOf(s).rightOpen);
  const toggleLeft = useApp((s) => s.toggleLeft);
  const toggleRight = useApp((s) => s.toggleRight);
  const setDialog = useApp((s) => s.setDialog);
  const hasConnections = useApp((s) => s.connections.length > 0);
  const activeId = useApp((s) => s.activeId);
  const dialog = useApp((s) => s.dialog);
  const error = useApp((s) => s.error);
  const setError = useApp((s) => s.setError);
  const { current } = useFeatures();
  const linked = useApp((s) => s.link.state === "connected" || s.link.state === "reconnecting");
  // A feature without a panel leaves its toggle disabled.
  const hasLeft = linked && current.left;
  const hasRight = linked && current.right;
  const showLeft = hasLeft && leftOpen;
  const showRight = hasRight && rightOpen;
  const leftWidth = useApp((s) => widthsOf(s).leftWidth) ?? DEFAULT_LEFT;
  const rightWidth = useApp((s) => widthsOf(s).rightWidth) ?? DEFAULT_RIGHT;
  const setPanelWidth = useApp((s) => s.setPanelWidth);
  const windowWidth = useWindowWidth();
  const appRef = useRef<HTMLDivElement>(null);
  const docked = windowWidth >= OVERLAYS;
  const fit = fitSides(windowWidth, showLeft ? leftWidth : null, showRight ? rightWidth : null);

  useEffect(() => {
    void useApp.getState().init();
  }, []);

  useEffect(() => {
    // Capture, so the layout's keys come before the terminal and anything else on the page.
    window.addEventListener("keydown", runLayoutKey, true);
    return () => window.removeEventListener("keydown", runLayoutKey, true);
  }, []);

  if (!activeId || !hasConnections) return null;
  const plugin = pluginOf(current.id);
  const bounds = () => appRef.current?.getBoundingClientRect() ?? new DOMRect(0, 0, windowWidth, 0);
  // Each side may grow only as far as leaves the middle panel its room beside the other side as drawn.
  const leftMax = Math.max(MIN_SIDE, Math.min(MAX_SIDE, windowWidth - MIN_MIDDLE - fit.right));
  const rightMax = Math.max(MIN_SIDE, Math.min(MAX_SIDE, windowWidth - MIN_MIDDLE - fit.left));
  const sizes = { "--left": `${fit.left}px`, "--right": `${fit.right}px` } as CSSProperties;

  return (
    <div ref={appRef} className="app" data-left={showLeft} data-right={showRight} data-feature={current.id} style={docked ? sizes : undefined}>
      <header className="header">
        <img className="brand" src="./logo.svg" alt="agentop" width={22} height={22} draggable={false} />
        <div className="header-title">
          <ServiceSwitcher />
          {linked && <FeatureNav />}
          <AppOutdated />
          <UpdateBadge />
        </div>
        <div className="header-tools" role="group" aria-label="Settings and layout">
          <button type="button" className="icon" aria-label="Settings" title="Settings (Ctrl+Shift+9)" onClick={() => setDialog("settings")}>
            <GearIcon />
          </button>
          {/* Always in their places: a part this feature lacks, or no connection, leaves its toggle disabled. */}
          <button type="button" className="icon" aria-label="Toggle left panel" aria-pressed={showLeft} disabled={!hasLeft} title={hasLeft ? "Toggle left panel (Ctrl+Shift+1)" : "No left panel here"} onClick={toggleLeft}>
            <ToolbarIcon side="left" filled={showLeft} />
          </button>
          <AreaToggle area="left" docked={docked} linked={linked} />
          <AreaToggle area="right" docked={docked} linked={linked} />
          <button type="button" className="icon" aria-label="Toggle right panel" aria-pressed={showRight} disabled={!hasRight} title={hasRight ? "Toggle right panel (Ctrl+Shift+0)" : "No right panel here"} onClick={toggleRight}>
            <ToolbarIcon side="right" filled={showRight} />
          </button>
        </div>
      </header>
      {showLeft && (
        <aside className="sidebar" aria-label="Left panel" tabIndex={-1}>
          {current.id === "sessions" ? <Sidebar /> : plugin && <div className="sidebar-scroll"><PluginPanel pluginId={plugin} side="left" /></div>}
        </aside>
      )}
      {docked && showLeft && (
        <Splitter
          label="Resize the left panel"
          className="splitter-left"
          value={fit.left}
          min={MIN_SIDE}
          max={leftMax}
          onMove={(x) => setPanelWidth("left", Math.min(leftMax, x - bounds().left))}
          onNudge={(dx) => setPanelWidth("left", Math.min(leftMax, fit.left + dx))}
          onReset={() => setPanelWidth("left", Math.min(leftMax, DEFAULT_LEFT))}
        />
      )}
      <MainArea />
      {docked && showRight && (
        <Splitter
          label="Resize the right panel"
          className="splitter-right"
          value={fit.right}
          min={MIN_SIDE}
          max={rightMax}
          onMove={(x) => setPanelWidth("right", Math.min(rightMax, bounds().right - x))}
          onNudge={(dx) => setPanelWidth("right", Math.min(rightMax, fit.right - dx))}
          onReset={() => setPanelWidth("right", Math.min(rightMax, DEFAULT_RIGHT))}
        />
      )}
      {showRight && (
        <aside className="details" aria-label="Right panel" tabIndex={-1}>
          {current.id === "sessions" ? <DetailsPanel /> : plugin && <PluginPanel pluginId={plugin} side="right" />}
        </aside>
      )}
      {(showLeft || showRight) && <div className="scrim" onClick={() => { if (showLeft) toggleLeft(); if (showRight) toggleRight(); }} />}
      {dialog === "add-service" && <AddServiceDialog />}
      {dialog === "settings" && <SettingsDialog />}
      {error && (
        <div className="toast" role="alert">
          {error}
          <button type="button" onClick={() => setError(null)} aria-label="Dismiss">
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

import { useEffect } from "react";
import { PluginPanel } from "../plugins/Slot";
import { layoutAction, type PanelOrArea } from "../hotkeys";
import { currentFeature, layoutOf, useApp } from "../store";
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
        if (!(left ? app.leftOpen : app.rightOpen)) (left ? app.toggleLeft : app.toggleRight)();
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

export function App() {
  // Each field on its own, so a change elsewhere in the store, such as a session's, doesn't draw the app again.
  const leftOpen = useApp((s) => s.leftOpen);
  const rightOpen = useApp((s) => s.rightOpen);
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
  // A feature without a panel has no toggle for it either.
  const hasLeft = linked && current.left;
  const hasRight = linked && current.right;
  const showLeft = hasLeft && leftOpen;
  const showRight = hasRight && rightOpen;

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

  return (
    <div className="app" data-left={showLeft} data-right={showRight} data-feature={current.id}>
      <header className="header">
        {hasLeft && (
          <button type="button" className="icon" aria-label="Toggle left panel" aria-pressed={leftOpen} title="Toggle left panel (Ctrl+Shift+1)" onClick={toggleLeft}>
            <ToolbarIcon side="left" filled={leftOpen} />
          </button>
        )}
        <div className="header-title">
          <ServiceSwitcher />
          {linked && <FeatureNav />}
          <AppOutdated />
          <UpdateBadge />
        </div>
        <button type="button" className="icon" aria-label="Settings" title="Settings (Ctrl+Shift+9)" onClick={() => setDialog("settings")}>
          <GearIcon />
        </button>
        {hasRight && (
          <button type="button" className="icon" aria-label="Toggle right panel" aria-pressed={rightOpen} title="Toggle right panel (Ctrl+Shift+0)" onClick={toggleRight}>
            <ToolbarIcon side="right" filled={rightOpen} />
          </button>
        )}
      </header>
      {showLeft && (
        <aside className="sidebar" aria-label="Left panel" tabIndex={-1}>
          {current.id === "sessions" ? <Sidebar /> : plugin && <div className="sidebar-scroll"><PluginPanel pluginId={plugin} side="left" /></div>}
        </aside>
      )}
      <MainArea />
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

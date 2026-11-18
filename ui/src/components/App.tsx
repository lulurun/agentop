import { useEffect } from "react";
import { PluginPanel } from "../plugins/Slot";
import { isAppShortcut } from "../shortcuts";
import { currentWorkspace, useApp } from "../store";
import { pluginOf, workspacesFor } from "../workspaces";
import { DetailsPanel } from "./DetailsPanel";
import { AddServiceDialog } from "./ServiceDialogs";
import { AppOutdated, DesktopUpdate, ServiceSwitcher } from "./ServiceSwitcher";
import { MainArea } from "./MainArea";
import { NewSessionDialog } from "./NewSessionDialog";
import { SettingsDialog } from "./SettingsDialog";
import { Sidebar } from "./Sidebar";
import { GearIcon, ToolbarIcon } from "./icons";
import { ACTION_EVENT, hasPluginAction, useWorkspaces, WorkspaceAction, WorkspaceNav } from "./WorkspaceNav";

const DIGITS = ["Digit1", "Digit2", "Digit3", "Digit4", "Digit5", "Digit6", "Digit7", "Digit8", "Digit9"];

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
  const { current } = useWorkspaces();
  const linked = useApp((s) => s.link.state === "connected" || s.link.state === "reconnecting");
  // A workspace without a panel has no toggle for it either.
  const hasLeft = linked && current.left;
  const hasRight = linked && current.right;
  const showLeft = hasLeft && leftOpen;
  const showRight = hasRight && rightOpen;

  useEffect(() => {
    void useApp.getState().init();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!isAppShortcut(event)) return;
      event.preventDefault();
      event.stopPropagation();
      const app = useApp.getState();
      const workspace = currentWorkspace(app);
      const here = workspacesFor(app.plugins).find((w) => w.id === workspace);
      const digit = DIGITS.indexOf(event.code);
      if (digit >= 0) {
        const target = workspacesFor(app.plugins)[digit];
        if (target) app.setWorkspace(target.id);
        return;
      }
      switch (event.code) {
        case "BracketLeft":
          if (here?.left) app.toggleLeft();
          break;
        case "BracketRight":
          if (here?.right) app.toggleRight();
          break;
        case "KeyN":
          if (hasPluginAction(workspace)) window.dispatchEvent(new Event(ACTION_EVENT));
          else app.setDialog("new-session");
          break;
        case "KeyS":
          app.setSwitcherOpen(!app.switcherOpen);
          break;
        case "KeyK":
          app.setWorkspace("sessions");
          if (!app.leftOpen) app.toggleLeft();
          app.setFolded("active", false);
          requestAnimationFrame(() => document.getElementById("session-filter")?.focus());
          break;
        case "KeyF":
          window.dispatchEvent(new Event("agentop:terminal-search"));
          break;
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  if (!activeId || !hasConnections) return null;
  const plugin = pluginOf(current.id);

  return (
    <div className="app" data-left={showLeft} data-right={showRight} data-workspace={current.id}>
      <header className="header">
        {hasLeft && (
          <button type="button" className="icon" aria-label={current.id === "sessions" ? "Toggle session list" : "Toggle left panel"} aria-pressed={leftOpen} title="Toggle left panel (Ctrl+Shift+[)" onClick={toggleLeft}>
            <ToolbarIcon side="left" filled={leftOpen} />
          </button>
        )}
        <div className="header-title">
          <ServiceSwitcher />
          {linked && <WorkspaceNav />}
          <AppOutdated />
          <DesktopUpdate />
        </div>
        {linked && <WorkspaceAction />}
        <button type="button" className="icon" aria-label="Settings" title="Settings" onClick={() => setDialog("settings")}>
          <GearIcon />
        </button>
        {hasRight && (
          <button type="button" className="icon" aria-label={current.id === "sessions" ? "Toggle session details" : "Toggle right panel"} aria-pressed={rightOpen} title="Toggle right panel (Ctrl+Shift+])" onClick={toggleRight}>
            <ToolbarIcon side="right" filled={rightOpen} />
          </button>
        )}
      </header>
      {showLeft &&
        (current.id === "sessions" ? <Sidebar /> : plugin && <aside className="sidebar"><PluginPanel pluginId={plugin} side="left" /></aside>)}
      <MainArea />
      {showRight &&
        (current.id === "sessions" ? <DetailsPanel /> : plugin && <aside className="details"><PluginPanel pluginId={plugin} side="right" /></aside>)}
      {(showLeft || showRight) && <div className="scrim" onClick={() => { if (showLeft) toggleLeft(); if (showRight) toggleRight(); }} />}
      {dialog === "new-session" && <NewSessionDialog />}
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

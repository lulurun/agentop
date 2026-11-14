import { useEffect } from "react";
import { isAppShortcut } from "../shortcuts";
import { sessionTitle, useApp } from "../store";
import { DetailsPanel } from "./DetailsPanel";
import { AddServiceDialog } from "./ServiceDialogs";
import { AppOutdated, ServiceSwitcher } from "./ServiceSwitcher";
import { MainArea, selectedSession } from "./MainArea";
import { NewSessionDialog } from "./NewSessionDialog";
import { ServicePanel } from "./ServicePanel";
import { SettingsDialog } from "./SettingsDialog";
import { Sidebar } from "./Sidebar";
import { ToolbarIcon } from "./icons";

export function App() {
  const state = useApp();
  const { leftOpen, rightOpen, toggleLeft, toggleRight, setDialog, connections, activeId } = state;

  useEffect(() => {
    void useApp.getState().init();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!isAppShortcut(event)) return;
      event.preventDefault();
      event.stopPropagation();
      const app = useApp.getState();
      switch (event.code) {
        case "BracketLeft":
          app.toggleLeft();
          break;
        case "BracketRight":
          app.toggleRight();
          break;
        case "KeyN":
          app.setDialog("new-session");
          break;
        case "KeyS":
          app.setSwitcherOpen(!app.switcherOpen);
          break;
        case "KeyK":
          if (!app.leftOpen) app.toggleLeft();
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

  if (!activeId || !connections.length) return null;
  const session = selectedSession(state);

  return (
    <div className="app" data-left={leftOpen} data-right={rightOpen}>
      <header className="header">
        <button type="button" className="icon" aria-label="Toggle session list" aria-pressed={leftOpen} title="Toggle session list (Ctrl+Shift+[)" onClick={toggleLeft}>
          <ToolbarIcon side="left" filled={leftOpen} />
        </button>
        <div className="header-title">
          {session && <strong title={session.cwd}>{sessionTitle(session)}</strong>}
          <ServiceSwitcher />
          <AppOutdated />
        </div>
        <button type="button" className="header-new" onClick={() => setDialog("new-session")} title="New session (Ctrl+Shift+N)">
          New session
        </button>
        <button type="button" className="icon" aria-label="Toggle session details" aria-pressed={rightOpen} title="Toggle session details (Ctrl+Shift+])" onClick={toggleRight}>
          <ToolbarIcon side="right" filled={rightOpen} />
        </button>
      </header>
      {leftOpen && <Sidebar />}
      <MainArea />
      {rightOpen && <DetailsPanel />}
      {(leftOpen || rightOpen) && <div className="scrim" onClick={() => { if (leftOpen) toggleLeft(); if (rightOpen) toggleRight(); }} />}
      {state.dialog === "new-session" && <NewSessionDialog />}
      {state.dialog === "add-service" && <AddServiceDialog />}
      {state.dialog === "service" && <ServicePanel />}
      {state.dialog === "settings" && <SettingsDialog />}
      {state.error && (
        <div className="toast" role="alert">
          {state.error}
          <button type="button" onClick={() => state.setError(null)} aria-label="Dismiss">
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

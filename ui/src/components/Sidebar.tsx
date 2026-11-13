import { useState } from "react";
import type { Session } from "../api/client";
import { pluginsFor } from "../plugins/registry";
import { PluginSlot } from "../plugins/Slot";
import { sessionSummary, sessionTitle, sortedSessions, useApp } from "../store";
import { StatusMark } from "./icons";
import { Menu } from "./Workbench";

const STATUS_TEXT: Record<string, string> = {
  starting: "Starting",
  exited: "Agent exited",
  failed: "Failed",
};

function basename(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() ?? "";
}

/** Tools with a colour of their own. Others get a plain badge. */
const TOOL_COLOURS = new Set(["claude", "codex", "antigravity"]);

function ToolBadge({ tool }: { tool: string }) {
  return (
    <span className="tool-badge" data-tool={TOOL_COLOURS.has(tool) ? tool : undefined}>
      {tool}
    </span>
  );
}

function SessionRow({ session, selected }: { session: Session; selected: boolean }) {
  const select = useApp((s) => s.select);
  const summary = sessionSummary(session) ?? basename(session.cwd);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  return (
    <>
    <button
      type="button"
      title={summary}
      className="session-row"
      data-status={session.status}
      aria-current={selected ? "true" : undefined}
      // Ctrl or Cmd, or the middle button, opens it in a new tab, as in a browser.
      onClick={(event) => select(session.id, event.ctrlKey || event.metaKey ? "tab" : "replace")}
      onAuxClick={(event) => {
        if (event.button === 1) select(session.id, "tab");
      }}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenu({ x: event.clientX, y: event.clientY });
      }}
    >
      <StatusMark status={session.status} />
      <span className="session-name">{sessionTitle(session)}</span>
      <span className="session-meta">
        {session.tool && <ToolBadge tool={session.tool} />}
        {STATUS_TEXT[session.status] && <span>{STATUS_TEXT[session.status]}</span>}
        <PluginSlot slot="session-badge" session={session} />
        <span className="session-summary">{summary}</span>
      </span>
    </button>
    {menu && (
      <Menu
        x={menu.x}
        y={menu.y}
        onClose={() => setMenu(null)}
        items={[
          ["Open", () => select(session.id, "replace")],
          ["Open in new tab", () => select(session.id, "tab")],
          ["Open to the side", () => select(session.id, "side")],
        ]}
      />
    )}
    </>
  );
}

export function Sidebar() {
  const sessions = useApp((s) => s.sessions);
  const selected = useApp((s) => s.selected[s.activeId]);
  const setDialog = useApp((s) => s.setDialog);
  const linked = useApp((s) => s.link.state === "connected" || s.link.state === "reconnecting");
  const view = useApp((s) => s.view);
  const setView = useApp((s) => s.setView);
  const [filter, setFilter] = useState("");
  const pluginViews = pluginsFor("left-view", useApp((s) => s.plugins));

  const needle = filter.trim().toLowerCase();
  const rows = sortedSessions(sessions).filter(
    (s) => !needle || `${sessionTitle(s)} ${s.tool ?? ""} ${s.cwd}`.toLowerCase().includes(needle),
  );
  const managed = rows.filter((s) => s.managed);
  const external = rows.filter((s) => !s.managed);

  return (
    <aside className="sidebar">
      {linked && (
        <>
          <div className="sidebar-actions">
            <input
              id="session-filter"
              type="search"
              aria-label="Filter sessions"
              placeholder="Filter sessions"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </div>
          <nav className="session-list" aria-label="Sessions">
            {managed.map((s) => (
              <SessionRow key={s.id} session={s} selected={s.id === selected} />
            ))}
            {rows.length === 0 && (
              <p className="session-empty">
                {needle ? "No sessions match." : (
                  <>
                    No sessions yet.{" "}
                    <button type="button" className="link" onClick={() => setDialog("new-session")}>
                      Start one
                    </button>
                  </>
                )}
              </p>
            )}
            {external.length > 0 && (
              <>
                <h3 className="session-group">Started elsewhere</h3>
                {external.map((s) => (
                  <SessionRow key={s.id} session={s} selected={s.id === selected} />
                ))}
              </>
            )}
          </nav>
        </>
      )}
      <div className="sidebar-views">
        {linked && (
          <>
            <button type="button" aria-current={view === "history" ? "page" : undefined} onClick={() => setView(view === "history" ? "sessions" : "history")}>
              History
            </button>
            {pluginViews.map(({ info, ui }) => {
              const id = `plugin:${info.id}` as const;
              return (
                <button key={id} type="button" aria-current={view === id ? "page" : undefined} onClick={() => setView(view === id ? "sessions" : id)}>
                  {ui.leftView!.label}
                </button>
              );
            })}
          </>
        )}
        <button type="button" onClick={() => setDialog("settings")}>
          Settings
        </button>
      </div>
    </aside>
  );
}

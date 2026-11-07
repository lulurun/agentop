import { useState } from "react";
import type { Session } from "../api/client";
import { sessionTitle, sortedSessions, useApp } from "../store";
import { StatusMark } from "./icons";
import { ServiceSwitcher } from "./ServiceSwitcher";

const STATUS_TEXT: Record<string, string> = {
  starting: "Starting",
  exited: "Agent exited",
  failed: "Failed",
};

function basename(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() ?? "";
}

function SessionRow({ session, selected }: { session: Session; selected: boolean }) {
  const select = useApp((s) => s.select);
  return (
    <button
      type="button"
      className="session-row"
      data-status={session.status}
      aria-current={selected ? "true" : undefined}
      onClick={() => select(session.id)}
    >
      <StatusMark status={session.status} />
      <span className="session-name">{sessionTitle(session)}</span>
      <span className="session-meta">
        <span>{STATUS_TEXT[session.status] ?? session.tool}</span>
        <span>{basename(session.cwd)}</span>
      </span>
    </button>
  );
}

export function Sidebar() {
  const sessions = useApp((s) => s.sessions);
  const selected = useApp((s) => s.selected[s.activeId]);
  const setDialog = useApp((s) => s.setDialog);
  const linked = useApp((s) => s.link.state === "connected" || s.link.state === "reconnecting");
  const [filter, setFilter] = useState("");

  const needle = filter.trim().toLowerCase();
  const rows = sortedSessions(sessions).filter(
    (s) => !needle || `${sessionTitle(s)} ${s.tool ?? ""} ${s.cwd}`.toLowerCase().includes(needle),
  );
  const managed = rows.filter((s) => s.managed);
  const external = rows.filter((s) => !s.managed);

  return (
    <aside className="sidebar">
      <ServiceSwitcher />
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
    </aside>
  );
}

import { memo, useState, type ReactNode } from "react";
import type { Session } from "../api/client";
import { PluginSlot } from "../plugins/Slot";
import { activeCount, layoutOf, sessionSummary, sessionTitle, sortedSessions, useApp } from "../store";
import { focusedTab } from "../tabs";
import { HistorySection } from "./HistoryView";
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

export function ToolBadge({ tool }: { tool: string }) {
  return (
    <span className="tool-badge" data-tool={TOOL_COLOURS.has(tool) ? tool : undefined}>
      {tool}
    </span>
  );
}

/** Drawn again only when its own session changes. */
const SessionRow = memo(function SessionRow({ session, selected }: { session: Session; selected: boolean }) {
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
});

export const Sidebar = memo(function Sidebar() {
  const sessions = useApp((s) => s.sessions);
  const selected = useApp((s) => s.selected[s.activeId]);
  const setDialog = useApp((s) => s.setDialog);
  const linked = useApp((s) => s.link.state === "connected" || s.link.state === "reconnecting");
  const maxActive = useApp((s) => s.history.maxActive);
  const pastOpen = useApp((s) => {
    const content = focusedTab(layoutOf(s, "sessions"))?.content;
    return content?.kind === "past" ? content.historyId : undefined;
  });
  const [filter, setFilter] = useState("");

  const needle = filter.trim().toLowerCase();
  // A plugin's own sessions, such as a dialogue's agents, are shown in its workspace instead.
  const rows = sortedSessions(sessions).filter(
    (s) => !s.owner && (!needle || `${sessionTitle(s)} ${s.tool ?? ""} ${s.cwd}`.toLowerCase().includes(needle)),
  );
  const managed = rows.filter((s) => s.managed);
  const external = rows.filter((s) => !s.managed);
  const active = activeCount(sessions);
  // The selected session is only marked while its tab, not a past session's, has focus.
  const current = pastOpen ? undefined : selected;

  return (
    <aside className="sidebar">
      {linked && (
        <div className="sidebar-scroll">
          <FoldSection
            id="active"
            title="Active sessions"
            hint={`At most ${maxActive} sessions started by agentop can be active at once`}
            extra={<span className="session-count" data-full={active >= maxActive || undefined}>{active}/{maxActive}</span>}
          >
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
                <SessionRow key={s.id} session={s} selected={s.id === current} />
              ))}
              {managed.length === 0 && (
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
            </nav>
          </FoldSection>
          {external.length > 0 && (
            <FoldSection id="elsewhere" title="Started elsewhere" extra={<span className="session-count">{external.length}</span>}>
              <nav className="session-list" aria-label="Started elsewhere">
                {external.map((s) => (
                  <SessionRow key={s.id} session={s} selected={s.id === current} />
                ))}
              </nav>
            </FoldSection>
          )}
          <HistorySection selected={pastOpen} />
        </div>
      )}
    </aside>
  );
});

/** One of the left panel's sections, folded shut or open by clicking its title. Remembered in this browser. */
export function FoldSection({
  id,
  title,
  hint,
  extra,
  children,
}: {
  id: string;
  title: string;
  hint?: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  const folded = useApp((s) => s.folded.includes(id));
  const setFolded = useApp((s) => s.setFolded);
  const body = `section-${id}`;
  return (
    <section className="sidebar-section" aria-label={title} data-folded={folded || undefined}>
      <h3 className="session-group">
        <button type="button" className="fold-toggle" aria-expanded={!folded} aria-controls={body} title={hint} onClick={() => setFolded(id, !folded)}>
          <span className="fold-chevron" aria-hidden="true" />
          <span className="fold-title">{title}</span>
          {extra}
        </button>
      </h3>
      <div id={body} hidden={folded}>
        {children}
      </div>
    </section>
  );
}

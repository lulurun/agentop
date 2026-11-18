import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ServiceClient, Session } from "../api/client";
import { UI_PLUGINS } from "../plugins/registry";
import { FileTab, PluginTab } from "../plugins/Slot";
import { activeConnection, layoutOf, sessionTitle, useApp } from "../store";
import { canSplit, MAX_GROUPS, sessionOf, type Group, type Tab } from "../tabs";
import type { WorkspaceId } from "../workspaces";
import { connectionName } from "../connections";
import { PastSessionView } from "./HistoryView";
import { StatusView } from "./StatusView";

function baseName(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

function useTabLabel(tab: Tab): { label: string; title: string; status?: string } {
  const id = sessionOf(tab.content);
  const session = useApp((s) => (id ? s.sessions[id] : undefined));
  const historyId = tab.content.kind === "past" ? tab.content.historyId : undefined;
  const past = useApp((s) => (historyId ? s.history.items?.find((i) => i.id === historyId) : undefined));
  const service = useApp((s) => connectionName(activeConnection(s), s.link.state === "connected" || s.link.state === "reconnecting" ? s.link.service.name : undefined));
  const { content } = tab;
  if (content.kind === "status") return { label: "Status", title: `Status of ${service}` };
  if (content.kind === "past") return { label: past?.name ?? "Past session", title: past ? `Past session in ${past.cwd}` : "Past session" };
  if (content.kind === "plugin") {
    const view = UI_PLUGINS[content.plugin]?.workspace?.views[content.view];
    const label = view?.label ?? content.view;
    if (!content.key) return { label, title: label };
    const named = view?.tabLabel?.(content.key);
    return { label: named ?? `${label}: ${content.key}`, title: named ?? label };
  }
  if (content.kind === "session") {
    return session
      ? { label: sessionTitle(session), title: session.cwd, status: session.status }
      : { label: "Session", title: content.sessionId };
  }
  const { file } = content;
  const where = file.source === "transcript" ? "transcript" : (session?.cwd ?? "");
  const diff = file.view === "diff";
  return { label: diff ? `${baseName(file.path)} (diff)` : baseName(file.path), title: `${diff ? "Changes to " : ""}${file.path} (${where})` };
}

/** Right-click menu for a tab. */
export function Menu({ x, y, items, onClose }: { x: number; y: number; items: [string, () => void][]; onClose(): void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector("button")?.focus();
    const away = (event: Event) => {
      if (!ref.current?.contains(event.target as Node)) onClose();
    };
    const key = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", key, true);
    return () => {
      window.removeEventListener("pointerdown", away, true);
      window.removeEventListener("keydown", key, true);
    };
  }, [onClose]);
  return (
    <div ref={ref} className="menu" role="menu" style={{ left: x, top: y }}>
      {items.map(([label, run]) => (
        <button
          key={label}
          type="button"
          role="menuitem"
          onClick={() => {
            onClose();
            run();
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function TabButton({ workspace, tab, group, active, canMove }: { workspace: WorkspaceId; tab: Tab; group: number; active: boolean; canMove: boolean }) {
  const { label, title, status } = useTabLabel(tab);
  const activateTab = useApp((s) => s.activateTab);
  const closeTab = useApp((s) => s.closeTab);
  const moveTab = useApp((s) => s.moveTab);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);

  return (
    <div className="tab" role="presentation" data-active={active || undefined} data-kind={tab.content.kind} data-pinned={tab.pinned || undefined}>
      <button
        type="button"
        role="tab"
        className="tab-label"
        aria-selected={active}
        title={title}
        data-status={status}
        onClick={() => activateTab(workspace, group, tab.id)}
        onAuxClick={(event) => {
          if (event.button === 1) closeTab(workspace, group, tab.id);
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          setMenu({ x: event.clientX, y: event.clientY });
        }}
      >
        {label}
      </button>
      {!tab.pinned && (
        <button type="button" className="tab-close" aria-label={`Close ${label}`} title="Close" onClick={() => closeTab(workspace, group, tab.id)}>
          ×
        </button>
      )}
      {menu && (
        <Menu
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          items={[
            ...(tab.pinned ? [] : ([["Close", () => closeTab(workspace, group, tab.id)]] as [string, () => void][])),
            ...(canMove ? ([[group === 0 ? "Move to the right" : "Move to the left", () => moveTab(workspace, group, tab.id)]] as [string, () => void][]) : []),
          ]}
        />
      )}
    </div>
  );
}

function TabBar({ workspace, group, index, groups }: { workspace: WorkspaceId; group: Group; index: number; groups: number }) {
  const splitTab = useApp((s) => s.splitTab);
  const canMove = groups > 1 || group.tabs.length > 1;
  const active = group.tabs.find((t) => t.id === group.active);
  return (
    <div className="tab-bar">
      <div className="tab-list" role="tablist" aria-label={groups > 1 ? (index === 0 ? "Left tabs" : "Right tabs") : "Tabs"}>
        {group.tabs.map((tab) => (
          <TabButton key={tab.id} workspace={workspace} tab={tab} group={index} active={tab.id === group.active} canMove={canMove} />
        ))}
      </div>
      {groups < MAX_GROUPS && active && canSplit(active.content) && (
        <button type="button" className="icon tab-split" aria-label="Split to the right" title="Split to the right" onClick={() => splitTab(workspace)}>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" fill="none" stroke="currentColor" />
            <line x1="8" y1="2.5" x2="8" y2="13.5" stroke="currentColor" />
          </svg>
        </button>
      )}
    </div>
  );
}

export interface SessionPaneProps {
  session: Session | undefined;
  sessionId: string;
  tabKey: string;
  /** Its group has focus and it is the tab shown there. */
  focused: boolean;
  /** Shown, not kept out of sight for a quick return. */
  visible: boolean;
  client: ServiceClient;
  onClose(): void;
}

/**
 * How many sessions each group keeps on the page, the shown one included. Going back to one
 * kept out of sight is instant; any other builds a new terminal and attaches it again.
 * Each kept terminal holds a WebGL context and a socket, and a browser allows about 16 contexts.
 */
export const KEEP_SESSIONS = 3;

/** A session kept on the page, and the tab that showed it last. */
interface Kept {
  sessionId: string;
  tabId: string;
}

/** One session's pane, drawn again only when that session changes. */
function SessionSlot({
  sessionId,
  renderSession,
  ...rest
}: Omit<SessionPaneProps, "client" | "session"> & { renderSession(props: Omit<SessionPaneProps, "client">): ReactNode }) {
  const session = useApp((s) => s.sessions[sessionId]);
  return <>{renderSession({ ...rest, session, sessionId })}</>;
}

/** One group: its tab bar and its active tab's contents. */
function GroupView({
  workspace,
  group,
  index,
  groups,
  focused,
  renderSession,
}: {
  workspace: WorkspaceId;
  group: Group;
  index: number;
  groups: number;
  focused: boolean;
  renderSession(props: Omit<SessionPaneProps, "client">): ReactNode;
}) {
  const focusGroup = useApp((s) => s.focusGroup);
  const closeTab = useApp((s) => s.closeTab);
  const tab = group.tabs.find((t) => t.id === group.active);
  const shown = tab?.content.kind === "session" ? { sessionId: tab.content.sessionId, tabId: tab.id } : null;

  // The sessions shown here last, most recent first. A session is in a group's tabs at most
  // once, so it is kept by its id: a tab that switched to another session keeps the one it
  // showed, and going back to it from the list is quick too.
  const [recent, setRecent] = useState<Kept[]>([]);
  if (shown && (recent[0]?.sessionId !== shown.sessionId || recent[0].tabId !== shown.tabId)) {
    setRecent([shown, ...recent.filter((k) => k.sessionId !== shown.sessionId)].slice(0, KEEP_SESSIONS));
  }
  // A kept session goes once the tab that showed it is closed, or once the session is.
  const alive = useApp((s) => recent.filter((k) => s.sessions[k.sessionId]).map((k) => k.sessionId).join("\n")).split("\n");
  const kept = recent.filter(
    (k) => k.sessionId === shown?.sessionId || (alive.includes(k.sessionId) && group.tabs.some((t) => t.id === k.tabId)),
  );

  return (
    <section
      className="tab-group"
      data-focused={focused || undefined}
      aria-label={groups > 1 ? (index === 0 ? "Left group" : "Right group") : undefined}
      onPointerDownCapture={() => {
        if (!focused) focusGroup(workspace, index);
      }}
      onFocusCapture={() => {
        if (!focused) focusGroup(workspace, index);
      }}
    >
      <TabBar workspace={workspace} group={group} index={index} groups={groups} />
      <div className="tab-body" role="tabpanel">
        {kept.map((k) => {
          const visible = k.sessionId === shown?.sessionId;
          return (
            <div key={k.sessionId} className="tab-pane" hidden={!visible || undefined} aria-hidden={!visible || undefined}>
              <SessionSlot
                sessionId={k.sessionId}
                tabKey={k.sessionId}
                focused={focused && visible}
                visible={visible}
                onClose={() => closeTab(workspace, index, k.tabId)}
                renderSession={renderSession}
              />
            </div>
          );
        })}
        {tab?.content.kind === "session"
          ? null
          : tab?.content.kind === "file" ? <FileTab key={tab.id} file={tab.content.file} />
          : tab?.content.kind === "status" ? <StatusView key={tab.id} onClose={() => closeTab(workspace, index, tab.id)} />
          : tab?.content.kind === "past" ? <PastSessionView key={tab.id} historyId={tab.content.historyId} onClose={() => closeTab(workspace, index, tab.id)} />
          : tab?.content.kind === "plugin" ? <PluginTab key={tab.id} pluginId={tab.content.plugin} view={tab.content.view} viewKey={tab.content.key} />
          : null}
      </div>
    </section>
  );
}

/** A workspace's tab groups. With no tabs at all, `empty` shows instead. */
export function Workbench({
  workspace,
  empty,
  renderSession,
  hidden = false,
}: {
  workspace: WorkspaceId;
  empty: ReactNode;
  renderSession(props: Omit<SessionPaneProps, "client">): ReactNode;
  /** Kept on the page but out of sight, so its terminals stay connected (design 10.7.3). */
  hidden?: boolean;
}) {
  const layout = useApp((s) => layoutOf(s, workspace));
  if (layout.groups.length === 0) return hidden ? null : <>{empty}</>;
  return (
    <div className="workbench" data-groups={layout.groups.length} data-workspace={workspace} hidden={hidden || undefined} aria-hidden={hidden || undefined}>
      {layout.groups.map((group, index) => (
        <GroupView
          key={index}
          workspace={workspace}
          group={group}
          index={index}
          groups={layout.groups.length}
          focused={!hidden && index === layout.focused}
          renderSession={renderSession}
        />
      ))}
    </div>
  );
}

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ServiceClient, Session } from "../api/client";
import { FileTab } from "../plugins/Slot";
import { layoutOf, sessionTitle, useApp } from "../store";
import { MAX_GROUPS, type Group, type Tab } from "../tabs";

function baseName(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

function useTabLabel(tab: Tab): { label: string; title: string; status?: string } {
  const session = useApp((s) => s.sessions[tab.content.kind === "session" ? tab.content.sessionId : tab.content.file.sessionId]);
  if (tab.content.kind === "session") {
    return session
      ? { label: sessionTitle(session), title: session.cwd, status: session.status }
      : { label: "Session", title: tab.content.sessionId };
  }
  const { file } = tab.content;
  const where = file.source === "transcript" ? "transcript" : (session?.cwd ?? "");
  return { label: baseName(file.path), title: `${file.path} (${where})` };
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

function TabButton({ tab, group, active, canMove }: { tab: Tab; group: number; active: boolean; canMove: boolean }) {
  const { label, title, status } = useTabLabel(tab);
  const activateTab = useApp((s) => s.activateTab);
  const closeTab = useApp((s) => s.closeTab);
  const moveTab = useApp((s) => s.moveTab);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);

  return (
    <div className="tab" role="presentation" data-active={active || undefined} data-kind={tab.content.kind}>
      <button
        type="button"
        role="tab"
        className="tab-label"
        aria-selected={active}
        title={title}
        data-status={status}
        onClick={() => activateTab(group, tab.id)}
        onAuxClick={(event) => {
          if (event.button === 1) closeTab(group, tab.id);
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          setMenu({ x: event.clientX, y: event.clientY });
        }}
      >
        {label}
      </button>
      <button type="button" className="tab-close" aria-label={`Close ${label}`} title="Close" onClick={() => closeTab(group, tab.id)}>
        ×
      </button>
      {menu && (
        <Menu
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
          items={[
            ["Close", () => closeTab(group, tab.id)],
            ...(canMove ? ([[group === 0 ? "Move to the right" : "Move to the left", () => moveTab(group, tab.id)]] as [string, () => void][]) : []),
          ]}
        />
      )}
    </div>
  );
}

function TabBar({ group, index, groups }: { group: Group; index: number; groups: number }) {
  const splitTab = useApp((s) => s.splitTab);
  const canMove = groups > 1 || group.tabs.length > 1;
  return (
    <div className="tab-bar">
      <div className="tab-list" role="tablist" aria-label={groups > 1 ? (index === 0 ? "Left tabs" : "Right tabs") : "Tabs"}>
        {group.tabs.map((tab) => (
          <TabButton key={tab.id} tab={tab} group={index} active={tab.id === group.active} canMove={canMove} />
        ))}
      </div>
      {groups < MAX_GROUPS && (
        <button type="button" className="icon tab-split" aria-label="Split to the right" title="Split to the right" onClick={() => splitTab()}>
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
  focused: boolean;
  client: ServiceClient;
  onClose(): void;
}

/** One group: its tab bar and its active tab's contents. */
function GroupView({
  group,
  index,
  groups,
  focused,
  renderSession,
}: {
  group: Group;
  index: number;
  groups: number;
  focused: boolean;
  renderSession(props: Omit<SessionPaneProps, "client">): ReactNode;
}) {
  const focusGroup = useApp((s) => s.focusGroup);
  const closeTab = useApp((s) => s.closeTab);
  const sessions = useApp((s) => s.sessions);
  const tab = group.tabs.find((t) => t.id === group.active);

  return (
    <section
      className="tab-group"
      data-focused={focused || undefined}
      aria-label={groups > 1 ? (index === 0 ? "Left group" : "Right group") : undefined}
      onPointerDownCapture={() => {
        if (!focused) focusGroup(index);
      }}
      onFocusCapture={() => {
        if (!focused) focusGroup(index);
      }}
    >
      <TabBar group={group} index={index} groups={groups} />
      <div className="tab-body" role="tabpanel">
        {tab?.content.kind === "session"
          ? renderSession({
              session: sessions[tab.content.sessionId],
              sessionId: tab.content.sessionId,
              tabKey: tab.id,
              focused,
              onClose: () => closeTab(index, tab.id),
            })
          : tab?.content.kind === "file" && <FileTab key={tab.id} file={tab.content.file} />}
      </div>
    </section>
  );
}

/** The main area's tab groups. With no tabs at all, `empty` shows instead. */
export function Workbench({
  empty,
  renderSession,
}: {
  empty: ReactNode;
  renderSession(props: Omit<SessionPaneProps, "client">): ReactNode;
}) {
  const layout = useApp(layoutOf);
  if (layout.groups.length === 0) return <>{empty}</>;
  return (
    <div className="workbench" data-groups={layout.groups.length}>
      {layout.groups.map((group, index) => (
        <GroupView
          key={index}
          group={group}
          index={index}
          groups={layout.groups.length}
          focused={index === layout.focused}
          renderSession={renderSession}
        />
      ))}
    </div>
  );
}

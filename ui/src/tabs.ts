/**
 * The main area's tabs. A tab is a container: it shows an agent session's
 * terminal, or a file through whichever file-viewer plugin is enabled.
 *
 * Tabs live in groups. There are one or two groups, shown side by side, and
 * one of them has focus: that is where opening a session or file lands, and
 * its active tab decides which session the header and right panel show.
 *
 * Everything here is a pure function of a Layout, so it is tested on its own.
 */

/** `workdir` is the session's working directory. `transcript` is one of the agent tool's log files for it. */
export type FileSource = "workdir" | "transcript";

export interface FileRef {
  sessionId: string;
  source: FileSource;
  /** Relative to the working directory, or to the transcript folder. */
  path: string;
}

export type TabContent = { kind: "session"; sessionId: string } | { kind: "file"; file: FileRef };

export interface Tab {
  id: string;
  content: TabContent;
}

export interface Group {
  tabs: Tab[];
  active: string | null;
}

export interface Layout {
  groups: Group[];
  focused: number;
}

/**
 * - `replace`: what clicking a session does. The session's tab is shown if it
 *   is open; otherwise the active session tab switches to it.
 * - `tab`: a new tab in the focused group, unless one already shows it.
 * - `side`: the other group, made if there is only one.
 */
export type OpenMode = "replace" | "tab" | "side";

export const MAX_GROUPS = 2;

let counter = 0;
export function newTabId(): string {
  counter += 1;
  return `t${Date.now().toString(36)}${counter}`;
}

export function emptyLayout(): Layout {
  return { groups: [], focused: 0 };
}

export function sameContent(a: TabContent, b: TabContent): boolean {
  if (a.kind === "session" && b.kind === "session") return a.sessionId === b.sessionId;
  if (a.kind === "file" && b.kind === "file") {
    return a.file.sessionId === b.file.sessionId && a.file.source === b.file.source && a.file.path === b.file.path;
  }
  return false;
}

/** The session a tab belongs to. A file tab belongs to the session whose directory or transcript it is from. */
export function sessionOf(content: TabContent): string {
  return content.kind === "session" ? content.sessionId : content.file.sessionId;
}

export function activeTab(group: Group | undefined): Tab | undefined {
  return group?.tabs.find((t) => t.id === group.active);
}

/** The focused group's active tab. */
export function focusedTab(layout: Layout): Tab | undefined {
  return activeTab(layout.groups[layout.focused]);
}

function withGroup(layout: Layout, index: number, group: Group): Layout {
  const groups = layout.groups.slice();
  groups[index] = group;
  return { ...layout, groups };
}

function insertAfterActive(group: Group, tab: Tab): Group {
  const at = group.tabs.findIndex((t) => t.id === group.active);
  const tabs = group.tabs.slice();
  tabs.splice(at < 0 ? tabs.length : at + 1, 0, tab);
  return { tabs, active: tab.id };
}

export function open(layout: Layout, content: TabContent, mode: OpenMode = "replace", id = newTabId()): Layout {
  if (layout.groups.length === 0) {
    return { groups: [{ tabs: [{ id, content }], active: id }], focused: 0 };
  }
  if (mode === "side") {
    if (layout.groups.length < MAX_GROUPS) {
      return { groups: [...layout.groups, { tabs: [{ id, content }], active: id }], focused: layout.groups.length };
    }
    return open({ ...layout, focused: 1 - layout.focused }, content, "tab", id);
  }

  const index = Math.min(layout.focused, layout.groups.length - 1);
  const group = layout.groups[index];
  const existing = group.tabs.find((t) => sameContent(t.content, content));
  if (existing) return withGroup({ ...layout, focused: index }, index, { ...group, active: existing.id });

  const current = activeTab(group);
  if (mode === "replace" && content.kind === "session" && current?.content.kind === "session") {
    const tabs = group.tabs.map((t) => (t.id === current.id ? { ...t, content } : t));
    return withGroup({ ...layout, focused: index }, index, { ...group, tabs });
  }
  return withGroup({ ...layout, focused: index }, index, insertAfterActive(group, { id, content }));
}

export function activate(layout: Layout, groupIndex: number, tabId: string): Layout {
  const group = layout.groups[groupIndex];
  if (!group?.tabs.some((t) => t.id === tabId)) return layout;
  return withGroup({ ...layout, focused: groupIndex }, groupIndex, { ...group, active: tabId });
}

export function focusGroup(layout: Layout, groupIndex: number): Layout {
  return groupIndex < layout.groups.length ? { ...layout, focused: groupIndex } : layout;
}

/** Drop empty groups, keeping focus on the same group where it survives. */
function compact(layout: Layout): Layout {
  const kept = layout.groups.map((g, i) => [g, i] as const).filter(([g]) => g.tabs.length > 0);
  const focusedAt = kept.findIndex(([, i]) => i === layout.focused);
  return { groups: kept.map(([g]) => g), focused: Math.max(0, focusedAt < 0 ? kept.length - 1 : focusedAt) };
}

export function close(layout: Layout, groupIndex: number, tabId: string): Layout {
  const group = layout.groups[groupIndex];
  const at = group?.tabs.findIndex((t) => t.id === tabId) ?? -1;
  if (at < 0) return layout;
  const tabs = group.tabs.filter((t) => t.id !== tabId);
  // Closing the active tab shows its right neighbour, or else its left one.
  const active = group.active === tabId ? (tabs[Math.min(at, tabs.length - 1)]?.id ?? null) : group.active;
  return compact(withGroup(layout, groupIndex, { tabs, active }));
}

/** Close every tab that belongs to a session, as when the session is closed. */
export function closeSession(layout: Layout, sessionId: string): Layout {
  let next = layout;
  for (const tab of layout.groups.flatMap((g) => g.tabs)) {
    if (sessionOf(tab.content) !== sessionId) continue;
    const index = findGroup(next, tab.id);
    if (index !== undefined) next = close(next, index, tab.id);
  }
  return next;
}

function findGroup(layout: Layout, tabId: string): number | undefined {
  const index = layout.groups.findIndex((g) => g.tabs.some((t) => t.id === tabId));
  return index < 0 ? undefined : index;
}

/** Show the focused tab in a second group beside it too. */
export function split(layout: Layout, id = newTabId()): Layout {
  const tab = focusedTab(layout);
  if (!tab || layout.groups.length >= MAX_GROUPS) return layout;
  return open(layout, tab.content, "side", id);
}

/** Move a tab to the other group, making it if there is only one. */
export function moveToOther(layout: Layout, groupIndex: number, tabId: string): Layout {
  const tab = layout.groups[groupIndex]?.tabs.find((t) => t.id === tabId);
  if (!tab) return layout;
  // A lone tab has nowhere to move from.
  if (layout.groups.length === 1 && layout.groups[0].tabs.length === 1) return layout;
  const target = layout.groups.length === 1 ? 1 : 1 - groupIndex;
  const source = layout.groups[groupIndex];
  const at = source.tabs.findIndex((t) => t.id === tabId);
  const left = source.tabs.filter((t) => t.id !== tabId);
  const groups = layout.groups.slice();
  groups[groupIndex] = {
    tabs: left,
    active: source.active === tabId ? (left[Math.min(at, left.length - 1)]?.id ?? null) : source.active,
  };
  const destination = groups[target] ?? { tabs: [], active: null };
  const existing = destination.tabs.find((t) => sameContent(t.content, tab.content));
  groups[target] = existing ? { ...destination, active: existing.id } : insertAfterActive(destination, tab);
  return compact({ groups, focused: target });
}

/** A saved layout from storage, or null if it isn't one. */
export function parseLayout(value: unknown): Layout | null {
  if (!value || typeof value !== "object") return null;
  const { groups, focused } = value as Layout;
  if (!Array.isArray(groups) || typeof focused !== "number") return null;
  const valid = groups.every(
    (g) =>
      g && Array.isArray(g.tabs) &&
      g.tabs.every((t) => typeof t?.id === "string" && (t.content?.kind === "session" || t.content?.kind === "file")),
  );
  if (!valid) return null;
  return compact({ groups: groups.slice(0, MAX_GROUPS), focused: Math.min(focused, MAX_GROUPS - 1) });
}

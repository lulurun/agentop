/**
 * The main area's tabs. A tab is a container: it shows an agent session's
 * terminal, a file through whichever file-viewer plugin is enabled, a
 * service's Status, the History list, or a view of a plugin's workspace.
 * Every workspace's main area is tabs (design 10.7.3), each with a layout of its own.
 *
 * Tabs live in groups. There are one or two groups, shown side by side, and
 * one of them has focus: its active tab decides which session the header and
 * right panel show. Clicking a session opens it in the left group (the only
 * one, when there is one), and clicking a file opens it in the right group,
 * made if needed. Tabs can then be moved between the groups freely.
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
  /** `diff` shows the file's changes against HEAD instead of its text, as a file from the Changed list does. */
  view?: "diff";
}

export type TabContent =
  | { kind: "session"; sessionId: string }
  | { kind: "file"; file: FileRef }
  | { kind: "status" }
  | { kind: "history" }
  /** One of a plugin's views, named by the plugin's workspace (`UiPlugin.workspace.views`). */
  | { kind: "plugin"; plugin: string; view: string; key?: string };

export type TabKind = TabContent["kind"];

export interface Tab {
  id: string;
  content: TabContent;
  /** A workspace's default tab: it can be moved, but not closed. */
  pinned?: boolean;
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
 * - `left`: what opening Status does. The tab is shown if it is open in either
 *   group; otherwise it gets a new tab in the left group.
 * - `replace`: what clicking a session does. The session is shown if it is open
 *   in either group; otherwise the left group's active session tab switches to it.
 * - `tab`: a new tab in the left group, unless one there already shows it.
 * - `right`: what clicking a file does. The file is shown if it is open in
 *   either group; otherwise it gets a new tab in the right group, made if needed.
 * - `side`: the group other than the focused one, made if there is only one.
 */
export type OpenMode = "replace" | "tab" | "left" | "right" | "side";

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
    return (
      a.file.sessionId === b.file.sessionId &&
      a.file.source === b.file.source &&
      a.file.path === b.file.path &&
      (a.file.view ?? null) === (b.file.view ?? null)
    );
  }
  if (a.kind === "plugin" && b.kind === "plugin") {
    return a.plugin === b.plugin && a.view === b.view && (a.key ?? null) === (b.key ?? null);
  }
  return a.kind === b.kind && (a.kind === "status" || a.kind === "history");
}

/**
 * The session a tab belongs to. A file tab belongs to the session whose
 * directory or transcript it is from; other kinds belong to none.
 */
export function sessionOf(content: TabContent): string | undefined {
  if (content.kind === "session") return content.sessionId;
  if (content.kind === "file") return content.file.sessionId;
  return undefined;
}

/** Only sessions and files make sense twice, side by side. */
export function canSplit(content: TabContent): boolean {
  return content.kind === "session" || content.kind === "file";
}

/** A layout of one pinned tab, the default of a workspace that opens with one. */
export function pinnedLayout(content: TabContent, id = "home"): Layout {
  return { groups: [{ tabs: [{ id, content, pinned: true }], active: id }], focused: 0 };
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
  if (mode === "replace" || mode === "right" || mode === "left") {
    for (const [index, group] of layout.groups.entries()) {
      const existing = group.tabs.find((t) => sameContent(t.content, content));
      if (existing) return withGroup({ ...layout, focused: index }, index, { ...group, active: existing.id });
    }
  }
  if (mode === "side" || mode === "right") {
    const target = mode === "right" ? 1 : 1 - layout.focused;
    if (layout.groups.length < MAX_GROUPS) {
      return { groups: [...layout.groups, { tabs: [{ id, content }], active: id }], focused: layout.groups.length };
    }
    const group = layout.groups[target];
    const existing = group.tabs.find((t) => sameContent(t.content, content));
    return withGroup({ ...layout, focused: target }, target, existing ? { ...group, active: existing.id } : insertAfterActive(group, { id, content }));
  }

  const group = layout.groups[0];
  const existing = group.tabs.find((t) => sameContent(t.content, content));
  if (existing) return withGroup({ ...layout, focused: 0 }, 0, { ...group, active: existing.id });

  const current = activeTab(group);
  if (mode === "replace" && content.kind === "session" && current?.content.kind === "session") {
    const tabs = group.tabs.map((t) => (t.id === current.id ? { ...t, content } : t));
    return withGroup({ ...layout, focused: 0 }, 0, { ...group, tabs });
  }
  return withGroup({ ...layout, focused: 0 }, 0, insertAfterActive(group, { id, content }));
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
  if (at < 0 || group.tabs[at].pinned) return layout;
  const tabs = group.tabs.filter((t) => t.id !== tabId);
  // Closing the active tab shows its right neighbour, or else its left one.
  const active = group.active === tabId ? (tabs[Math.min(at, tabs.length - 1)]?.id ?? null) : group.active;
  return compact(withGroup(layout, groupIndex, { tabs, active }));
}

/** Close every tab that belongs to a session, as when the session is closed. */
export function closeSession(layout: Layout, sessionId: string): Layout {
  return closeWhere(layout, (content) => sessionOf(content) === sessionId);
}

/** Close every tab whose content matches, pinned ones too, as when a plugin is turned off. */
export function closeWhere(layout: Layout, matches: (content: TabContent) => boolean): Layout {
  let next = layout;
  for (const tab of layout.groups.flatMap((g) => g.tabs)) {
    if (!matches(tab.content)) continue;
    const index = findGroup(next, tab.id);
    if (index === undefined) continue;
    next = close(unpin(next, index, tab.id), index, tab.id);
  }
  return next;
}

function unpin(layout: Layout, groupIndex: number, tabId: string): Layout {
  const group = layout.groups[groupIndex];
  return withGroup(layout, groupIndex, { ...group, tabs: group.tabs.map((t) => (t.id === tabId ? { ...t, pinned: false } : t)) });
}

function findGroup(layout: Layout, tabId: string): number | undefined {
  const index = layout.groups.findIndex((g) => g.tabs.some((t) => t.id === tabId));
  return index < 0 ? undefined : index;
}

/** Show the focused tab in a second group beside it too. */
export function split(layout: Layout, id = newTabId()): Layout {
  const tab = focusedTab(layout);
  if (!tab || !canSplit(tab.content) || layout.groups.length >= MAX_GROUPS) return layout;
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

function validContent(content: unknown): boolean {
  if (!content || typeof content !== "object") return false;
  const c = content as Record<string, unknown>;
  switch (c.kind) {
    case "session":
      return typeof c.sessionId === "string";
    case "file":
      return !!c.file && typeof (c.file as FileRef).path === "string";
    case "status":
    case "history":
      return true;
    case "plugin":
      return typeof c.plugin === "string" && typeof c.view === "string";
    default:
      return false;
  }
}

/** A saved layout from storage, or null if it isn't one. */
export function parseLayout(value: unknown): Layout | null {
  if (!value || typeof value !== "object") return null;
  const { groups, focused } = value as Layout;
  if (!Array.isArray(groups) || typeof focused !== "number") return null;
  const valid = groups.every(
    (g) => g && Array.isArray(g.tabs) && g.tabs.every((t) => typeof t?.id === "string" && validContent(t.content)),
  );
  if (!valid) return null;
  return compact({ groups: groups.slice(0, MAX_GROUPS), focused: Math.min(focused, MAX_GROUPS - 1) });
}

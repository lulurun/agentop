/**
 * The middle panel's tabs (docs/0_10_ui.md): a left and a right area, each a tab
 * bar showing one buffer at a time. Pure functions of a Layout, tested on their own.
 */

/** `workdir` is the session's working directory, `memory` the notes the agent tool keeps for its project, `transcript` one of the tool's log files for it. */
export type FileSource = "workdir" | "memory" | "transcript";

export interface FileRef {
  sessionId: string;
  source: FileSource;
  /** Relative to the source's folder. */
  path: string;
  /** `diff` shows the file's changes against HEAD instead of its text, as a file from the Changed list does. */
  view?: "diff";
}

export type TabContent =
  | { kind: "session"; sessionId: string }
  | { kind: "file"; file: FileRef }
  | { kind: "status" }
  /** Sessions' start buffer: a session from its first prompt (docs/0_04_start.md). */
  | { kind: "start" }
  /** A past session from the History list, by its ID there (`HistoryItem.id`), to resume or delete. */
  | { kind: "past"; historyId: string }
  /** One of a plugin's views, named by the plugin's feature (`UiPlugin.feature.views`). */
  | { kind: "plugin"; plugin: string; view: string; key?: string };

export type TabKind = TabContent["kind"];

export interface Tab {
  id: string;
  content: TabContent;
  /** A feature's default tab: it can be moved, but not closed. */
  pinned?: boolean;
}

export interface Group {
  tabs: Tab[];
  active: string | null;
}

export type Area = "left" | "right";
export const AREAS: readonly Area[] = ["left", "right"];

/** The middle panel: a left area, a right area, either, or neither (the background shows). */
export interface Layout {
  left: Group | null;
  right: Group | null;
  /** The active area, which tab keys act on. */
  focused: Area;
}

/**
 * - `replace`: take the place of the area's tab of the same kind, as clicking a session does.
 * - `tab`: a new tab after the area's active one.
 * - `also`: in this area even when it is open in the other, as asked for by name ("Open in the
 *   right area"); only a session or a file, which make sense twice.
 *
 * Otherwise content already open in either area is shown where it is.
 */
export type OpenMode = "replace" | "tab" | "also";

export const other = (area: Area): Area => (area === "left" ? "right" : "left");

let counter = 0;
export function newTabId(): string {
  counter += 1;
  return `t${Date.now().toString(36)}${counter}`;
}

export function emptyLayout(): Layout {
  return { left: null, right: null, focused: "left" };
}

/** The areas that are there, left first. */
export function areasOf(layout: Layout): [Area, Group][] {
  return AREAS.flatMap((area) => (layout[area] ? [[area, layout[area]!] as [Area, Group]] : []));
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
  if (a.kind === "past" && b.kind === "past") return a.historyId === b.historyId;
  return a.kind === b.kind && (a.kind === "status" || a.kind === "start");
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

export const START_TAB: Tab = { id: "start", content: { kind: "start" } };

/** The layout with a feature's pinned tab in it (Sessions' start buffer): first in the left area, unless it was moved. The same layout when it is. */
export function withPinnedTab(layout: Layout, pinned: Tab = START_TAB): Layout {
  if (findOpen(layout, pinned.content)) return layout;
  const left = layout.left;
  if (!left) return { ...layout, left: { tabs: [pinned], active: pinned.id }, focused: layout.right ? layout.focused : "left" };
  return { ...layout, left: { ...left, tabs: [pinned, ...left.tabs], active: left.active ?? pinned.id } };
}

/** A layout of one pinned tab, the default of a feature that opens with one. */
export function pinnedLayout(content: TabContent, id = "home"): Layout {
  return { left: { tabs: [{ id, content, pinned: true }], active: id }, right: null, focused: "left" };
}

export function activeTab(group: Group | null | undefined): Tab | undefined {
  return group?.tabs.find((t) => t.id === group.active);
}

/** The active area's active tab. */
export function focusedTab(layout: Layout): Tab | undefined {
  return activeTab(layout[layout.focused]);
}

function withArea(layout: Layout, area: Area, group: Group | null): Layout {
  return { ...layout, [area]: group };
}

function insertAfterActive(group: Group, tab: Tab): Group {
  const at = group.tabs.findIndex((t) => t.id === group.active);
  const tabs = group.tabs.slice();
  tabs.splice(at < 0 ? tabs.length : at + 1, 0, tab);
  return { tabs, active: tab.id };
}

/** What a tab of this content can take the place of with `replace`, or null for always a new tab. */
function slotOf(content: TabContent): string | null {
  if (content.kind === "session" || content.kind === "past") return content.kind;
  if (content.kind === "plugin") return `plugin:${content.plugin}:${content.view}`;
  return null;
}

function findOpen(layout: Layout, content: TabContent): [Area, Tab] | undefined {
  for (const [area, group] of areasOf(layout)) {
    const tab = group.tabs.find((t) => sameContent(t.content, content));
    if (tab) return [area, tab];
  }
  return undefined;
}

export function open(layout: Layout, content: TabContent, area: Area, mode: OpenMode = "replace", id = newTabId()): Layout {
  const also = mode === "also" && canSplit(content);
  const found = also ? findOpen({ ...layout, [other(area)]: null }, content) : findOpen(layout, content);
  if (found) {
    if (content.kind === "start" && found[0] !== area) return moveToOther(layout, found[0], found[1].id);
    return activate(layout, found[0], found[1].id);
  }
  const group = layout[area];
  if (!group) return { ...withArea(layout, area, { tabs: [{ id, content }], active: id }), focused: area };
  const slot = slotOf(content);
  if (mode !== "tab" && slot) {
    // The active tab of the same kind; for a plugin's view, any tab of it in the area.
    const current = activeTab(group);
    const target =
      current && slotOf(current.content) === slot ? current
      : content.kind === "plugin" ? group.tabs.find((t) => slotOf(t.content) === slot)
      : undefined;
    if (target) {
      const tabs = group.tabs.map((t) => (t.id === target.id ? { ...t, content } : t));
      return { ...withArea(layout, area, { tabs, active: target.id }), focused: area };
    }
  }
  return { ...withArea(layout, area, insertAfterActive(group, { id, content })), focused: area };
}

export function activate(layout: Layout, area: Area, tabId: string): Layout {
  const group = layout[area];
  if (!group?.tabs.some((t) => t.id === tabId)) return layout;
  return { ...withArea(layout, area, { ...group, active: tabId }), focused: area };
}

/** The active area's tab at `index`, made active. */
export function activateAt(layout: Layout, index: number): Layout {
  const tab = layout[layout.focused]?.tabs[index];
  return tab ? activate(layout, layout.focused, tab.id) : layout;
}

export function focusArea(layout: Layout, area: Area): Layout {
  return layout[area] && layout.focused !== area ? { ...layout, focused: area } : layout;
}

/** Drop an empty area; the active area moves to the other one if it went. */
function compact(layout: Layout): Layout {
  const left = layout.left?.tabs.length ? layout.left : null;
  const right = layout.right?.tabs.length ? layout.right : null;
  const focused = (layout.focused === "left" ? left : right) ? layout.focused : left ? "left" : right ? "right" : "left";
  return { left, right, focused };
}

export function close(layout: Layout, area: Area, tabId: string): Layout {
  const group = layout[area];
  const at = group?.tabs.findIndex((t) => t.id === tabId) ?? -1;
  if (!group || at < 0 || group.tabs[at].pinned) return layout;
  const tabs = group.tabs.filter((t) => t.id !== tabId);
  // Closing the active tab shows its right neighbour, or else its left one.
  const active = group.active === tabId ? (tabs[Math.min(at, tabs.length - 1)]?.id ?? null) : group.active;
  return compact(withArea(layout, area, { tabs, active }));
}

/** Close the active area's tab. */
export function closeActive(layout: Layout): Layout {
  const tab = focusedTab(layout);
  return tab ? close(layout, layout.focused, tab.id) : layout;
}

/** Close every tab that belongs to a session, as when the session is closed. */
export function closeSession(layout: Layout, sessionId: string): Layout {
  return closeWhere(layout, (content) => sessionOf(content) === sessionId);
}

/** Close every tab whose content matches, pinned ones too, as when a plugin is turned off. */
export function closeWhere(layout: Layout, matches: (content: TabContent) => boolean): Layout {
  let next = layout;
  for (const [area, group] of areasOf(layout)) {
    for (const tab of group.tabs) {
      if (!matches(tab.content)) continue;
      const now = next[area];
      if (!now) continue;
      next = close(withArea(next, area, { ...now, tabs: now.tabs.map((t) => (t.id === tab.id ? { ...t, pinned: false } : t)) }), area, tab.id);
    }
  }
  return next;
}

/** Replace every tab's content that matches, as when a resumed past session becomes a session. */
export function mapContent(layout: Layout, change: (content: TabContent) => TabContent): Layout {
  const map = (group: Group | null) => group && { ...group, tabs: group.tabs.map((t) => ({ ...t, content: change(t.content) })) };
  return { ...layout, left: map(layout.left), right: map(layout.right) };
}

/** Show the active tab in the other area too, when there is no other area yet. */
export function split(layout: Layout, id = newTabId()): Layout {
  const tab = focusedTab(layout);
  const to = other(layout.focused);
  if (!tab || !canSplit(tab.content) || layout[to]) return layout;
  return { ...withArea(layout, to, { tabs: [{ ...tab, id }], active: id }), focused: to };
}

/** Move a tab to the other area, making it if it isn't there. */
export function moveToOther(layout: Layout, area: Area, tabId: string): Layout {
  const source = layout[area];
  const tab = source?.tabs.find((t) => t.id === tabId);
  if (!source || !tab) return layout;
  const to = other(area);
  const at = source.tabs.findIndex((t) => t.id === tabId);
  const rest = source.tabs.filter((t) => t.id !== tabId);
  const from = { tabs: rest, active: source.active === tabId ? (rest[Math.min(at, rest.length - 1)]?.id ?? null) : source.active };
  const destination = layout[to] ?? { tabs: [], active: null };
  const existing = destination.tabs.find((t) => sameContent(t.content, tab.content));
  const moved = existing ? { ...destination, active: existing.id } : insertAfterActive(destination, tab);
  return compact({ ...withArea(withArea(layout, area, from), to, moved), focused: to });
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
    case "start":
      return true;
    case "past":
      return typeof c.historyId === "string";
    case "plugin":
      return typeof c.plugin === "string" && typeof c.view === "string";
    default:
      return false;
  }
}

function validGroup(g: unknown): g is Group {
  const group = g as Group;
  return !!group && Array.isArray(group.tabs) && group.tabs.every((t) => typeof t?.id === "string" && validContent(t.content));
}

/** A saved layout from storage, or null if it isn't one. Reads the older `{groups, focused}` too. */
export function parseLayout(value: unknown): Layout | null {
  if (!value || typeof value !== "object") return null;
  const saved = value as Record<string, unknown>;
  if (Array.isArray(saved.groups)) {
    const groups = saved.groups as unknown[];
    if (!groups.every(validGroup)) return null;
    return parseLayout({ left: groups[0] ?? null, right: groups[1] ?? null, focused: saved.focused === 1 ? "right" : "left" });
  }
  const { left, right, focused } = saved;
  if ((left !== null && !validGroup(left)) || (right !== null && !validGroup(right)) || (focused !== "left" && focused !== "right")) return null;
  const unpinStart = (group: Group | null) => group && ({ ...group, tabs: group.tabs.map((tab) => tab.content.kind === "start" ? { ...tab, pinned: false } : tab) });
  return compact({ left: unpinStart(left as Group | null), right: unpinStart(right as Group | null), focused });
}

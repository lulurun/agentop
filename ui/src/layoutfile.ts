/**
 * A project's layout file, `.project/layout.json` (docs/0_10_ui.md, Project layouts): its format,
 * a lenient reader for files edited by hand, and the queue that loads and saves it per project.
 */
import { newTabId, type Area, type FileSource, type Group, type Layout, type Tab, type TabContent } from "./tabs";

export const LAYOUT_VERSION = 1;

export interface AreaFile {
  open: boolean;
  tabs: { id: string; content: TabContent }[];
  active: string | null;
}

export interface LayoutFile {
  version: typeof LAYOUT_VERSION;
  "left-panel": { open: boolean };
  "mid-panel": { left: AreaFile; right: AreaFile };
  "right-panel": { open: boolean };
  focused: Area;
}

/** What the app shows of one project: its middle panel's tabs, and whether each side panel is open. */
export interface ProjectView {
  layout: Layout;
  leftOpen: boolean;
  rightOpen: boolean;
}

export function defaultView(): ProjectView {
  return { layout: { left: null, right: null, focused: "left" }, leftOpen: true, rightOpen: true };
}

function areaFile(group: Group | null): AreaFile {
  if (!group?.tabs.length) return { open: false, tabs: [], active: null };
  return { open: true, tabs: group.tabs.map(({ id, content }) => ({ id, content })), active: group.active };
}

export function toFile(view: ProjectView): LayoutFile {
  return {
    version: LAYOUT_VERSION,
    "left-panel": { open: view.leftOpen },
    "mid-panel": { left: areaFile(view.layout.left), right: areaFile(view.layout.right) },
    "right-panel": { open: view.rightOpen },
    focused: view.layout.focused,
  };
}

const SOURCES: FileSource[] = ["workdir", "memory", "transcript"];
const str = (value: unknown, max = 200): value is string => typeof value === "string" && value.length > 0 && value.length <= max;

/** A tab's content as the service accepts it, with nothing else in it; null if it isn't one. */
export function cleanContent(value: unknown): TabContent | null {
  if (!value || typeof value !== "object") return null;
  const c = value as Record<string, unknown>;
  switch (c.kind) {
    case "session":
      return str(c.sessionId) ? { kind: "session", sessionId: c.sessionId } : null;
    case "status":
    case "start":
      return { kind: c.kind };
    case "past":
      return str(c.historyId) ? { kind: "past", historyId: c.historyId } : null;
    case "plugin":
      if (!str(c.plugin) || !str(c.view)) return null;
      return str(c.key, 4096) ? { kind: "plugin", plugin: c.plugin, view: c.view, key: c.key } : { kind: "plugin", plugin: c.plugin, view: c.view };
    case "file": {
      const f = c.file as Record<string, unknown> | undefined;
      if (!f || !str(f.sessionId) || typeof f.path !== "string" || f.path.length > 4096 || !SOURCES.includes(f.source as FileSource)) return null;
      const file = { sessionId: f.sessionId, source: f.source as FileSource, path: f.path };
      return { kind: "file", file: f.view === "diff" ? { ...file, view: "diff" } : file };
    }
    default:
      return null;
  }
}

const MAX_TABS = 64;

function readArea(value: unknown, seen: Set<string>): Group | null {
  if (!value || typeof value !== "object") return null;
  const area = value as Record<string, unknown>;
  if (area.open === false || !Array.isArray(area.tabs)) return null;
  const tabs: Tab[] = [];
  for (const raw of area.tabs) {
    const content = cleanContent((raw as Record<string, unknown> | null)?.content);
    if (!content || tabs.length >= MAX_TABS) continue;
    let id = (raw as Record<string, unknown>).id;
    if (!str(id, 64) || seen.has(id)) id = newTabId();
    seen.add(id as string);
    tabs.push({ id: id as string, content });
  }
  if (!tabs.length) return null;
  const active = tabs.some((t) => t.id === area.active) ? (area.active as string) : tabs[0].id;
  return { tabs, active };
}

/**
 * A layout file as read: `null` for no file or one that isn't a layout (the defaults show),
 * `"newer"` for one from a later version of the app, which this one must not write over.
 * Anything wrong inside it, such as one bad tab, is left out rather than losing the rest.
 */
export function fromFile(value: unknown): ProjectView | null | "newer" {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const file = value as Record<string, unknown>;
  if (typeof file.version === "number" && file.version > LAYOUT_VERSION) return "newer";
  if (file.version !== LAYOUT_VERSION) return null;
  const open = (panel: unknown) => (panel as { open?: unknown } | null)?.open !== false;
  const mid = (file["mid-panel"] ?? {}) as Record<string, unknown>;
  const seen = new Set<string>();
  const left = readArea(mid.left, seen);
  const right = readArea(mid.right, seen);
  const wanted: Area = file.focused === "right" ? "right" : "left";
  const focused: Area = (wanted === "left" ? left : right) ? wanted : left ? "left" : right ? "right" : "left";
  return { layout: { left, right, focused }, leftOpen: open(file["left-panel"]), rightOpen: open(file["right-panel"]) };
}

export function sameView(a: ProjectView, b: ProjectView): boolean {
  return JSON.stringify(toFile(a)) === JSON.stringify(toFile(b));
}

export interface LayoutIO {
  /** The file as the service has it, or null for none. Throws when it can't be read. */
  read(connection: string, project: string): Promise<unknown>;
  write(connection: string, project: string, file: LayoutFile, keepalive?: boolean): Promise<void>;
}

export type LoadResult =
  | { kind: "loaded"; file: unknown }
  /** Edited here since the load began, or before it and not saved yet: what is shown is fresher than the file. */
  | { kind: "stale" }
  | { kind: "failed"; error: unknown };

/**
 * Loads and saves each project's file on its own queue, keyed by connection and project, so
 * a slow reply can only ever land on its own project, and a load or save never passes an
 * earlier one. Saves wait a moment to gather edits, and only go once the file was read
 * (or found missing), so nothing is written over a file this page hasn't seen.
 */
export class LayoutSync {
  private edits = new Map<string, number>();
  private pending = new Map<string, LayoutFile>();
  private timers = new Map<string, ReturnType<typeof setTimeout>>();
  private chains = new Map<string, Promise<unknown>>();
  private writable = new Set<string>();

  constructor(
    private readonly io: LayoutIO,
    private readonly delayMs = 400,
  ) {}

  private key = (connection: string, project: string) => `${connection}\n${project}`;

  private enqueue<T>(key: string, run: () => Promise<T>): Promise<T> {
    const next = (this.chains.get(key) ?? Promise.resolve()).catch(() => undefined).then(run);
    this.chains.set(key, next);
    return next;
  }

  /** The view was changed here: save it soon. */
  edited(connection: string, project: string, file: LayoutFile): void {
    const key = this.key(connection, project);
    this.edits.set(key, (this.edits.get(key) ?? 0) + 1);
    this.pending.set(key, file);
    if (this.writable.has(key)) this.schedule(connection, project);
  }

  private schedule(connection: string, project: string) {
    const key = this.key(connection, project);
    clearTimeout(this.timers.get(key));
    this.timers.set(key, setTimeout(() => void this.flush(connection, project), this.delayMs));
  }

  /** Saves may go from now on; one waiting goes soon. */
  allowWrites(connection: string, project: string): void {
    const key = this.key(connection, project);
    this.writable.add(key);
    if (this.pending.has(key)) this.schedule(connection, project);
  }

  /** As for a file from a newer app: edits stay on this page. */
  forbidWrites(connection: string, project: string): void {
    const key = this.key(connection, project);
    this.writable.delete(key);
    clearTimeout(this.timers.get(key));
  }

  /** Forget an edit not saved yet. */
  discard(connection: string, project: string): void {
    const key = this.key(connection, project);
    this.pending.delete(key);
    clearTimeout(this.timers.get(key));
    this.timers.delete(key);
  }

  /** Save the waiting edit now. Resolves once every save queued so far is done; a failed one waits for the next flush. */
  flush(connection: string, project: string, keepalive = false): Promise<void> {
    const key = this.key(connection, project);
    clearTimeout(this.timers.get(key));
    this.timers.delete(key);
    const file = this.pending.get(key);
    if (!file || !this.writable.has(key)) return (this.chains.get(key) ?? Promise.resolve()).then(() => undefined, () => undefined);
    this.pending.delete(key);
    return this.enqueue(key, () => this.io.write(connection, project, file, keepalive)).catch((error) => {
      if (!this.pending.has(key)) this.pending.set(key, file);
      console.warn(`Could not save the layout of ${project}`, error);
    });
  }

  /** Flush every project of a connection, or of all of them. */
  flushAll(connection?: string, keepalive = false): Promise<void> {
    const keys = new Set([...this.pending.keys(), ...this.timers.keys()]);
    const runs = [...keys]
      .map((key) => key.split("\n") as [string, string])
      .filter(([c]) => connection === undefined || c === connection)
      .map(([c, p]) => this.flush(c, p, keepalive));
    return Promise.all(runs).then(() => undefined);
  }

  /** Read the project's file, after any save of it still on its way. */
  async load(connection: string, project: string): Promise<LoadResult> {
    const key = this.key(connection, project);
    const before = this.edits.get(key) ?? 0;
    const unsaved = this.pending.has(key) && !this.writable.has(key);
    await this.flush(connection, project);
    let file: unknown;
    try {
      file = await this.enqueue(key, () => this.io.read(connection, project));
    } catch (error) {
      return { kind: "failed", error };
    }
    if (unsaved || (this.edits.get(key) ?? 0) !== before) return { kind: "stale" };
    return { kind: "loaded", file };
  }
}

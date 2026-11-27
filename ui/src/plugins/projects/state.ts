/** The Projects feature's shared state: its listing, opening a project, and the Project creation buffer. */
import { create } from "zustand";
import { layoutIn, useApp, type ViewOwner } from "../../store";
import { areasOf, type TabContent } from "../../tabs";
import type { PluginContext } from "../types";

export interface Project {
  name: string;
  owner: string;
  folder: string;
  created_at: number | null;
}

export const FEATURE = "plugin:projects" as const;
export const PLUGIN_ID = "projects";
export const CREATOR: TabContent = { kind: "plugin", plugin: PLUGIN_ID, view: "new" };
export const isCreator = (content: TabContent) => content.kind === "plugin" && content.plugin === PLUGIN_ID && content.view === "new";

/** The project names the service takes (projects.py). */
export const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,95}$/;

/** The plugin's projects, refreshed by every component that asks. */
export const listing = (() => {
  const listeners = new Set<() => void>();
  let projects: Project[] | null = null;
  return {
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    snapshot: () => projects,
    async load(plugin: PluginContext) {
      projects = await plugin.client.get<Project[]>("/projects");
      listeners.forEach((l) => l());
      return projects;
    },
  };
})();

/**
 * Show a project as it was left; one with no tabs yet, or when asked, gets its Coordinator's. Captured before any
 * await, so its Coordinator's tab goes into this project's own tabs on this service, whatever is shown later.
 */
export async function openProject(service: string, name: string, coordinator = false): Promise<void> {
  const app = useApp.getState;
  if (app().activeId !== service) return;
  const owner: ViewOwner = { connection: service, feature: FEATURE, project: name };
  await app().setProject(FEATURE, name);
  if (!coordinator && areasOf(layoutIn(app(), owner)).length > 0) return;
  const rows = await app().act((client) => client.sessions());
  if (app().activeId !== service) return;
  const row = rows?.find((s) => s.project === name && s.display_name === "Coordinator" && (s.status === "running" || s.status === "starting"));
  if (!row) return;
  useApp.setState((s) => ({ sessions: { ...s.sessions, [row.id]: row } }));
  app().openTabIn(owner, { kind: "session", sessionId: row.id }, "left");
}

/** What the creation buffer holds on one service, kept while its tab is away or the request is out. */
export interface Draft {
  name: string;
  tool: string;
  error: string;
  busy: boolean;
}

export const EMPTY_DRAFT: Draft = { name: "", tool: "", error: "", busy: false };

export const useDrafts = create<{ drafts: Record<string, Draft>; focusAsked: number }>(() => ({ drafts: {}, focusAsked: 0 }));

export function updateDraft(connection: string, change: Partial<Draft>): void {
  useDrafts.setState((s) => ({ drafts: { ...s.drafts, [connection]: { ...(s.drafts[connection] ?? EMPTY_DRAFT), ...change } } }));
}

const narrow = () => window.innerWidth < 900;

/** Show the one creation buffer in the left area of Projects with none picked, opening that area if shut, and focus its name. */
export async function openCreator(): Promise<void> {
  const app = useApp.getState;
  const service = app().activeId;
  app().setFeature(FEATURE);
  await app().setProject(FEATURE, null);
  if (app().activeId !== service) return;
  const owner: ViewOwner = { connection: service, feature: FEATURE };
  const right = layoutIn(app(), owner).right?.tabs.find((t) => isCreator(t.content));
  if (right) app().moveTab(FEATURE, "right", right.id);
  else app().openTabIn(owner, CREATOR, "left");
  app().focusAreaIn(owner, "left");
  // On a narrow screen the left panel covers the buffer.
  if (narrow() && app().leftOpen) app().toggleLeft();
  useDrafts.setState((s) => ({ focusAsked: s.focusAsked + 1 }));
}

/** Projects' default Coordinator tool, as the service picks one when none is chosen (coordinator.py). */
export function preferredTool(installed: string[]): string {
  return ["codex", "claude"].find((t) => installed.includes(t)) ?? installed[0] ?? "";
}

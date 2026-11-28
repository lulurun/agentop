/** The Projects feature's shared state: its listing, opening a project, and the Project creation buffer. */
import { create } from "zustand";
import type { Session } from "../../api/client";
import { layoutIn, useApp, type ViewOwner } from "../../store";
import type { TabContent } from "../../tabs";
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

export const coordinatorTab = (project: string): TabContent => ({ kind: "plugin", plugin: PLUGIN_ID, view: "coordinator", key: project });

const coordinators = new WeakMap<Record<string, Session>, Map<string, string | undefined>>();

/** The session a project's Coordinator tab shows: its newest running or starting Coordinator, else its newest one at all. */
export function coordinatorOf(project: string, sessions: Record<string, Session>): string | undefined {
  let byProject = coordinators.get(sessions);
  if (!byProject) coordinators.set(sessions, (byProject = new Map()));
  if (byProject.has(project)) return byProject.get(project);
  const live = (s: Session) => (s.status === "running" || s.status === "starting" ? 1 : 0);
  const found = Object.values(sessions)
    .filter((s) => s.managed && s.project === project && s.display_name === "Coordinator")
    .sort((a, b) => live(b) - live(a) || (b.created_at ?? 0) - (a.created_at ?? 0) || b.id.localeCompare(a.id))[0]?.id;
  byProject.set(project, found);
  return found;
}

/**
 * Show a project as it was left, its areas shown; asked for, with its Coordinator tab active. The owner is captured
 * before any await, so the tab is activated in this project's own tabs on this service, whatever is shown later.
 */
export async function openProject(service: string, name: string, coordinator = false): Promise<void> {
  const app = useApp.getState;
  if (app().activeId !== service) return;
  const owner: ViewOwner = { connection: service, feature: FEATURE, project: name };
  await app().setProject(FEATURE, name);
  if (coordinator) app().openTabIn(owner, coordinatorTab(name), "left");
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

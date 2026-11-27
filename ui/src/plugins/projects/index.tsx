import { useCallback, useEffect, useState, useSyncExternalStore, type FormEvent } from "react";
import { DetailsPanel } from "../../components/DetailsPanel";
import { selectedSession } from "../../components/MainArea";
import { Section } from "../../components/Section";
import { SessionSections } from "../../components/Sidebar";
import { focusedPastId, layoutIn, useApp, type ViewOwner } from "../../store";
import { areasOf } from "../../tabs";
import type { PluginContext, UiPlugin } from "../types";
import { isProjectCoordinator } from "./progress";
import { ProgressViewer } from "./ProgressViewer";

interface Project {
  name: string;
  owner: string;
  folder: string;
  created_at: number | null;
}

const FEATURE = "plugin:projects" as const;

/** The plugin's projects, refreshed by every component that asks. */
const listing = (() => {
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

function useProjects(plugin: PluginContext) {
  const service = useApp((s) => s.activeId);
  const projects = useSyncExternalStore(listing.subscribe, listing.snapshot);
  const chosen = useApp((s) => s.scopes[s.activeId]?.[FEATURE]);
  const setProject = useApp((s) => s.setProject);

  useEffect(() => {
    void listing.load(plugin).catch(() => undefined);
  }, [plugin, service]);

  // A project closed meanwhile is no longer shown, nor its tabs.
  const gone = !!projects && !!chosen && !projects.some((p) => p.name === chosen);
  useEffect(() => {
    if (gone) void setProject(FEATURE, null);
  }, [gone, setProject]);

  /** Show a project as it was left; one with no tabs yet, or when asked, gets its Coordinator's. */
  const open = useCallback((name: string, coordinator = false) => openProject(service, name, coordinator), [service]);

  const create = useCallback(
    async (name: string) => {
      await plugin.client.post<Project>("/projects", { name });
      await listing.load(plugin);
      await open(name);
    },
    [plugin, open],
  );

  const known = projects?.some((p) => p.name === chosen) ? chosen : undefined;
  return { projects, chosen: known, open, create };
}

function NewProject({ onCreate }: { onCreate(name: string): Promise<void> }) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const wanted = name.trim();
    if (!wanted) return;
    setBusy(true);
    setError("");
    // Cleared now, so a name typed while this one is made isn't lost.
    setName("");
    try {
      await onCreate(wanted);
    } catch (e) {
      setName(wanted);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="sidebar-actions" onSubmit={(event) => void submit(event)}>
      <input
        type="text"
        aria-label="New project's name"
        placeholder="my-project"
        pattern="[a-z0-9][a-z0-9_\-]*"
        title="Lowercase letters, digits, '-' and '_'"
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <button type="submit" className="primary" disabled={busy || !name.trim()}>
        Create
      </button>
      {error && <p className="form-error">{error}</p>}
    </form>
  );
}

function Left({ plugin }: { plugin: PluginContext }) {
  const { projects, chosen, open, create } = useProjects(plugin);

  // The Coordinator takes a closed project off the list; this notices.
  useEffect(() => {
    const timer = setInterval(() => void listing.load(plugin).catch(() => undefined), 5000);
    return () => clearInterval(timer);
  }, [plugin]);

  return (
    <>
      <Section id="projects:projects" title="Projects" extra={projects && <span className="session-count">{projects.length}</span>}>
        <NewProject onCreate={create} />
        <nav className="session-list" aria-label="Projects">
          {projects?.map((p) => (
            <div key={p.name} className="project-row" aria-current={p.name === chosen ? "true" : undefined}>
              <button type="button" className="project-pick" title={p.folder} onClick={() => void open(p.name)}>
                {p.name}
              </button>
            </div>
          ))}
          {projects && projects.length === 0 && <p className="session-empty">No projects yet.</p>}
        </nav>
      </Section>
      {chosen && <SessionSections project={chosen} canStart={false} />}
    </>
  );
}

function Background({ plugin }: { plugin: PluginContext }) {
  const { projects, chosen, open, create } = useProjects(plugin);
  return (
    <div className="placard">
      {chosen ? (
        <>
          <h2>{chosen}</h2>
          <p>Talk to its Coordinator to run it, or open any of its sessions from the left panel.</p>
          <div className="placard-actions">
            <button type="button" className="primary" onClick={() => void open(chosen, true)}>
              Open the Coordinator
            </button>
          </div>
        </>
      ) : (
        <>
          <h2>{projects?.length ? "Pick a project, or make a new one" : "Make your first project"}</h2>
          <p>A project is a folder its sessions share, and a Coordinator you talk to, which starts and routes work to the others.</p>
          <NewProject onCreate={create} />
        </>
      )}
    </div>
  );
}

function Right({ plugin }: { plugin: PluginContext }) {
  const { projects, chosen } = useProjects(plugin);
  const session = useApp(selectedSession);
  const pastId = useApp(focusedPastId);
  const openTab = useApp((s) => s.openTab);
  const entry = chosen && isProjectCoordinator(session, chosen, projects, pastId) && (
    <button
      type="button"
      onClick={() => openTab({ kind: "plugin", plugin: plugin.id, view: "progress", key: chosen }, "right", "tab")}
    >
      Progress Viewer
    </button>
  );
  return <DetailsPanel actions={entry} />;
}

export const plugin: UiPlugin = {
  feature: {
    label: "Projects",
    views: {
      progress: {
        label: "Progress Viewer",
        tabLabel: (project) => `Progress Viewer: ${project}`,
        Component: ({ plugin, viewKey }) => viewKey ? <ProgressViewer plugin={plugin} project={viewKey} /> : null,
      },
    },
    projects: true,
    left: Left,
    right: Right,
    background: Background,
  },
};

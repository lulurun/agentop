import { useCallback, useEffect, useState, useSyncExternalStore, type FormEvent } from "react";
import type { Session } from "../../api/client";
import { DetailsPanel } from "../../components/DetailsPanel";
import { Section } from "../../components/Section";
import { PlusIcon, TrashIcon } from "../../components/icons";
import { CreateSection, SessionSections } from "../../components/Sidebar";
import { useApp } from "../../store";
import type { PluginContext, UiPlugin } from "../types";

interface Project {
  name: string;
  owner: string;
  folder: string;
  created_at: number | null;
}

const CURRENT_KEY = "agentop.projects.current";

/** The project chosen, per service, shared by the left panel and the background. */
const current = (() => {
  const listeners = new Set<() => void>();
  let saved: Record<string, string> = {};
  try {
    saved = JSON.parse(localStorage.getItem(CURRENT_KEY) ?? "{}");
  } catch {
    saved = {};
  }
  return {
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    snapshot: () => saved,
    set(service: string, name: string | null) {
      saved = { ...saved };
      if (name) saved[service] = name;
      else delete saved[service];
      try {
        localStorage.setItem(CURRENT_KEY, JSON.stringify(saved));
      } catch {
        // Kept for this page only.
      }
      listeners.forEach((l) => l());
    },
  };
})();

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

function useProjects(plugin: PluginContext) {
  const service = useApp((s) => s.activeId);
  const projects = useSyncExternalStore(listing.subscribe, listing.snapshot);
  const chosen = useSyncExternalStore(current.subscribe, current.snapshot)[service];
  const select = useApp((s) => s.select);
  const setError = useApp((s) => s.setError);

  useEffect(() => {
    void listing.load(plugin).catch(() => undefined);
  }, [plugin, service]);

  /** Show a project, opening its Coordinator: resumed, or started, if it isn't running. */
  const open = useCallback(
    async (name: string) => {
      current.set(service, name);
      try {
        const row = await plugin.client.post<Session>(`/projects/${encodeURIComponent(name)}/coordinator`);
        useApp.setState((s) => ({ sessions: { ...s.sessions, [row.id]: row } }));
        select(row.id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [plugin, service, select, setError],
  );

  const create = useCallback(
    async (name: string) => {
      await plugin.client.post<Project>("/projects", { name });
      await listing.load(plugin);
      await open(name);
    },
    [plugin, open],
  );

  const remove = useCallback(
    async (name: string, deleteFolder: boolean) => {
      await plugin.client.delete(`/projects/${encodeURIComponent(name)}?delete_folder=${deleteFolder}`);
      if (current.snapshot()[service] === name) current.set(service, null);
      await listing.load(plugin);
    },
    [plugin, service],
  );

  const known = projects?.some((p) => p.name === chosen) ? chosen : undefined;
  return { projects, chosen: known, open, create, remove };
}

function NewProject({ onCreate, onCancel }: { onCreate(name: string): Promise<void>; onCancel?(): void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError("");
    try {
      await onCreate(name.trim());
      setName("");
      onCancel?.();
    } catch (e) {
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
        pattern="[a-z0-9][a-z0-9_-]*"
        title="Lowercase letters, digits, '-' and '_'"
        autoFocus={!!onCancel}
        value={name}
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => event.key === "Escape" && onCancel?.()}
      />
      <button type="submit" className="primary" disabled={busy || !name.trim()}>
        Create
      </button>
      {error && <p className="form-error">{error}</p>}
    </form>
  );
}

function Left({ plugin }: { plugin: PluginContext }) {
  const { projects, chosen, open, create, remove } = useProjects(plugin);
  const setDialog = useApp((s) => s.setDialog);
  const [naming, setNaming] = useState(false);

  function removeChosen() {
    if (!chosen) return;
    if (!window.confirm(`Stop ${chosen}'s sessions and remove the project? Its folder is kept.`)) return;
    void remove(chosen, false).catch((e) => useApp.getState().setError(e instanceof Error ? e.message : String(e)));
  }

  return (
    <>
      <CreateSection id="projects:create" label="New project" onClick={() => setNaming((n) => !n)}>
        {naming && <NewProject onCreate={create} onCancel={() => setNaming(false)} />}
      </CreateSection>
      <Section id="projects:projects" title="Projects" extra={projects && <span className="session-count">{projects.length}</span>}>
        {projects && projects.length > 0 && (
          <div className="sidebar-actions">
            <select
              className="sidebar-select"
              aria-label="Project"
              value={chosen ?? ""}
              onChange={(event) => event.target.value && void open(event.target.value)}
            >
              {!chosen && <option value="">Pick a project</option>}
              {projects.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
        )}
        {chosen && (
          <div className="project-current">
            <span className="project-current-name" title={chosen}>
              {chosen}
            </span>
            <button type="button" className="icon" title={`New session in ${chosen}`} aria-label={`New session in ${chosen}`} onClick={() => setDialog("new-session", chosen)}>
              <PlusIcon />
            </button>
            <button type="button" className="icon" title={`Remove ${chosen}`} aria-label={`Remove ${chosen}`} onClick={removeChosen}>
              <TrashIcon />
            </button>
          </div>
        )}
        {projects && projects.length === 0 && <p className="session-empty">No projects yet.</p>}
      </Section>
      {chosen && <SessionSections project={chosen} />}
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
            <button type="button" className="primary" onClick={() => void open(chosen)}>
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

export const plugin: UiPlugin = {
  feature: {
    label: "Projects",
    views: {},
    left: Left,
    right: () => <DetailsPanel />,
    background: Background,
  },
};

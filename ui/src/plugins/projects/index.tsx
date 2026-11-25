import { useCallback, useEffect, useState, useSyncExternalStore, type FormEvent } from "react";
import { DetailsPanel } from "../../components/DetailsPanel";
import { Section } from "../../components/Section";
import { SessionSections } from "../../components/Sidebar";
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
  const act = useApp((s) => s.act);

  useEffect(() => {
    void listing.load(plugin).catch(() => undefined);
  }, [plugin, service]);

  const open = useCallback(
    async (name: string) => {
      current.set(service, name);
      const rows = await act((client) => client.sessions());
      if (useApp.getState().activeId !== service) return;
      const row = rows?.find(
        (s) => s.project === name && s.display_name === "Coordinator" && (s.status === "running" || s.status === "starting"),
      );
      if (row) {
        useApp.setState((s) => ({ sessions: { ...s.sessions, [row.id]: row } }));
        select(row.id);
      }
    },
    [service, select, act],
  );

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

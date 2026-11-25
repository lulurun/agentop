import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import type { Session } from "../../api/client";
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

  /** Ask its Coordinator to close it, and show the Coordinator: the project leaves the list once it has. */
  const close = useCallback(
    async (name: string) => {
      await plugin.client.post(`/projects/${encodeURIComponent(name)}/close`);
      await open(name);
    },
    [plugin, open],
  );

  const known = projects?.some((p) => p.name === chosen) ? chosen : undefined;
  return { projects, chosen: known, open, create, close };
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

/** A modal in the page, not window.confirm: where a webview shows no dialog, confirm answers no, and the old button did nothing. */
function CloseProject({ name, onClose, onCancel }: { name: string; onClose(): Promise<void>; onCancel(): void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  async function close() {
    setBusy(true);
    setError("");
    try {
      await onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <dialog ref={ref} className="dialog" onClose={onCancel} aria-labelledby="close-project-title">
      <h2 id="close-project-title">Close {name}?</h2>
      <p>
        Its Coordinator is asked to close it. Once no task is running and the track is up to date, it marks the project
        closed, takes it off this list, and ends its own session. The folder, its files and the other sessions are kept;
        creating {name} again loads it.
      </p>
      {error && <p className="form-error">{error}</p>}
      <div className="stop-buttons">
        <button type="button" onClick={onCancel} disabled={busy} data-autofocus>
          Cancel
        </button>
        <button type="button" className="primary" onClick={() => void close()} disabled={busy}>
          Close {name}
        </button>
      </div>
    </dialog>
  );
}

function Left({ plugin }: { plugin: PluginContext }) {
  const { projects, chosen, open, create, close } = useProjects(plugin);
  const [closing, setClosing] = useState<string | null>(null);

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
              <button
                type="button"
                className="project-close-button"
                title={`Close ${p.name}: its Coordinator closes it; everything is kept`}
                aria-label={`Close ${p.name}`}
                onClick={() => setClosing(p.name)}
              >
                Close
              </button>
            </div>
          ))}
          {projects && projects.length === 0 && <p className="session-empty">No projects yet.</p>}
        </nav>
      </Section>
      {closing && (
        <CloseProject
          key={closing}
          name={closing}
          onCancel={() => setClosing(null)}
          onClose={async () => {
            await close(closing);
            setClosing(null);
          }}
        />
      )}
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

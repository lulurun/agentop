import { useCallback, useEffect, useSyncExternalStore } from "react";
import { DetailsPanel } from "../../components/DetailsPanel";
import { selectedSession } from "../../components/MainArea";
import { Section } from "../../components/Section";
import { SessionSections } from "../../components/Sidebar";
import { focusedPastId, useApp } from "../../store";
import type { PluginContext, UiPlugin } from "../types";
import { NewProject } from "./NewProject";
import { isProjectCoordinator } from "./progress";
import { ProgressViewer } from "./ProgressViewer";
import { coordinatorOf, FEATURE, listing, openCreator, openProject } from "./state";

export { openProject } from "./state";

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

  /** Show a project as it was left, its areas shown; asked for, with its Coordinator tab active. */
  const open = useCallback((name: string, coordinator = false) => openProject(service, name, coordinator), [service]);

  const known = projects?.some((p) => p.name === chosen) ? chosen : undefined;
  return { projects, chosen: known, open };
}

function CreateButton() {
  return (
    <div className="sidebar-actions">
      <button type="button" className="primary sidebar-wide" title="Make a new project" onClick={() => void openCreator()}>
        New project
      </button>
    </div>
  );
}

function Left({ plugin }: { plugin: PluginContext }) {
  const { projects, chosen, open } = useProjects(plugin);

  // The Coordinator takes a closed project off the list; this notices.
  useEffect(() => {
    const timer = setInterval(() => void listing.load(plugin).catch(() => undefined), 5000);
    return () => clearInterval(timer);
  }, [plugin]);

  return (
    <>
      <Section id="projects:projects" title="Projects" extra={projects && <span className="session-count">{projects.length}</span>}>
        <CreateButton />
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

/** With a project chosen its fixed tabs are always there, so this shows only with none. */
function Background({ plugin }: { plugin: PluginContext }) {
  const { projects } = useProjects(plugin);
  return (
    <div className="placard">
      <h2>{projects?.length ? "Pick a project, or make a new one" : "Make your first project"}</h2>
      <p>A project is a folder its sessions share, and a Coordinator you talk to, which starts and routes work to the others.</p>
      <div className="placard-actions">
        <button type="button" className="primary" onClick={() => void openCreator()}>
          New project
        </button>
      </div>
    </div>
  );
}

/** The Coordinator tab while its project has no Coordinator session: the tab shows its terminal once there is one. */
function NoCoordinator({ viewKey }: { plugin: PluginContext; viewKey?: string }) {
  const loaded = useApp((s) => s.sessionsLoaded);
  const linked = useApp((s) => s.link.state === "connected");
  if (!loaded) {
    return (
      <div className="placard" role="status">
        <h2>Connecting to the Coordinator of {viewKey}</h2>
      </div>
    );
  }
  return (
    <div className="placard" role="status">
      <h2>No Coordinator is running in {viewKey}</h2>
      <p>
        {linked ? "Browsing never starts one. " : "The service is reconnecting. "}
        Its terminal shows here as soon as one runs: when the project is created or reopened by name.
      </p>
    </div>
  );
}

function Right({ plugin }: { plugin: PluginContext }) {
  const { projects, chosen } = useProjects(plugin);
  const session = useApp(selectedSession);
  const pastId = useApp(focusedPastId);
  const openTab = useApp((s) => s.openTab);
  const entry = chosen && isProjectCoordinator(session, chosen, projects, pastId) && (
    <button type="button" onClick={() => openTab({ kind: "plugin", plugin: plugin.id, view: "progress", key: chosen }, "right", "tab")}>
      Progress Viewer
    </button>
  );
  return <DetailsPanel actions={entry} />;
}

export const plugin: UiPlugin = {
  feature: {
    label: "Projects",
    views: {
      new: { label: "New project", Component: NewProject },
      coordinator: {
        label: "Coordinator",
        tabLabel: (project) => `Coordinator: ${project}`,
        Component: NoCoordinator,
        session: coordinatorOf,
      },
      progress: {
        label: "Progress Viewer",
        tabLabel: (project) => `Progress: ${project}`,
        Component: ({ plugin, viewKey }) => viewKey ? <ProgressViewer plugin={plugin} project={viewKey} /> : null,
      },
    },
    projects: true,
    fixed: { left: "coordinator", right: "progress" },
    left: Left,
    right: Right,
    background: Background,
  },
};

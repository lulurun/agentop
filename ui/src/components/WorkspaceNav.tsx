import { useEffect } from "react";
import type { PluginInfo } from "../api/client";
import { UI_PLUGINS } from "../plugins/registry";
import { PluginBoundary, usePluginContext } from "../plugins/Slot";
import { currentWorkspace, useApp } from "../store";
import { pluginOf, workspacesFor, type WorkspaceInfo } from "../workspaces";

/** Ctrl+Shift+N sends this in a plugin's workspace that has an action, for the action to run. */
export const ACTION_EVENT = "agentop:workspace-action";

/** Whether the workspace has an action of a plugin's own, which Ctrl+Shift+N runs instead of New session. */
export function hasPluginAction(id: WorkspaceInfo["id"]): boolean {
  const plugin = pluginOf(id);
  return !!plugin && !!UI_PLUGINS[plugin]?.workspace?.action;
}

export function useWorkspaces(): { list: WorkspaceInfo[]; current: WorkspaceInfo } {
  const plugins = useApp((s) => s.plugins);
  const id = useApp(currentWorkspace);
  const list = workspacesFor(plugins);
  return { list, current: list.find((w) => w.id === id) ?? list[0] };
}

/** The selected service's workspaces. The current one is highlighted and can't be clicked. */
export function WorkspaceNav() {
  const { list, current } = useWorkspaces();
  const setWorkspace = useApp((s) => s.setWorkspace);
  return (
    <>
      <nav className="workspaces" aria-label="Workspaces">
        {list.map((w, index) => {
          const here = w.id === current.id;
          return (
            <button
              key={w.id}
              type="button"
              aria-current={here ? "page" : undefined}
              aria-disabled={here || undefined}
              title={index < 9 ? `${w.label} (Ctrl+Shift+${index + 1})` : w.label}
              onClick={() => !here && setWorkspace(w.id)}
            >
              {w.label}
            </button>
          );
        })}
      </nav>
      <select className="workspaces-compact" aria-label="Workspace" value={current.id} onChange={(event) => setWorkspace(event.target.value as WorkspaceInfo["id"])}>
        {list.map((w) => (
          <option key={w.id} value={w.id}>
            {w.label}
          </option>
        ))}
      </select>
    </>
  );
}

/** The workspace's button in the header: New session in Sessions, a plugin's own in its workspace. */
export function WorkspaceAction() {
  const { current } = useWorkspaces();
  const setDialog = useApp((s) => s.setDialog);
  const plugin = pluginOf(current.id);
  const info = useApp((s) => s.plugins.find((p) => p.id === plugin));

  if (current.id === "sessions") {
    return (
      <button type="button" className="header-new" onClick={() => setDialog("new-session")} title="New session (Ctrl+Shift+N)">
        New session
      </button>
    );
  }
  if (!info || !UI_PLUGINS[info.id]?.workspace?.action) return null;
  return (
    <PluginBoundary key={info.id} id={info.id}>
      <PluginAction info={info} />
    </PluginBoundary>
  );
}

function PluginAction({ info }: { info: PluginInfo }) {
  const plugin = usePluginContext(info);
  const action = UI_PLUGINS[info.id]!.workspace!.action!;
  useEffect(() => {
    const run = () => action.run(plugin);
    window.addEventListener(ACTION_EVENT, run);
    return () => window.removeEventListener(ACTION_EVENT, run);
  }, [action, plugin]);
  return (
    <button type="button" className="header-new" onClick={() => action.run(plugin)} title={`${action.label} (Ctrl+Shift+N)`}>
      {action.label}
    </button>
  );
}

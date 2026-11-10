import { Component, Fragment, useMemo, type ReactNode } from "react";
import type { PluginInfo, Session } from "../api/client";
import { activeClient, onPluginEvent, useApp } from "../store";
import type { FileRef } from "../tabs";
import { pluginsFor, UI_PLUGINS } from "./registry";
import type { PluginContext, Slot, UiPlugin } from "./types";

/** A plugin component that throws hides itself. The rest of the page keeps rendering. */
export class PluginBoundary extends Component<{ id: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(`Plugin ${this.props.id} failed to render`, error);
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** The context a plugin's components get, for the active connection. */
export function usePluginContext(info: PluginInfo): PluginContext {
  const connections = useApp((s) => s.connections);
  const activeId = useApp((s) => s.activeId);
  return useMemo(() => {
    const client = activeClient({ connections, activeId }).plugin(info.id);
    return { id: info.id, info, client, onEvent: (listener) => onPluginEvent(info.id, listener) };
  }, [connections, activeId, info]);
}

function Piece({ info, ui, slot, session }: { info: PluginInfo; ui: UiPlugin; slot: Slot; session?: Session }) {
  const plugin = usePluginContext(info);
  switch (slot) {
    case "session-badge":
      return ui.sessionBadge && session ? <ui.sessionBadge session={session} plugin={plugin} /> : null;
    case "right-panel":
      return ui.rightPanel && session ? <ui.rightPanel session={session} plugin={plugin} /> : null;
    case "settings-section":
      return ui.settingsSection ? <ui.settingsSection plugin={plugin} /> : null;
    case "left-view":
      return ui.leftView ? <ui.leftView.Component plugin={plugin} /> : null;
    case "file-viewer":
      return null; // drawn by the tab that shows the file (FileTab)
  }
}

/** The enabled plugin that opens files, if any. Files in the right panel are clickable only while there is one. */
export function useFileViewer(): PluginInfo | undefined {
  const infos = useApp((s) => s.plugins);
  return pluginsFor("file-viewer", infos)[0]?.info;
}

/** A file, drawn by the file-viewer plugin, or a note when none is enabled. */
export function FileTab({ file }: { file: FileRef }) {
  const info = useFileViewer();
  if (!info) {
    return (
      <div className="placard">
        <h2>No file viewer</h2>
        <p>Enable the File viewer plugin in Settings to see this file here.</p>
      </div>
    );
  }
  return (
    <PluginBoundary key={info.id} id={info.id}>
      <FileViewerPiece info={info} file={file} />
    </PluginBoundary>
  );
}

function FileViewerPiece({ info, file }: { info: PluginInfo; file: FileRef }) {
  const plugin = usePluginContext(info);
  const Viewer = UI_PLUGINS[info.id]?.fileViewer;
  return Viewer ? <Viewer file={file} plugin={plugin} /> : null;
}

/** Every enabled plugin's piece for a slot, each behind its own error boundary. */
export function PluginSlot({ slot, session, only }: { slot: Slot; session?: Session; only?: string }) {
  const infos = useApp((s) => s.plugins);
  const pieces = pluginsFor(slot, infos).filter(({ info }) => !only || info.id === only);
  return (
    <>
      {pieces.map(({ info, ui }) => (
        <Fragment key={info.id}>
          <PluginBoundary id={info.id}>
            <Piece info={info} ui={ui} slot={slot} session={session} />
          </PluginBoundary>
        </Fragment>
      ))}
    </>
  );
}

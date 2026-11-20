import { Component, Fragment, useEffect, useMemo, type MutableRefObject, type ReactNode } from "react";
import type { PluginInfo, Session } from "../api/client";
import { onPluginEvent, useActiveClient, useApp } from "../store";
import type { FileRef } from "../tabs";
import { Section } from "../components/Section";
import { pluginsFor, UI_PLUGINS } from "./registry";
import type { PluginContext, Slot, TerminalInputControl, UiPlugin } from "./types";

/** A plugin component that throws hides itself. The rest of the page keeps rendering. */
export class PluginBoundary extends Component<{ id: string; children: ReactNode; onFail?: () => void }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(`Plugin ${this.props.id} failed to render`, error);
    this.props.onFail?.();
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** The context a plugin's components get, for the active connection. */
export function usePluginContext(info: PluginInfo): PluginContext {
  const service = useActiveClient();
  return useMemo(() => {
    const client = service.plugin(info.id);
    return { id: info.id, info, client, onEvent: (listener) => onPluginEvent(info.id, listener) };
  }, [service, info]);
}

function Piece({ info, ui, slot, session }: { info: PluginInfo; ui: UiPlugin; slot: Slot; session?: Session }) {
  const plugin = usePluginContext(info);
  switch (slot) {
    case "session-badge":
      return ui.sessionBadge && session ? <ui.sessionBadge session={session} plugin={plugin} /> : null;
    case "right-panel":
      return ui.rightPanel && session ? (
        <Section id={`plugin:${info.id}`} title={info.name}>
          <ui.rightPanel session={session} plugin={plugin} />
        </Section>
      ) : null;
    case "settings-section":
      return ui.settingsSection ? <ui.settingsSection plugin={plugin} /> : null;
    case "feature":
    case "file-viewer":
    case "login":
    case "terminal-input":
      return null; // drawn by the feature and the tabs it shows (PluginTab, FileTab), the sign-in screen (LoginMethods), or below a terminal (TerminalInputs)
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

/** One of a plugin's views in a tab, or a note when the plugin isn't on. */
export function PluginTab({ pluginId, view, viewKey }: { pluginId: string; view: string; viewKey?: string }) {
  const info = useApp((s) => s.plugins.find((p) => p.id === pluginId && p.state === "enabled"));
  const View = UI_PLUGINS[pluginId]?.feature?.views[view];
  if (!info || !View) {
    return (
      <div className="placard">
        <h2>Not available</h2>
        <p>This tab belongs to a plugin that isn't on for this service.</p>
      </div>
    );
  }
  return (
    <PluginBoundary key={info.id} id={info.id}>
      <PluginViewPiece info={info} View={View.Component} viewKey={viewKey} />
    </PluginBoundary>
  );
}

function PluginViewPiece({ info, View, viewKey }: { info: PluginInfo; View: NonNullable<UiPlugin["feature"]>["views"][string]["Component"]; viewKey?: string }) {
  const plugin = usePluginContext(info);
  return <View plugin={plugin} viewKey={viewKey} />;
}

/** A plugin feature's left or right panel, or its background. */
export function PluginPanel({ pluginId, side }: { pluginId: string; side: "left" | "right" | "background" }) {
  const info = useApp((s) => s.plugins.find((p) => p.id === pluginId && p.state === "enabled"));
  if (!info) return null;
  return (
    <PluginBoundary key={info.id} id={info.id}>
      <PluginPanelPiece info={info} side={side} />
    </PluginBoundary>
  );
}

function PluginPanelPiece({ info, side }: { info: PluginInfo; side: "left" | "right" | "background" }) {
  const plugin = usePluginContext(info);
  const Panel = UI_PLUGINS[info.id]?.feature?.[side];
  return Panel ? <Panel plugin={plugin} /> : null;
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

/**
 * Every enabled `terminal-input` piece, below a session's terminal. `onTakeInput`
 * hears which plugins are taking the terminal's input; a piece that crashes or
 * goes away gives it back.
 */
export function TerminalInputs({
  session,
  onTakeInput,
  keyboard,
}: {
  session: Session;
  onTakeInput: (id: string, on: boolean, focus?: () => void) => void;
  /** The terminal's keyboard (`TerminalView`'s `keyboard`). */
  keyboard: MutableRefObject<((data: string) => boolean) | null>;
}) {
  const infos = useApp((s) => s.plugins);
  const pieces = pluginsFor("terminal-input", infos);
  return (
    <>
      {pieces.map(({ info }) => (
        <PluginBoundary key={info.id} id={info.id} onFail={() => onTakeInput(info.id, false)}>
          <TerminalInputPiece info={info} session={session} onTakeInput={onTakeInput} keyboard={keyboard} />
        </PluginBoundary>
      ))}
    </>
  );
}

function TerminalInputPiece({
  info,
  session,
  onTakeInput,
  keyboard,
}: {
  info: PluginInfo;
  session: Session;
  onTakeInput: (id: string, on: boolean, focus?: () => void) => void;
  keyboard: MutableRefObject<((data: string) => boolean) | null>;
}) {
  const plugin = usePluginContext(info);
  const terminal = useMemo<TerminalInputControl>(
    () => ({ takeInput: (on, focus) => onTakeInput(info.id, on, focus), type: (data) => keyboard.current?.(data) ?? false }),
    [info.id, onTakeInput, keyboard],
  );
  useEffect(() => () => onTakeInput(info.id, false), [info.id, onTakeInput]);
  const Input = UI_PLUGINS[info.id]?.terminalInput;
  return Input ? <Input session={session} plugin={plugin} terminal={terminal} /> : null;
}

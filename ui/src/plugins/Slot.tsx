import { Component, Fragment, useMemo, type ReactNode } from "react";
import type { PluginInfo, Session } from "../api/client";
import { activeClient, onPluginEvent, useApp } from "../store";
import { pluginsFor } from "./registry";
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
  }
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

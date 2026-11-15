import { useEffect, useState, useSyncExternalStore } from "react";
import { useApp } from "../../store";
import type { PluginClient, PluginContext, UiPlugin } from "../types";

/** {session ID: when it started waiting, epoch seconds}. */
type Waiting = Record<string, number>;

interface State {
  waiting: Waiting;
  quiet_seconds: number;
  stats: { at: number | null; sessions: number; captured: number; ms: number; skipped: number };
}

/**
 * One copy of the waiting set per connection, shared by every badge: read once,
 * then kept up to date by the plugin's events.
 */
class WaitingSet {
  waiting: Waiting = {};
  private listeners = new Set<() => void>();
  private stop: (() => void) | null = null;

  constructor(private plugin: PluginContext) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    if (!this.stop) {
      this.stop = this.plugin.onEvent((data) => {
        const waiting = (data as { waiting?: Waiting } | null)?.waiting;
        if (waiting) this.set(waiting);
      });
      void this.plugin.client.get<State>("/state").then((state) => this.set(state.waiting), () => undefined);
    }
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size) {
        this.stop?.();
        this.stop = null;
      }
    };
  };

  snapshot = () => this.waiting;

  private set(waiting: Waiting) {
    this.waiting = waiting;
    for (const listener of this.listeners) listener();
  }
}

const sets = new WeakMap<PluginClient, WaitingSet>();

function waitingSet(plugin: PluginContext): WaitingSet {
  let set = sets.get(plugin.client);
  if (!set) {
    set = new WaitingSet(plugin);
    sets.set(plugin.client, set);
  }
  return set;
}

/** Shown on a session that stopped working, unless it is the one open now. */
function WaitingBadge({ session, plugin }: { session: { id: string }; plugin: PluginContext }) {
  const set = waitingSet(plugin);
  const waiting = useSyncExternalStore(set.subscribe, set.snapshot);
  const open = useApp((s) => s.selected[s.activeId] === session.id);
  const since = waiting[session.id];
  if (since === undefined || open) return null;
  return (
    <span className="session-tag attention-tag" title={`Stopped at ${new Date(since * 1000).toLocaleTimeString()}`}>
      Waiting
    </span>
  );
}

/** What a tick costs, so it is clear whether to keep the plugin on. */
function Cost({ plugin }: { plugin: PluginContext }) {
  const [state, setState] = useState<State | null>(null);
  useEffect(() => {
    let live = true;
    const load = () =>
      void plugin.client.get<State>("/state").then(
        (value) => live && setState(value),
        () => undefined,
      );
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [plugin.client]);
  if (!state) return null;
  const { stats } = state;
  return (
    <p className="memory-note">
      A session is marked once its screen has stopped changing for {state.quiet_seconds}s after it was working.{" "}
      {stats.at === null
        ? "Not checked yet."
        : `Last check: ${stats.sessions} sessions, ${stats.captured} screens read, ${stats.ms} ms.`}
      {stats.skipped > 0 && ` ${stats.skipped} checks skipped because the one before was still running.`}
    </p>
  );
}

export const plugin: UiPlugin = {
  sessionBadge: WaitingBadge,
  settingsSection: Cost,
};

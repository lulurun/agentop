import { useCallback, useEffect, useState } from "react";
import type { ServiceInfo, UpdateInfo } from "../api/client";
import { ApiError } from "../api/client";
import { connectionName, isEncrypted, isLoopback } from "../connections";
import { activeConnection, useActiveClient, useApp } from "../store";
import { ClientsSection } from "./ClientsSection";
import { formatDuration } from "./format";
import { PluginsSection } from "./PluginsSection";

type Action = "restart" | "stop" | "update";

const POLL_MS = 2_000;

const STEPS: Record<string, string> = {
  checking: "Checking the install",
  pulling: "Getting the new version",
  installing: "Installing",
  building: "Building the app",
  restarting: "Restarting the service",
  verifying: "Checking that it started",
  rolling_back: "Going back to the previous version",
};

function bringing(update: UpdateInfo | null | undefined): string {
  if (!update?.behind) return "";
  if (update.update_from === "release") return `This installs ${update.upstream} and restarts the service. `;
  return `This installs ${update.behind} new commit${update.behind === 1 ? "" : "s"} and restarts the service. `;
}

function confirmation(action: Action, name: string, update: UpdateInfo | null | undefined, manager: string | undefined) {
  switch (action) {
    case "restart":
      return {
        title: `Restart ${name}?`,
        detail: "Sessions keep running. Terminals drop for a moment and reconnect by themselves.",
        button: "Restart service",
      };
    case "stop":
      return {
        title: `Stop ${name}?`,
        detail: `Sessions keep running, but this app can't start the service again. To bring it back, ${
          manager === "systemd" ? "start its systemd unit" : "run agentop serve"
        } on that machine.`,
        button: "Stop service",
      };
    case "update":
      return {
        title: `Update ${name}?`,
        detail: `${bringing(update)}Sessions keep running. If the new version fails, the service goes back to this one.`,
        button: "Update service",
      };
  }
}

function UpdateResult({ info }: { info: UpdateInfo }) {
  const run = info.last_run;
  if (info.running && run?.state === "running") {
    return (
      <p className="service-progress" role="status">
        {STEPS[run.step ?? ""] ?? "Updating"}…
      </p>
    );
  }
  if (info.running) return <p className="service-progress" role="status">Updating…</p>;
  if (run?.state === "done") {
    return <p className="service-note" role="status">{run.message ?? `Updated to ${run.to_commit}.`}</p>;
  }
  if (run && run.state !== "running") return <p className="form-error" role="alert">{run.message}</p>;
  return null;
}

function updateSummary(info: UpdateInfo | null): string {
  if (!info) return "Checking…";
  if (info.check_error) return `Couldn't check: ${info.check_error}`;
  if (info.behind === 0) return info.update_from === "release" && info.upstream ? `Up to date (${info.upstream} or newer)` : "Up to date";
  if (info.update_from === "release") return `${info.upstream} is out`;
  return info.behind === 1 ? "1 new commit" : `${info.behind} new commits`;
}

/**
 * The selected service's Status, in a tab (design 10.7.4): what it is, Stop,
 * Restart and Update, the clients signed in to it, and its plugins.
 */
export function StatusView({ onClose }: { onClose(): void }) {
  const connection = useApp(activeConnection);
  const markStopped = useApp((s) => s.markStopped);
  const updateConnection = useApp((s) => s.updateConnection);
  const removeConnection = useApp((s) => s.removeConnection);
  const client = useActiveClient();

  const [info, setInfo] = useState<ServiceInfo | null>(null);
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [pending, setPending] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The start time before a restart, until a different one shows the restart is done.
  const [restarting, setRestarting] = useState<{ startedAt: number | null; uptime: number } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const fresh = await client.service();
      setInfo(fresh);
      setUnreachable(false);
      return fresh;
    } catch {
      setUnreachable(true);
      return null;
    }
  }, [client]);

  const refreshUpdate = useCallback(() => client.updateInfo().then(setUpdate).catch(() => undefined), [client]);

  useEffect(() => {
    void refresh();
    void refreshUpdate();
  }, [refresh, refreshUpdate]);

  // While something is happening the service may be away, so failures here are expected.
  const updating = update?.running === true;
  const busyRestarting = restarting !== null;
  useEffect(() => {
    if (!updating && !restarting) return;
    const timer = setInterval(() => {
      void refresh().then((fresh) => {
        if (!fresh || !restarting) return;
        const restarted =
          restarting.startedAt != null && fresh.started_at != null
            ? fresh.started_at !== restarting.startedAt
            : fresh.uptime_seconds < restarting.uptime; // a service too old to report started_at
        if (restarted) setRestarting(null);
      });
      void refreshUpdate();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [updating, restarting, refresh, refreshUpdate]);

  async function perform(action: Action) {
    setBusy(true);
    setError(null);
    try {
      if (action === "stop") {
        await client.stopService();
        onClose();
        markStopped();
        return;
      }
      if (action === "restart") {
        await client.restartService();
        setRestarting({ startedAt: info?.started_at ?? null, uptime: info?.uptime_seconds ?? 0 });
      } else {
        await client.startUpdate();
        await refreshUpdate();
      }
      setPending(null);
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "The service didn't answer.");
    } finally {
      setBusy(false);
    }
  }

  const name = connectionName(connection, info?.name);
  const can = info?.capabilities ?? {};
  const asking = pending && confirmation(pending, name, update, info?.manager);

  return (
    <section className="view status-view" aria-label="Status">
      <h2>{name}</h2>
      {asking && (
        <div className="status-confirm" role="alertdialog" aria-label={asking.title}>
          <h3 className="status-confirm-title">{asking.title}</h3>
          <p className="dialog-text">{asking.detail}</p>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="dialog-actions">
            <button type="button" onClick={() => setPending(null)}>
              Cancel
            </button>
            <button type="button" className="primary" disabled={busy} onClick={() => void perform(pending)}>
              {asking.button}
            </button>
          </div>
        </div>
      )}
      {!asking && error && <p className="form-error" role="alert">{error}</p>}
      {unreachable && !updating && !busyRestarting && <p className="form-error" role="alert">This service isn't answering.</p>}
      {restarting && <p className="service-progress" role="status">Restarting…</p>}
      <div className="status-columns">
        <section className="status-section" aria-label="Service">
          <h3>Service</h3>
          <label className="field">
            Name
            <input
              value={connection.alias ?? ""}
              placeholder={info?.name ?? connection.label}
              spellCheck={false}
              onChange={(event) => updateConnection(connection.id, { alias: event.target.value })}
            />
          </label>
          <p className="field-hint">Only this app uses it. Leave it empty to use the service's own name.</p>
          <dl className="service-facts">
            {info && info.name !== name && <div className="detail-row"><dt>Service name</dt><dd>{info.name}</dd></div>}
            <div className="detail-row"><dt>Address</dt><dd>{connection.url}</dd></div>
            <div className="detail-row"><dt>Connection</dt><dd>{isEncrypted(connection.url) ? "Encrypted" : isLoopback(connection.url) ? "This computer" : "Not encrypted"}</dd></div>
            {info && (
              <>
                <div className="detail-row"><dt>Version</dt><dd>{info.commit ? `${info.version} (${info.commit})` : info.version}</dd></div>
                {info.checkout_commit && info.commit && info.checkout_commit !== info.commit && (
                  <div className="detail-row"><dt>Checked out</dt><dd>{info.checkout_commit}, not running yet: Restart runs it</dd></div>
                )}
                <div className="detail-row"><dt>Running for</dt><dd>{formatDuration(info.uptime_seconds)}</dd></div>
                <div className="detail-row"><dt>tmux</dt><dd>{info.tmux_version ?? "Not found"}</dd></div>
                {can.update && <div className="detail-row"><dt>Updates</dt><dd>{updateSummary(update)}</dd></div>}
              </>
            )}
          </dl>
          {update && <UpdateResult info={update} />}
          {info && info.manager === "none" && (
            <p className="service-note">
              This service can't be stopped, restarted, or updated from here. It's an older version, or wasn't started with
              agentop serve.
            </p>
          )}
          <div className="status-actions">
            {!connection.isHome && (
              <button
                type="button"
                title="Remove it from this app. Its sessions keep running."
                onClick={() => {
                  onClose();
                  removeConnection(connection.id);
                }}
              >
                Forget
              </button>
            )}
            <button type="button" disabled={!can.stop || updating || busyRestarting} onClick={() => setPending("stop")}>
              Stop
            </button>
            <button type="button" disabled={!can.restart || updating || busyRestarting} onClick={() => setPending("restart")}>
              Restart
            </button>
            <button
              type="button"
              className="primary"
              disabled={!can.update || updating || busyRestarting || !update?.behind}
              onClick={() => setPending("update")}
            >
              Update
            </button>
          </div>
          {info && <ClientsSection client={client} connection={connection} />}
        </section>
        <PluginsSection />
      </div>
    </section>
  );
}

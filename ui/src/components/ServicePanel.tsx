import { useCallback, useEffect, useMemo, useState } from "react";
import type { ServiceInfo, UpdateInfo } from "../api/client";
import { ApiError, ServiceClient } from "../api/client";
import { connectionName, isEncrypted, isLoopback } from "../connections";
import { activeConnection, useApp } from "../store";
import { Dialog } from "./Dialog";
import { formatDuration } from "./format";

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

function confirmation(action: Action, name: string, behind: number | null | undefined) {
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
        detail:
          "Sessions keep running, but this app can't start the service again. To bring it back, run systemctl --user start agentop-api on that machine.",
        button: "Stop service",
      };
    case "update":
      return {
        title: `Update ${name}?`,
        detail: `${behind ? `This installs ${behind} new commit${behind === 1 ? "" : "s"} and restarts the service. ` : ""}Sessions keep running. If the new version fails, the service goes back to this one.`,
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
  if (info.behind === 0) return "Up to date";
  return info.behind === 1 ? "1 new commit" : `${info.behind} new commits`;
}

export function ServicePanel() {
  const activeId = useApp((s) => s.activeId);
  const active = useApp(activeConnection);
  const connection = useApp((s) => s.connections.find((c) => c.id === s.statusId)) ?? active;
  const setDialog = useApp((s) => s.setDialog);
  const markStopped = useApp((s) => s.markStopped);
  const updateConnection = useApp((s) => s.updateConnection);
  const removeConnection = useApp((s) => s.removeConnection);
  const { url, token } = connection;
  const client = useMemo(() => new ServiceClient(url, token), [url, token]);

  const [info, setInfo] = useState<ServiceInfo | null>(null);
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [pending, setPending] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);

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
  useEffect(() => {
    if (!updating && !restarting) return;
    const timer = setInterval(() => {
      void refresh().then((fresh) => {
        if (fresh && restarting && fresh.uptime_seconds < (info?.uptime_seconds ?? 0)) setRestarting(false);
      });
      void refreshUpdate();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [updating, restarting, refresh, refreshUpdate, info?.uptime_seconds]);

  async function perform(action: Action) {
    setBusy(true);
    setError(null);
    try {
      if (action === "stop") {
        await client.stopService();
        setDialog(null);
        if (connection.id === activeId) markStopped();
        return;
      }
      if (action === "restart") {
        await client.restartService();
        setRestarting(true);
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
  const asking = pending && confirmation(pending, name, update?.behind);

  return (
    <Dialog title={asking ? asking.title : name}>
      {asking ? (
        <>
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
        </>
      ) : (
        <>
          {unreachable && !updating && !restarting && <p className="form-error" role="alert">This service isn't answering.</p>}
          {restarting && <p className="service-progress" role="status">Restarting…</p>}
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
                <div className="detail-row"><dt>Running for</dt><dd>{formatDuration(info.uptime_seconds)}</dd></div>
                <div className="detail-row"><dt>tmux</dt><dd>{info.tmux_version ?? "Not found"}</dd></div>
                {can.update && <div className="detail-row"><dt>Updates</dt><dd>{updateSummary(update)}</dd></div>}
              </>
            )}
          </dl>
          {update && <UpdateResult info={update} />}
          {info && info.manager === "none" && (
            <p className="service-note">
              This service was started by hand, so it can't be stopped, restarted, or updated from here. Use the terminal
              it's running in.
            </p>
          )}
          <div className="dialog-actions">
            <button type="button" className="dialog-action-start" data-autofocus onClick={() => setDialog(null)}>
              Close
            </button>
            {!connection.isHome && (
              <button
                type="button"
                title="Remove it from this app. Its sessions keep running."
                onClick={() => {
                  setDialog(null);
                  removeConnection(connection.id);
                }}
              >
                Forget
              </button>
            )}
            <button type="button" disabled={!can.stop || updating || restarting} onClick={() => setPending("stop")}>
              Stop
            </button>
            <button type="button" disabled={!can.restart || updating || restarting} onClick={() => setPending("restart")}>
              Restart
            </button>
            <button
              type="button"
              className="primary"
              disabled={!can.update || updating || restarting || !update?.behind}
              onClick={() => setPending("update")}
            >
              Update
            </button>
          </div>
        </>
      )}
    </Dialog>
  );
}

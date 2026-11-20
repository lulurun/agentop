import { useEffect, useState } from "react";
import { fetchHealth } from "../api/client";
import { pageIsOutdated } from "../build";
import { appIsOutdated, connectionName, isEncrypted, isLoopback } from "../connections";
import { platform, type AppUpdate } from "../platform";
import { compareVersions } from "../platform/tauri";
import { activeClient, activeConnection, useApp } from "../store";

type Reach = "checking" | "reachable" | "unreachable";

const POLL_MS = 15_000;

export function ServiceSwitcher() {
  const connections = useApp((s) => s.connections);
  const activeId = useApp((s) => s.activeId);
  const link = useApp((s) => s.link);
  const open = useApp((s) => s.switcherOpen);
  const setOpen = useApp((s) => s.setSwitcherOpen);
  const switchTo = useApp((s) => s.switchTo);
  const setDialog = useApp((s) => s.setDialog);
  const openStatus = useApp((s) => s.openStatus);
  const active = useApp(activeConnection);
  const [reach, setReach] = useState<Record<string, Reach>>({});

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const poll = () => {
      for (const connection of connections) {
        fetchHealth(connection.url)
          .then(() => !cancelled && setReach((r) => ({ ...r, [connection.id]: "reachable" })))
          .catch(() => !cancelled && setReach((r) => ({ ...r, [connection.id]: "unreachable" })));
      }
    };
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [open, connections]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  const name = connectionName(
    active,
    link.state === "connected" || link.state === "reconnecting" ? link.service.name : undefined,
  );

  return (
    <div className="switcher">
      <button
        type="button"
        className="switcher-current"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Connections (Ctrl+Shift+2)"
        onClick={() => setOpen(!open)}
      >
        <span className={`link-dot link-${link.state}`} aria-hidden="true" />
        <span className="switcher-name">{name}</span>
        <span className="switcher-caret" aria-hidden="true" />
      </button>
      {open && (
        <>
          <div className="switcher-dismiss" onClick={() => setOpen(false)} />
          <div className="switcher-menu" role="menu" aria-label="Services">
            {connections.map((connection) => {
              const rowName = connection.id === activeId ? name : connectionName(connection);
              return (
                <div key={connection.id} className="switcher-row" data-active={connection.id === activeId || undefined}>
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={connection.id === activeId}
                    className="switcher-pick"
                    onClick={() => switchTo(connection.id)}
                  >
                    <span className={`link-dot link-${reach[connection.id] ?? "checking"}`} aria-hidden="true" />
                    <span className="switcher-name">{rowName}</span>
                    <span className="switcher-note">
                      {reach[connection.id] === "unreachable"
                        ? "Unreachable"
                        : !isEncrypted(connection.url) && !isLoopback(connection.url)
                          ? "Not encrypted"
                          : ""}
                    </span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="switcher-status"
                    aria-label={`${rowName} status`}
                    onClick={() => openStatus(connection.id)}
                  >
                    Status
                  </button>
                </div>
              );
            })}
            <div className="switcher-footer">
              <button type="button" role="menuitem" onClick={() => setDialog("add-service")}>
                Connect to a service
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * This page is older than the app its service now serves: a notice, and the user reloads.
 * Or another service's /api/v2 is newer than this UI. The desktop app shows the UI of this
 * computer's service, so there that means this computer's service is older.
 */
export function AppOutdated() {
  const link = useApp((s) => s.link);
  const url = useApp((s) => s.connections.find((c) => c.id === s.activeId)?.url ?? "");
  if (link.state !== "connected" && link.state !== "reconnecting") return null;
  if (pageIsOutdated(url, link.service.ui_build)) {
    return (
      <button type="button" className="app-outdated" title={`${link.service.name} has a newer version of this page. Reload when you're ready; sessions keep running.`} onClick={() => window.location.reload()}>
        New version: reload
      </button>
    );
  }
  if (!appIsOutdated(link.service)) return null;
  if (platform.kind === "desktop") {
    return (
      <span className="app-outdated" title={`${link.service.name} runs ${link.service.version}, which is newer than this computer's service. Some features may be missing until this computer's service is updated.`}>
        Update this computer's service
      </span>
    );
  }
  return (
    <button type="button" className="app-outdated" title={`${link.service.name} has a newer version of this app`} onClick={() => window.location.reload()}>
      Reload for the new version
    </button>
  );
}

const LATEST_POLL_MS = 60 * 60 * 1000;

/** The newest release, as the connected service last saw it on the git remote. */
function useLatestRelease(): string | null {
  const link = useApp((s) => s.link);
  const connected = link.state === "connected" || link.state === "reconnecting";
  const activeId = useApp((s) => s.activeId);
  const [latest, setLatest] = useState<string | null>(null);
  useEffect(() => {
    if (!connected) return;
    let live = true;
    const run = () =>
      void activeClient(useApp.getState())
        .latestRelease()
        .then((found) => live && setLatest(found.version ?? null))
        .catch(() => undefined);
    run();
    const timer = setInterval(run, LATEST_POLL_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [connected, activeId]);
  return latest;
}

/**
 * A newer release than the service runs. In the desktop app, a new major version links to its
 * page and any other updates this computer's agentop in place; on the web, it opens Status.
 */
export function UpdateBadge() {
  const latest = useLatestRelease();
  const link = useApp((s) => s.link);
  const activeId = useApp((s) => s.activeId);
  const openStatus = useApp((s) => s.openStatus);
  const [update, setUpdate] = useState<AppUpdate | null>(null);
  const [installing, setInstalling] = useState(false);
  const [failed, setFailed] = useState("");
  const running = link.state === "connected" || link.state === "reconnecting" ? link.service : null;

  useEffect(() => {
    const check = platform.checkAppUpdate;
    if (!check) return;
    let live = true;
    void check(latest)
      .then((found) => live && setUpdate(found))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [latest, running?.version]);

  if (!platform.checkAppUpdate) {
    if (!running || !latest || compareVersions(latest, running.version) <= 0) return null;
    return (
      <button
        type="button"
        className="app-outdated"
        title={`${running.name} runs ${running.version}. Update it from its Status; this page then offers to reload.`}
        onClick={() => activeId && openStatus(activeId)}
      >
        {`agentop ${latest} is out`}
      </button>
    );
  }
  if (!update) return null;
  const version = update.version ? ` ${update.version}` : "";
  if (update.kind === "service") {
    return (
      <button
        type="button"
        className="app-outdated"
        title={failed ? `The update couldn't start: ${failed}` : `Update this computer's agentop to${version}. The service restarts; sessions keep running.`}
        disabled={installing}
        onClick={async () => {
          setInstalling(true);
          try {
            await update.install?.();
          } catch (error) {
            setFailed(String(error));
            setInstalling(false);
          }
        }}
      >
        {`Update agentop${version}`}
      </button>
    );
  }
  return (
    <button
      type="button"
      className="app-outdated"
      title={`agentop-desktop${version} is out. Open the release page to download and install it.`}
      onClick={() => void platform.openUrl?.(update.releaseUrl)}
    >
      {update.required ? "App update needed" : "App update"}
    </button>
  );
}

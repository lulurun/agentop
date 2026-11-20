import { useEffect, useRef, useState } from "react";
import { fetchHealth, ServiceClient, type VersionInfo } from "../api/client";
import { ownBuild } from "../build";
import { appIsOutdated, connectionName, isEncrypted, isLoopback } from "../connections";
import { platform, type AppUpdate } from "../platform";
import { activeConnection, useApp } from "../store";

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
 * Another service's /api/v2 is newer than this UI. The desktop app shows the UI of this
 * computer's service, so there that means this computer's service is older.
 */
export function AppOutdated() {
  const link = useApp((s) => s.link);
  if (link.state !== "connected" && link.state !== "reconnecting") return null;
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

const VERSION_POLL_MS = 60 * 60 * 1000;

/**
 * The versions of the service that served this page, asked hourly and when it restarts. Its first
 * answer is the page's own version, so a later one that differs means the page is out of date.
 */
function useHomeVersion(): { info: VersionInfo; stale: boolean } | null {
  const id = useApp((s) => s.connections.find((c) => c.isHome)?.id);
  const url = useApp((s) => s.connections.find((c) => c.isHome)?.url ?? "");
  const signedIn = useApp((s) => !!s.connections.find((c) => c.isHome)?.token);
  const restarted = useApp((s) =>
    s.activeId === id && (s.link.state === "connected" || s.link.state === "reconnecting") ? s.link.service.started_at : null,
  );
  const page = useRef<{ current: string | null; build: string | null } | null>(null);
  const [info, setInfo] = useState<VersionInfo | null>(null);
  useEffect(() => {
    if (!url || !signedIn) return;
    const client = new ServiceClient(url, () => useApp.getState().connections.find((c) => c.id === id)?.token ?? "");
    let live = true;
    const run = () =>
      void client
        .versionInfo()
        .then((found) => {
          if (!live) return;
          page.current ??= { current: found.current, build: ownBuild() };
          setInfo(found);
        })
        .catch(() => undefined);
    run();
    const timer = setInterval(run, VERSION_POLL_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [id, url, signedIn, restarted]);
  if (!info || !page.current) return null;
  const own = page.current;
  const stale = info.current !== own.current || (!!own.build && !!info.ui_build && info.ui_build !== own.build);
  return { info, stale };
}

/**
 * The service that served this page has a newer version than the page, or Update would bring one.
 * The desktop app, on a service following releases, decides by the part of X.Y.Z that changed:
 * X is a new app to download, Y or Z updates this computer's agentop in place.
 */
export function UpdateBadge() {
  const home = useHomeVersion();
  const homeId = useApp((s) => s.connections.find((c) => c.isHome)?.id);
  const openStatus = useApp((s) => s.openStatus);
  const [update, setUpdate] = useState<AppUpdate | null>(null);
  const [installing, setInstalling] = useState(false);
  const [failed, setFailed] = useState("");
  const info = home?.info;
  const release =
    info?.update_from === "release" && info.current && info.latest ? { current: info.current, latest: info.latest } : null;

  useEffect(() => {
    const check = platform.checkAppUpdate;
    if (!check) return;
    let live = true;
    void check(release)
      .then((found) => live && setUpdate(found))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [release?.current, release?.latest]);

  if (home?.stale && !update?.required) {
    return (
      <button
        type="button"
        className="app-outdated"
        title="The service that opened this page has a newer version of it. Reload when you're ready; sessions keep running."
        onClick={() => window.location.reload()}
      >
        New version: reload
      </button>
    );
  }
  if (!update) {
    if (!info?.latest || info.latest === info.current || (platform.checkAppUpdate && release)) return null;
    return (
      <button
        type="button"
        className="app-outdated"
        title="Update it from its Status; this page then offers to reload."
        onClick={() => homeId && openStatus(homeId)}
      >
        {info.update_from === "release" ? `agentop ${info.latest} is out` : "New commits on main"}
      </button>
    );
  }
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

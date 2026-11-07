import { useEffect, useState } from "react";
import { fetchHealth } from "../api/client";
import { isEncrypted, isLoopback } from "../connections";
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

  const name = link.state === "connected" || link.state === "reconnecting" ? link.service.name : active.label;

  return (
    <div className="switcher">
      <button
        type="button"
        className="switcher-current"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Switch service (Ctrl+Shift+S)"
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
            {connections.map((connection) => (
              <button
                key={connection.id}
                type="button"
                role="menuitemradio"
                aria-checked={connection.id === activeId}
                className="switcher-row"
                onClick={() => switchTo(connection.id)}
              >
                <span className={`link-dot link-${reach[connection.id] ?? "checking"}`} aria-hidden="true" />
                <span className="switcher-name">{connection.label}</span>
                <span className="switcher-note">
                  {reach[connection.id] === "unreachable"
                    ? "Unreachable"
                    : !isEncrypted(connection.url) && !isLoopback(connection.url)
                      ? "Not encrypted"
                      : connection.isHome
                        ? "This service"
                        : ""}
                </span>
              </button>
            ))}
            <div className="switcher-footer">
              <button type="button" role="menuitem" onClick={() => setDialog("add-service")}>
                Add service
              </button>
              <button type="button" role="menuitem" onClick={() => setDialog("manage-services")}>
                Manage services
              </button>
              <button type="button" role="menuitem" onClick={() => setDialog("service")}>
                {name} status
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

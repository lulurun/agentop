import { useCallback, useEffect, useState } from "react";
import { ApiError, type ClientInfo, type ServiceClient } from "../api/client";
import { SIGNED_OUT, type Connection } from "../connections";
import { useApp } from "../store";
import { formatDuration } from "./format";

const words = (seconds: number) => (seconds < 60 ? "under a minute" : formatDuration(seconds));
const ago = (epoch: number) => words(Math.max(0, Math.round(Date.now() / 1000 - epoch)));
const until = (epoch: number) => words(Math.max(0, Math.round(epoch - Date.now() / 1000)));

/** The apps and browsers signed in to a service. Each can be signed out on its own. */
export function ClientsSection({ client, connection }: { client: ServiceClient; connection: Connection }) {
  const activeId = useApp((s) => s.activeId);
  const signOut = useApp((s) => s.signOut);
  const updateConnection = useApp((s) => s.updateConnection);
  const [clients, setClients] = useState<ClientInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(
    () =>
      client
        .clients()
        .then((list) => {
          setClients(list);
          setError(null);
        })
        .catch((failure) => setError(failure instanceof ApiError && failure.status === 404 ? null : "Couldn't list them.")),
    [client],
  );

  useEffect(() => {
    if (connection.token) void refresh();
  }, [refresh, connection.token]);

  async function revoke(info: ClientInfo) {
    if (info.current) {
      if (connection.id === activeId) await signOut();
      else {
        await client.revokeClient(info.id).catch(() => undefined);
        updateConnection(connection.id, SIGNED_OUT);
      }
      return;
    }
    try {
      await client.revokeClient(info.id);
    } catch {
      setError("Couldn't sign it out.");
    }
    void refresh();
  }

  if (!clients && !error) return null;
  return (
    <section className="settings-plugins" aria-label="Signed in">
      <h3>Signed in</h3>
      {error && <p className="form-error" role="alert">{error}</p>}
      <ul className="plugin-list">
        {clients?.map((info) => (
          <li key={info.id} className="plugin-entry">
            <div className="plugin-head">
              <strong>
                {info.name}
                {info.current && " (this app)"}
              </strong>
              <button type="button" className="client-revoke" onClick={() => void revoke(info)}>
                Sign out
              </button>
            </div>
            <p className="plugin-description">
              Added {ago(info.created_at)} ago, last seen {ago(info.last_seen)} ago. Signed out in {until(info.expires_at)} unless
              used.
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

import { useState, type FormEvent } from "react";
import { checkConnection, failureMessage, isEncrypted, isLoopback, newConnectionId, parseConnectInfo, reachabilityNote, type ConnectResult, type Connection } from "../connections";
import { useApp } from "../store";
import { Dialog } from "./Dialog";

export function AddServiceDialog() {
  const addConnection = useApp((s) => s.addConnection);
  const setDialog = useApp((s) => s.setDialog);
  const [address, setAddress] = useState("");
  const [token, setToken] = useState("");
  const [label, setLabel] = useState("");
  const [result, setResult] = useState<ConnectResult | null>(null);
  const [testing, setTesting] = useState(false);

  const parsed = parseConnectInfo(address);
  const effectiveToken = token || parsed?.token || "";
  const note = parsed ? reachabilityNote(location.href, parsed.url) : null;
  const unencrypted = parsed && !isEncrypted(parsed.url) && !isLoopback(parsed.url);

  function draft(): Connection {
    return { id: newConnectionId(), label: label.trim(), url: parsed!.url, token: effectiveToken };
  }

  async function test(event: FormEvent) {
    event.preventDefault();
    setTesting(true);
    const outcome = await checkConnection(draft());
    setResult(outcome);
    setTesting(false);
  }

  function save() {
    if (!result?.ok) return;
    addConnection({ ...draft(), label: label.trim() || result.health.name, instanceId: result.health.instance_id });
  }

  return (
    <Dialog title="Add service">
      <form onSubmit={(event) => void test(event)}>
        <label className="field">
          Address
          <input
            autoFocus
            value={address}
            spellCheck={false}
            placeholder="http://build-box:9775"
            onChange={(event) => { setAddress(event.target.value); setResult(null); }}
          />
        </label>
        <p className="field-hint">On the other machine, agentop service connect-info prints a string you can paste here.</p>
        {!parsed?.token && (
          <label className="field">
            Token
            <input type="text" spellCheck={false} autoCapitalize="off" autoComplete="off" value={token} onChange={(event) => { setToken(event.target.value); setResult(null); }} />
          </label>
        )}
        <label className="field">
          Label
          <input value={label} placeholder="Optional, taken from the service" onChange={(event) => setLabel(event.target.value)} />
        </label>
        {unencrypted && <p className="form-warning">Not encrypted. Anyone on this network can read the token and terminal. A tunnel or HTTPS is safer.</p>}
        {note && <p className="form-warning">{note.message}</p>}
        {result?.ok && <p className="form-success">Found {result.health.name}, version {result.health.version}.</p>}
        {result && !result.ok && (
          <p className="form-error" role="alert">
            {failureMessage(result, parsed?.url ?? "this service").title}. {failureMessage(result, parsed?.url ?? "this service").detail}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" onClick={() => setDialog(null)}>Cancel</button>
          <button type="submit" disabled={!parsed || testing}>Test connection</button>
          <button type="button" className="primary" disabled={!result?.ok} onClick={save}>Add and connect</button>
        </div>
      </form>
    </Dialog>
  );
}

export function ManageServicesDialog() {
  const connections = useApp((s) => s.connections);
  const updateConnection = useApp((s) => s.updateConnection);
  const removeConnection = useApp((s) => s.removeConnection);
  const setDialog = useApp((s) => s.setDialog);

  return (
    <Dialog title="Manage services">
      <p className="field-hint">Removing a service here only forgets it in this app. Its sessions keep running.</p>
      <ul className="service-list">
        {connections.map((connection) => (
          <li key={connection.id}>
            <input
              aria-label={`Label for ${connection.url}`}
              value={connection.label}
              onChange={(event) => updateConnection(connection.id, { label: event.target.value })}
            />
            <span className="service-url">{connection.url}</span>
            {!connection.isHome && (
              <button type="button" onClick={() => removeConnection(connection.id)}>Remove</button>
            )}
          </li>
        ))}
      </ul>
      <div className="dialog-actions">
        <button type="button" className="primary" onClick={() => setDialog(null)}>Done</button>
      </div>
    </Dialog>
  );
}

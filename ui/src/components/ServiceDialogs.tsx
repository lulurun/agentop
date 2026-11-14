import { useState, type FormEvent } from "react";
import { checkConnection, failureMessage, isPlainRemote, newConnectionId, parseConnectInfo, reachabilityNote, type ConnectResult, type Connection } from "../connections";
import { useApp } from "../store";
import { Dialog } from "./Dialog";

export function AddServiceDialog() {
  const addConnection = useApp((s) => s.addConnection);
  const setDialog = useApp((s) => s.setDialog);
  const [address, setAddress] = useState("");
  const [token, setToken] = useState("");
  const [label, setLabel] = useState("");
  const [result, setResult] = useState<ConnectResult | null>(null);
  // The ID the test was made with: its sign-in belongs to that connection.
  const [tested, setTested] = useState<Connection | null>(null);
  const [testing, setTesting] = useState(false);

  const parsed = parseConnectInfo(address);
  const effectiveToken = token || parsed?.token || "";
  const note = parsed ? reachabilityNote(location.href, parsed.url) : null;
  const unencrypted = !!parsed && isPlainRemote(parsed.url);

  function draft(): Connection {
    return { id: newConnectionId(), label: "", alias: label.trim() || undefined, url: parsed!.url, token: "", serviceToken: effectiveToken };
  }

  async function test(event: FormEvent) {
    event.preventDefault();
    if (!parsed || unencrypted) return;
    setTesting(true);
    const candidate = draft();
    const outcome = await checkConnection(candidate);
    setTested(candidate);
    setResult(outcome);
    setTesting(false);
  }

  function save() {
    if (!result?.ok || !tested) return;
    // Signed in by the test: the service token is not kept.
    addConnection({
      ...tested,
      alias: label.trim() || undefined,
      ...result.update,
      serviceToken: undefined,
      label: result.service.name,
    });
  }

  return (
    <Dialog title="Connect to a service">
      <form onSubmit={(event) => void test(event)}>
        <label className="field">
          Address
          <input
            autoFocus
            value={address}
            spellCheck={false}
            placeholder="https://build-box:9775"
            onChange={(event) => { setAddress(event.target.value); setResult(null); }}
          />
        </label>
        <p className="field-hint">On the other machine, agentop service connect-info prints a string you can paste here.</p>
        {!parsed?.token && (
          <label className="field">
            Token
            <input type="text" spellCheck={false} autoCapitalize="off" autoComplete="off" disabled={unencrypted} value={token} onChange={(event) => { setToken(event.target.value); setResult(null); }} />
          </label>
        )}
        <label className="field">
          Name
          <input value={label} disabled={unencrypted} placeholder="Optional, taken from the service" onChange={(event) => setLabel(event.target.value)} />
        </label>
        {unencrypted && (
          <p className="form-warning" role="alert">
            Plain HTTP to another machine is not allowed: anyone on the network could read the token and every terminal. Use an
            https:// address, or an SSH tunnel to the service and http://127.0.0.1 (see the README).
          </p>
        )}
        {note && !unencrypted && <p className="form-warning">{note.message}</p>}
        {result?.ok && <p className="form-success">Found {result.service.name}, version {result.service.version}.</p>}
        {result && !result.ok && (
          <p className="form-error" role="alert">
            {failureMessage(result, parsed?.url ?? "this service").title}. {failureMessage(result, parsed?.url ?? "this service").detail}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" onClick={() => setDialog(null)}>Cancel</button>
          <button type="submit" disabled={!parsed || unencrypted || testing}>Test connection</button>
          <button type="button" className="primary" disabled={!result?.ok || unencrypted} onClick={save}>Add and connect</button>
        </div>
      </form>
    </Dialog>
  );
}

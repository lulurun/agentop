import { useState, type FormEvent } from "react";
import { checkReachable, failureMessage, isPlainRemote, newConnectionId, parseConnectInfo, reachabilityNote, type ConnectFailure } from "../connections";
import { useApp } from "../store";
import { Dialog } from "./Dialog";

/**
 * The test only checks that an agentop service answers. The connection is added signed out,
 * and the main area then offers the service's ways to sign in. A connect-info string's token
 * still signs in on the first connect.
 */
export function AddServiceDialog() {
  const addConnection = useApp((s) => s.addConnection);
  const setDialog = useApp((s) => s.setDialog);
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [result, setResult] = useState<{ ok: true; url: string } | ({ ok: false } & ConnectFailure) | null>(null);
  const [testing, setTesting] = useState(false);

  const parsed = parseConnectInfo(address);
  const note = parsed ? reachabilityNote(location.href, parsed.url) : null;
  const unencrypted = !!parsed && isPlainRemote(parsed.url);
  const reachable = result?.ok && result.url === parsed?.url;

  async function test(event: FormEvent) {
    event.preventDefault();
    if (!parsed || unencrypted) return;
    setTesting(true);
    const outcome = await checkReachable(parsed.url);
    setResult(outcome.ok ? { ok: true, url: parsed.url } : outcome);
    setTesting(false);
  }

  function save() {
    if (!reachable || !parsed) return;
    addConnection({
      id: newConnectionId(),
      label: new URL(parsed.url).host,
      alias: label.trim() || undefined,
      url: parsed.url,
      token: "",
      serviceToken: parsed.token || undefined,
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
        {reachable && <p className="form-success">Found an agentop service. You'll sign in once it's added.</p>}
        {result && !result.ok && (
          <p className="form-error" role="alert">
            {failureMessage(result, parsed?.url ?? "this service").title}. {failureMessage(result, parsed?.url ?? "this service").detail}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" onClick={() => setDialog(null)}>Cancel</button>
          <button type="submit" disabled={!parsed || unencrypted || testing}>Test connection</button>
          <button type="button" className="primary" disabled={!reachable || unencrypted} onClick={save}>Add and connect</button>
        </div>
      </form>
    </Dialog>
  );
}

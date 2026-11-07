import { useEffect, useMemo, useState, type FormEvent } from "react";
import type { DirListing, ToolInfo } from "../api/client";
import { activeClient, useApp } from "../store";
import { Dialog } from "./Dialog";

const RECENT_KEY = "agentop.recentDirs";

function loadRecent(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function saveRecent(dir: string, recent: string[]) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify([dir, ...recent.filter((d) => d !== dir)].slice(0, 6)));
  } catch {
    // Only a convenience.
  }
}

export function NewSessionDialog() {
  const connections = useApp((s) => s.connections);
  const activeId = useApp((s) => s.activeId);
  const setDialog = useApp((s) => s.setDialog);
  const select = useApp((s) => s.select);
  const client = useMemo(() => activeClient({ connections, activeId }), [connections, activeId]);

  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [tool, setTool] = useState("");
  const [cwd, setCwd] = useState("");
  const [name, setName] = useState("");
  const [listing, setListing] = useState<DirListing | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const recent = useMemo(loadRecent, []);

  useEffect(() => {
    client.tools().then((list) => {
      setTools(list);
      setTool((current) => current || (list.find((t) => t.installed)?.name ?? ""));
    }).catch((e) => setError(e.message));
    client.dirs(recent[0] ?? "~").then((result) => {
      setListing(result);
      setCwd(result.path);
    }).catch(() => client.dirs("~").then((result) => {
      setListing(result);
      setCwd(result.path);
    }));
  }, [client, recent]);

  function browse(path: string) {
    setCwd(path);
    client.dirs(path).then(setListing).catch(() => setListing(null));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const session = await client.create({ tool, cwd, name: name.trim() || null });
      saveRecent(cwd, recent);
      useApp.setState((s) => ({ sessions: { ...s.sessions, [session.id]: session } }));
      select(session.id);
      setDialog(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  }

  return (
    <Dialog title="New session">
      <form onSubmit={(event) => void submit(event)}>
        <fieldset className="tool-choice">
          <legend>Agent</legend>
          {tools.map((t) => (
            <label key={t.name} data-disabled={!t.installed} title={t.installed ? undefined : `${t.name} was not found on this service`}>
              <input type="radio" name="tool" value={t.name} checked={tool === t.name} disabled={!t.installed} onChange={() => setTool(t.name)} />
              {t.name}
            </label>
          ))}
        </fieldset>
        <label className="field">
          Working directory
          <input
            value={cwd}
            spellCheck={false}
            onChange={(event) => browse(event.target.value)}
            aria-describedby="dir-list"
          />
        </label>
        {listing && (
          <ul className="dir-list" id="dir-list">
            {listing.parent && (
              <li><button type="button" onClick={() => browse(listing.parent!)}>..</button></li>
            )}
            {listing.dirs.map((dir) => (
              <li key={dir}>
                <button type="button" onClick={() => browse(`${listing.path.replace(/\/$/, "")}/${dir}`)}>{dir}</button>
              </li>
            ))}
          </ul>
        )}
        <label className="field">
          Name
          <input value={name} placeholder="Optional, taken from the directory" onChange={(event) => setName(event.target.value)} maxLength={32} spellCheck={false} />
        </label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button type="button" onClick={() => setDialog(null)}>Cancel</button>
          <button type="submit" className="primary" disabled={!tool || !cwd || submitting}>Start session</button>
        </div>
      </form>
    </Dialog>
  );
}

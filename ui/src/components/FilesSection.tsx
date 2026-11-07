import { useCallback, useEffect, useMemo, useState } from "react";
import type { FileEntry, FileListing, GitChanges } from "../api/client";
import { platform } from "../platform";
import { activeClient, useApp } from "../store";

const REFRESH_MS = 10_000;

const STATE_LABEL: Record<string, string> = {
  M: "Modified",
  A: "Added",
  D: "Deleted",
  R: "Renamed",
  U: "Conflict",
  "?": "New file",
};

function useClient() {
  const connections = useApp((s) => s.connections);
  const activeId = useApp((s) => s.activeId);
  return useMemo(() => activeClient({ connections, activeId }), [connections, activeId]);
}

function Changed({ sessionId }: { sessionId: string }) {
  const client = useClient();
  const [changes, setChanges] = useState<GitChanges | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      if (document.visibilityState === "hidden") return;
      client.changes(sessionId).then(
        (result) => {
          if (!cancelled) {
            setChanges(result);
            setFailed(false);
          }
        },
        () => !cancelled && setFailed(true),
      );
    };
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [client, sessionId]);

  if (failed) return <p className="file-note">Could not read this directory.</p>;
  if (!changes) return null;
  if (!changes.is_repo) return <p className="file-note">This directory isn't a git repository.</p>;

  const position = [changes.ahead && `${changes.ahead} ahead`, changes.behind && `${changes.behind} behind`]
    .filter(Boolean)
    .join(", ");
  return (
    <>
      <p className="file-note">
        {changes.branch ?? "Detached HEAD"}
        {position && `, ${position}`}
      </p>
      {changes.files.length === 0 && <p className="file-note">No changes.</p>}
      <ul className="file-list">
        {changes.files.map((file) => (
          <li key={file.path}>
            <button type="button" className="file-row" title="Copy path" onClick={() => void platform.copyText(file.path)}>
              <span className="file-state" data-state={file.status} title={STATE_LABEL[file.status]}>
                {file.status === "?" ? "N" : file.status}
              </span>
              <span>{file.path}</span>
            </button>
          </li>
        ))}
      </ul>
      {changes.truncated && <p className="file-note">Showing the first {changes.files.length} files.</p>}
    </>
  );
}

function Node({ sessionId, entry, root }: { sessionId: string; entry: FileEntry; root: string }) {
  const [open, setOpen] = useState(false);

  if (!entry.is_dir) {
    return (
      <li>
        <button type="button" className="file-row" title="Copy path" onClick={() => void platform.copyText(`${root}/${entry.path}`)}>
          <span className="file-glyph" aria-hidden="true" />
          <span>{entry.name}</span>
        </button>
      </li>
    );
  }
  return (
    <li>
      <button type="button" className="file-row" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="file-glyph" aria-hidden="true">{open ? "−" : "+"}</span>
        <span>{entry.name}</span>
      </button>
      {open && <Directory sessionId={sessionId} path={entry.path} />}
    </li>
  );
}

function Directory({ sessionId, path }: { sessionId: string; path: string }) {
  const client = useClient();
  const [listing, setListing] = useState<FileListing | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    client.files(sessionId, path).then(setListing, () => setFailed(true));
  }, [client, sessionId, path]);
  useEffect(load, [load]);

  if (failed) return <p className="file-note">Could not read this folder.</p>;
  if (!listing) return null;
  return (
    <ul className={path ? undefined : "file-list"}>
      {listing.entries.length === 0 && !path && <li className="file-note">This directory is empty.</li>}
      {listing.entries.map((entry) => (
        <Node key={entry.path} sessionId={sessionId} entry={entry} root={listing.root} />
      ))}
      {listing.truncated && <li className="file-note">Showing the first {listing.entries.length} items.</li>}
    </ul>
  );
}

export function FilesSection({ sessionId }: { sessionId: string }) {
  const [tab, setTab] = useState<"changed" | "all">("changed");
  return (
    <section>
      <h3>Files</h3>
      <div className="file-tabs" role="tablist" aria-label="Files">
        <button type="button" role="tab" aria-selected={tab === "changed"} onClick={() => setTab("changed")}>Changed</button>
        <button type="button" role="tab" aria-selected={tab === "all"} onClick={() => setTab("all")}>All</button>
      </div>
      {tab === "changed" ? <Changed sessionId={sessionId} /> : <Directory sessionId={sessionId} path="" />}
    </section>
  );
}

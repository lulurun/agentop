import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type { FileEntry, FileListing, GitChanges, TranscriptListing } from "../api/client";
import { platform } from "../platform";
import { useFileViewer } from "../plugins/Slot";
import { activeClient, useApp } from "../store";
import type { FileRef } from "../tabs";

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

/**
 * A file in a list. With a file viewer enabled it opens in a tab, and Ctrl or
 * Cmd opens it beside the current one. Without one, clicking copies its path.
 */
function FileRow({ file, absolute, children }: { file: FileRef | null; absolute: string; children: ReactNode }) {
  const viewer = useFileViewer();
  const openTab = useApp((s) => s.openTab);
  if (!viewer || !file) {
    return (
      <button type="button" className="file-row" title="Copy path" onClick={() => void platform.copyText(absolute)}>
        {children}
      </button>
    );
  }
  return (
    <button
      type="button"
      className="file-row"
      title={absolute}
      onClick={(event) => openTab({ kind: "file", file }, event.ctrlKey || event.metaKey ? "side" : "tab")}
    >
      {children}
    </button>
  );
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
            <FileRow
              absolute={file.path}
              // Changed paths are from the top of the repository. Only those inside the session's directory can be opened.
              file={
                file.status !== "D" && file.path.startsWith(changes.prefix)
                  ? { sessionId, source: "workdir", path: file.path.slice(changes.prefix.length) }
                  : null
              }
            >
              <span className="file-state" data-state={file.status} title={STATE_LABEL[file.status]}>
                {file.status === "?" ? "N" : file.status}
              </span>
              <span>{file.path}</span>
            </FileRow>
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
        <FileRow absolute={`${root}/${entry.path}`} file={{ sessionId, source: "workdir", path: entry.path }}>
          <span className="file-glyph" aria-hidden="true" />
          <span>{entry.name}</span>
        </FileRow>
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

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** The agent tool's own log files for this session: the conversation, and whatever it keeps beside it. */
export function TranscriptsSection({ sessionId }: { sessionId: string }) {
  const client = useClient();
  const [listing, setListing] = useState<TranscriptListing | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setListing(null);
    const load = () => {
      if (document.visibilityState === "hidden") return;
      client.transcripts(sessionId).then(
        (result) => {
          if (!cancelled) {
            setListing(result);
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

  return (
    <section>
      <h3>Transcripts</h3>
      {failed ? (
        <p className="file-note">Could not list the transcripts.</p>
      ) : !listing ? null : listing.files.length === 0 ? (
        <p className="file-note">The agent hasn't reported a transcript for this session.</p>
      ) : (
        <>
          {listing.archived && <p className="file-note">The agent deleted these files. This is agentop's archived copy.</p>}
          <ul className="file-list">
            {listing.files.map((file) => (
              <li key={file.path}>
                <FileRow absolute={`${listing.root}/${file.path}`} file={{ sessionId, source: "transcript", path: file.path }}>
                  <span className="file-glyph" aria-hidden="true" />
                  <span>{file.path}</span>
                  <span className="file-size">{formatSize(file.size)}</span>
                </FileRow>
              </li>
            ))}
          </ul>
          {listing.truncated && <p className="file-note">Showing the first {listing.files.length} files.</p>}
        </>
      )}
    </section>
  );
}

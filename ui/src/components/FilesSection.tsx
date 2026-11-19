import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { FileEntry, FileListing, GitChanges } from "../api/client";
import { platform } from "../platform";
import { useFileViewer } from "../plugins/Slot";
import { useActiveClient, useApp } from "../store";
import { Section } from "./Section";
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

/**
 * A file in a list. With a file viewer enabled it opens in a tab in the right
 * group. Without one, clicking copies its path.
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
      onClick={() => openTab({ kind: "file", file }, "right")}
    >
      {children}
    </button>
  );
}

function Changed({ sessionId }: { sessionId: string }) {
  const client = useActiveClient();
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
              // They open as their diff, so a deleted file opens too.
              file={
                file.path.startsWith(changes.prefix)
                  ? { sessionId, source: "workdir", path: file.path.slice(changes.prefix.length), view: "diff" }
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

type Tree = "workdir" | "memory";

function Node({ sessionId, tree, entry, root }: { sessionId: string; tree: Tree; entry: FileEntry; root: string }) {
  const [open, setOpen] = useState(false);

  if (!entry.is_dir) {
    return (
      <li>
        <FileRow absolute={`${root}/${entry.path}`} file={{ sessionId, source: tree, path: entry.path }}>
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
      {open && <Directory sessionId={sessionId} tree={tree} path={entry.path} />}
    </li>
  );
}

function Directory({ sessionId, tree, path }: { sessionId: string; tree: Tree; path: string }) {
  const client = useActiveClient();
  const [listing, setListing] = useState<FileListing | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    const list = tree === "memory" ? client.memoryFiles : client.files;
    list(sessionId, path).then(setListing, () => setFailed(true));
  }, [client, sessionId, tree, path]);
  useEffect(load, [load]);

  if (failed) return <p className="file-note">Could not read this folder.</p>;
  if (!listing) return null;
  return (
    <ul className={path ? undefined : "file-list"}>
      {!path && tree === "memory" && <li className="file-note">{listing.root}</li>}
      {listing.entries.length === 0 && !path && <li className="file-note">This directory is empty.</li>}
      {listing.entries.map((entry) => (
        <Node key={entry.path} sessionId={sessionId} tree={tree} entry={entry} root={listing.root} />
      ))}
      {listing.truncated && <li className="file-note">Showing the first {listing.entries.length} items.</li>}
    </ul>
  );
}

/** Whether the session's agent tool keeps a memory folder, which only Claude Code does. */
function useHasMemory(sessionId: string): boolean {
  const client = useActiveClient();
  const [has, setHas] = useState(false);
  useEffect(() => {
    let cancelled = false;
    client.memoryFiles(sessionId).then(
      () => !cancelled && setHas(true),
      () => !cancelled && setHas(false),
    );
    return () => {
      cancelled = true;
    };
  }, [client, sessionId]);
  return has;
}

export function FilesSection({ sessionId, tool }: { sessionId: string; tool: string | null }) {
  const [tab, setTab] = useState<"changed" | "all" | "memory">("changed");
  const hasMemory = useHasMemory(sessionId);
  const shown = tab === "memory" && !hasMemory ? "changed" : tab;
  return (
    <Section id="files" title="Files">
      <div className="file-tabs" role="tablist" aria-label="Files">
        <button type="button" role="tab" aria-selected={shown === "changed"} onClick={() => setTab("changed")}>Changed</button>
        <button type="button" role="tab" aria-selected={shown === "all"} onClick={() => setTab("all")}>All</button>
        {hasMemory && (
          <button
            type="button"
            role="tab"
            aria-selected={shown === "memory"}
            title="The notes the agent tool keeps for this project"
            onClick={() => setTab("memory")}
          >
            {tool ? `${tool[0].toUpperCase()}${tool.slice(1)} memory` : "Memory"}
          </button>
        )}
      </div>
      {shown === "changed" && <Changed sessionId={sessionId} />}
      {shown === "all" && <Directory sessionId={sessionId} tree="workdir" path="" />}
      {shown === "memory" && <Directory sessionId={sessionId} tree="memory" path="" />}
    </Section>
  );
}

import { useEffect, useMemo, useState } from "react";
import type { HistoryItem } from "../api/client";
import { activeCount, useApp } from "../store";
import { Row } from "./DetailsPanel";
import { FoldSection, ToolBadge } from "./Sidebar";

function when(seconds: number | null | undefined): string {
  return seconds != null ? new Date(seconds * 1000).toLocaleString() : "Unknown";
}

/** Short, for a row: the month, day and time. */
function short(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function days(seconds: number | null): string {
  if (seconds == null) return "";
  const d = Math.round(seconds / 86400);
  return d === 1 ? "the last day" : `the last ${d} days`;
}

/** The past session a tab shows, from the History list. */
function useItem(historyId: string): HistoryItem | undefined {
  return useApp((s) => s.history.items?.find((i) => i.id === historyId));
}

/** Whether another session may be resumed now, and the limit. */
function useLimit(): { atLimit: boolean; max: number } {
  const max = useApp((s) => s.history.maxActive);
  const active = useApp((s) => activeCount(s.sessions));
  return { atLimit: active >= max, max };
}

export const LIMIT_MESSAGE = "Active sessions are at the limit. Close a session to resume this one.";

/**
 * History, under the active sessions in Sessions' left panel: the past sessions
 * agentop started and used within the window, newest first.
 */
export function HistorySection({ selected }: { selected?: string }) {
  const history = useApp((s) => s.history);
  const loadHistory = useApp((s) => s.loadHistory);
  const openTab = useApp((s) => s.openTab);
  const sessionIds = useApp((s) => Object.keys(s.sessions).sort().join("\n"));
  const [search, setSearch] = useState("");
  const [tool, setTool] = useState("");

  // A closed session joins History, and a resumed one leaves it.
  useEffect(() => {
    const timer = setTimeout(() => void loadHistory(), history.items === null ? 0 : 1500);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionIds, loadHistory]);

  const tools = useMemo(() => [...new Set((history.items ?? []).map((i) => i.tool))].sort(), [history.items]);
  const needle = search.trim().toLowerCase();
  const rows = useMemo(
    () =>
      (history.items ?? [])
        .filter(
          (i) =>
            (!tool || i.tool === tool) &&
            (!needle || `${i.name} ${i.title ?? ""} ${i.description} ${i.cwd}`.toLowerCase().includes(needle)),
        )
        .sort((a, b) => b.last_active - a.last_active),
    [history.items, tool, needle],
  );

  function open(item: HistoryItem, mode: "replace" | "tab") {
    openTab({ kind: "past", historyId: item.id }, mode);
  }

  return (
    <FoldSection
      id="history"
      title="History"
      hint={history.windowSeconds ? `Sessions agentop started, used in ${days(history.windowSeconds)}` : undefined}
      extra={history.items && <span className="session-count">{history.items.length}</span>}
    >
      <div className="sidebar-actions history-tools">
        <input type="search" aria-label="Filter history" placeholder="Filter history" value={search} onChange={(event) => setSearch(event.target.value)} />
        <select aria-label="CLI" value={tool} onChange={(event) => setTool(event.target.value)}>
          <option value="">All CLIs</option>
          {tools.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </div>
      <nav className="session-list" aria-label="Past sessions">
        {history.items === null ? (
          <p className="session-empty">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="session-empty">{needle || tool ? "No past sessions match." : `No sessions ended in ${days(history.windowSeconds)}.`}</p>
        ) : (
          rows.map((item) => (
            <button
              key={item.id}
              type="button"
              className="session-row history-row"
              title={item.cwd}
              aria-current={item.id === selected ? "true" : undefined}
              onClick={(event) => open(item, event.ctrlKey || event.metaKey ? "tab" : "replace")}
              onAuxClick={(event) => {
                if (event.button === 1) open(item, "tab");
              }}
            >
              <span className="session-name">{item.name}</span>
              <span className="session-meta">
                <ToolBadge tool={item.tool} />
                <span title={when(item.last_active)}>{short(item.last_active)}</span>
                <span className="session-summary">{item.description || item.title || item.cwd}</span>
              </span>
            </button>
          ))
        )}
      </nav>
    </FoldSection>
  );
}

/** A past session in the main area: what it was, and Resume or Delete. */
export function PastSessionView({ historyId, onClose }: { historyId: string; onClose(): void }) {
  const item = useItem(historyId);
  const loaded = useApp((s) => s.history.items !== null);
  const resumeHistory = useApp((s) => s.resumeHistory);
  const removeHistory = useApp((s) => s.removeHistory);
  const { atLimit } = useLimit();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => setConfirming(false), [historyId]);

  if (!item) {
    return (
      <section className="view past-session" aria-label="Past session">
        <p className="view-empty">{loaded ? "This session is no longer in History: it was resumed, deleted, or last used too long ago." : "Loading…"}</p>
        {loaded && <div className="stop-buttons"><button type="button" onClick={onClose}>Close tab</button></div>}
      </section>
    );
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  }

  const resumable = !!item.cwd && !atLimit;
  return (
    <section className="view past-session" aria-label="Past session">
      <h2>{item.name}</h2>
      <dl>
        <Row label="Title" value={item.title || "Untitled"} />
        {item.description && <Row label="Description" value={item.description} />}
        <Row label="CLI" value={item.tool} />
        <Row label="Directory" value={item.cwd || "Unknown"} copy={item.cwd || undefined} />
        <Row label="Started" value={when(item.created_at)} />
        <Row label="Last used" value={when(item.last_active)} />
        <Row label="Session ID" value={item.session_id} copy={item.session_id} />
      </dl>
      <div className="details-actions" aria-label="Actions">
        {confirming ? (
          <>
            <p className="stop-note">Delete this session's transcript from {item.tool}? It can't be resumed afterwards.</p>
            <div className="stop-buttons">
              <button type="button" onClick={() => setConfirming(false)}>Keep</button>
              <button type="button" className="primary" disabled={busy} onClick={() => void run(() => removeHistory(item))}>Delete for good</button>
            </div>
          </>
        ) : (
          <>
            {atLimit && <p className="stop-note" role="status">{LIMIT_MESSAGE}</p>}
            {!item.cwd && <p className="stop-note">Its directory isn't known, so it can't be resumed.</p>}
            <div className="stop-buttons">
              <button type="button" className="primary" disabled={!resumable || busy} onClick={() => void run(() => resumeHistory(item))}>
                Resume
              </button>
              <button type="button" onClick={() => setConfirming(true)}>Delete</button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

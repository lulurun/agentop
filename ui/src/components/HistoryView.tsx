import { useEffect, useMemo, useState } from "react";
import type { SavedSession } from "../api/client";
import { historyKey, useApp } from "../store";
import { Row } from "./DetailsPanel";
import { ToolBadge } from "./Sidebar";

type Sort = "updated" | "tool";

function when(item: SavedSession): string {
  return item.last_active != null ? new Date(item.last_active * 1000).toLocaleString() : "Unknown";
}

function useSelectedItem(): SavedSession | undefined {
  const history = useApp((s) => s.history);
  return history.items?.find((i) => historyKey(i) === history.selected);
}

/** The History workspace's pinned tab: every past session on the service, to search, filter and sort. */
export function HistoryList() {
  const history = useApp((s) => s.history);
  const loadHistory = useApp((s) => s.loadHistory);
  const selectHistory = useApp((s) => s.selectHistory);
  const rightOpen = useApp((s) => s.rightOpen);
  const toggleRight = useApp((s) => s.toggleRight);
  const [search, setSearch] = useState("");
  const [tool, setTool] = useState("");
  const [sort, setSort] = useState<Sort>("updated");

  useEffect(() => {
    if (history.items === null) void loadHistory();
  }, [history.items, loadHistory]);

  const tools = useMemo(() => [...new Set((history.items ?? []).map((i) => i.tool))].sort(), [history.items]);
  const needle = search.trim().toLowerCase();
  const rows = useMemo(() => {
    const kept = (history.items ?? []).filter(
      (i) =>
        (!tool || i.tool === tool) &&
        (!needle || `${i.title ?? ""} ${i.tool} ${i.cwd ?? ""} ${i.session_id}`.toLowerCase().includes(needle)),
    );
    const newest = (a: SavedSession, b: SavedSession) => (b.last_active ?? 0) - (a.last_active ?? 0);
    return kept.sort(sort === "tool" ? (a, b) => a.tool.localeCompare(b.tool) || newest(a, b) : newest);
  }, [history.items, tool, needle, sort]);

  function pick(item: SavedSession) {
    selectHistory(historyKey(item));
    // On a narrow screen the details are an overlay, shut until asked for.
    if (!rightOpen && window.innerWidth < 900) toggleRight();
  }

  return (
    <section className="view history" aria-label="History">
      <div className="view-tools">
        <input type="search" aria-label="Search history" placeholder="Search titles, directories, IDs" value={search} onChange={(event) => setSearch(event.target.value)} />
        <select aria-label="CLI" value={tool} onChange={(event) => setTool(event.target.value)}>
          <option value="">All CLIs</option>
          {tools.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select aria-label="Sort by" value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
          <option value="updated">Last updated</option>
          <option value="tool">CLI</option>
        </select>
        <button type="button" onClick={() => void loadHistory()}>Refresh</button>
      </div>
      {history.items === null ? (
        <p className="view-empty">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="view-empty">{needle || tool ? "No past sessions match." : "No past sessions on this service."}</p>
      ) : (
        <>
          <p className="view-count">{rows.length === 1 ? "1 past session" : `${rows.length} past sessions`}</p>
          <ul className="view-list history-list" aria-label="Past sessions">
            {rows.map((item) => {
              const key = historyKey(item);
              return (
                <li key={key}>
                  <button type="button" className="history-row" aria-current={key === history.selected ? "true" : undefined} onClick={() => pick(item)}>
                    <span className="view-row-title">{item.title || item.session_id}</span>
                    <span className="view-row-meta">
                      <ToolBadge tool={item.tool} />
                      {item.cwd && <span className="history-cwd">{item.cwd}</span>}
                      <span>{when(item)}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

/** The History workspace's right panel: the selected past session, to resume or remove. */
export function HistoryDetails() {
  const item = useSelectedItem();
  const act = useApp((s) => s.act);
  const select = useApp((s) => s.select);
  const removeHistory = useApp((s) => s.removeHistory);
  const [confirming, setConfirming] = useState(false);
  const key = item && historyKey(item);
  useEffect(() => setConfirming(false), [key]);

  if (!item) return <aside className="details"><p className="detail-empty">Select a past session to see its details.</p></aside>;

  async function resume(item: SavedSession) {
    const created = await act((c) =>
      c.create({ tool: item.tool, cwd: item.cwd ?? "", name: null, resume: { session_id: item.session_id } }),
    );
    if (created) select(created.id);
  }

  return (
    <aside className="details" aria-label="Past session">
      <section>
        <h3>Details</h3>
        <dl>
          <Row label="Title" value={item.title || "Untitled"} />
          <Row label="Tool" value={item.tool} />
          <Row label="Directory" value={item.cwd || "Unknown"} copy={item.cwd || undefined} />
          <Row label="Session ID" value={item.session_id} copy={item.session_id} />
          <Row label="Updated" value={when(item)} />
        </dl>
      </section>
      <section className="details-actions" aria-label="Actions">
        {confirming ? (
          <>
            <p className="stop-note">Delete this session's transcript from {item.tool}? It can't be resumed afterwards.</p>
            <div className="stop-buttons">
              <button type="button" onClick={() => setConfirming(false)}>Keep</button>
              <button type="button" className="primary" onClick={() => void removeHistory(item)}>Delete for good</button>
            </div>
          </>
        ) : (
          <div className="stop-buttons">
            <button type="button" className="primary" disabled={!item.cwd} title={item.cwd ? undefined : "Its directory isn't known"} onClick={() => void resume(item)}>
              Resume
            </button>
            <button type="button" onClick={() => setConfirming(true)}>Delete</button>
          </div>
        )}
      </section>
    </aside>
  );
}

import { useCallback, useEffect, useState } from "react";
import type { SavedSession, ServiceClient } from "../api/client";
import { useApp } from "../store";

export function HistoryView({ client }: { client: ServiceClient }) {
  const act = useApp((s) => s.act);
  const select = useApp((s) => s.select);
  const [items, setItems] = useState<SavedSession[] | null>(null);
  const [filter, setFilter] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(() => act((c) => c.savedSessions()).then((list) => list && setItems(list)), [act]);
  useEffect(() => {
    void load();
  }, [load, client]);

  async function resume(item: SavedSession) {
    const created = await act((c) =>
      c.create({ tool: item.tool, cwd: item.cwd ?? "", name: null, resume: { session_id: item.session_id } }),
    );
    if (created) select(created.id);
  }

  async function remove(item: SavedSession) {
    await act((c) => c.deleteSavedSession(item.tool, item.session_id));
    setDeleting(null);
    await load();
  }

  const needle = filter.trim().toLowerCase();
  const rows = (items ?? []).filter(
    (i) => !needle || `${i.title ?? ""} ${i.tool} ${i.cwd ?? ""}`.toLowerCase().includes(needle),
  );

  return (
    <section className="view" aria-label="History">
      <h2>History</h2>
      <div className="view-tools">
        <input type="search" aria-label="Filter history" placeholder="Filter history" value={filter} onChange={(event) => setFilter(event.target.value)} />
      </div>
      {items === null ? (
        <p className="view-empty">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="view-empty">{needle ? "No past sessions match." : "No past sessions on this service."}</p>
      ) : (
        <ul className="view-list">
          {rows.map((item) => (
            <li key={`${item.tool}/${item.session_id}`} className="view-row">
              <div className="view-row-main">
                <div className="view-row-title">{item.title || item.session_id}</div>
                <div className="view-row-meta">
                  {item.tool}
                  {item.cwd && `, ${item.cwd}`}
                  {item.last_active != null && `, ${new Date(item.last_active * 1000).toLocaleString()}`}
                </div>
              </div>
              <div className="view-row-actions">
                {deleting === item.session_id ? (
                  <>
                    <button type="button" onClick={() => setDeleting(null)}>Keep</button>
                    <button type="button" className="primary" onClick={() => void remove(item)}>Delete for good</button>
                  </>
                ) : (
                  <>
                    <button type="button" disabled={!item.cwd} onClick={() => void resume(item)}>Resume</button>
                    <button type="button" onClick={() => setDeleting(item.session_id)}>Delete</button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

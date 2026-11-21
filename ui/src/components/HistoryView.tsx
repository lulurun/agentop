import { useEffect, useMemo, useState } from "react";
import type { HistoryConversation, HistoryItem, HistoryStats } from "../api/client";
import { activeCount, useApp } from "../store";
import { ChatView } from "./Conversation";
import { Row, TokenRows } from "./DetailsPanel";
import { FilesSection } from "./FilesSection";
import { formatBytes } from "./format";
import { Section } from "./Section";
import { ToolBadge } from "./Sidebar";

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
export function HistorySection({ selected, project = "default" }: { selected?: string; project?: string }) {
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

  const own = useMemo(() => history.items?.filter((i) => (i.project ?? "default") === project) ?? null, [history.items, project]);
  const tools = useMemo(() => [...new Set((own ?? []).map((i) => i.tool))].sort(), [own]);
  const needle = search.trim().toLowerCase();
  const rows = useMemo(
    () =>
      (own ?? [])
        .filter(
          (i) =>
            (!tool || i.tool === tool) &&
            (!needle || `${i.name} ${i.title ?? ""} ${i.description} ${i.cwd}`.toLowerCase().includes(needle)),
        )
        .sort((a, b) => b.last_active - a.last_active),
    [own, tool, needle],
  );

  function open(item: HistoryItem, mode: "replace" | "tab") {
    openTab({ kind: "past", historyId: item.id }, "left", mode);
  }

  return (
    <Section
      id="history"
      title="History"
      hint={history.windowSeconds ? `Sessions agentop started, used in ${days(history.windowSeconds)}` : undefined}
      extra={own && <span className="session-count">{own.length}</span>}
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
    </Section>
  );
}

/**
 * What a past session's transcript holds. Read when it is shown, and again
 * when the session was used since: the service reads the whole transcript.
 */
function TranscriptStats({ item }: { item: HistoryItem }) {
  const act = useApp((s) => s.act);
  const [stats, setStats] = useState<HistoryStats | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    setStats(undefined);
    void act((client) => client.historyStats(item.id)).then((found) => {
      if (live) setStats(found ?? null);
    });
    return () => {
      live = false;
    };
  }, [act, item.id, item.last_active]);

  if (stats === undefined) return <p className="detail-empty">Reading the transcript…</p>;
  if (stats === null) return <p className="detail-empty">The transcript couldn't be read.</p>;
  const files = `${stats.files}${stats.truncated ? "+" : ""} ${stats.files === 1 ? "file" : "files"}`;
  return (
    <>
      <dl>
        <Row label="Prompts" value={String(stats.prompts)} />
        <Row label="Replies" value={String(stats.replies)} />
        <Row label="Size" value={`${formatBytes(stats.bytes)} in ${files}${stats.archived ? ", from the Memory archive" : ""}`} />
      </dl>
      <h3>Tokens</h3>
      {stats.token_usage ? (
        <dl>
          <TokenRows usage={stats.token_usage} />
        </dl>
      ) : (
        <p className="detail-empty">Not recorded by {item.tool}.</p>
      )}
    </>
  );
}

/** The right panel while a past session's tab has focus: what it was, its transcript, its files. */
export function PastDetails({ historyId }: { historyId: string }) {
  const item = useItem(historyId);
  if (!item) {
    return (
      <Section id="details" title="Details">
        <p className="detail-empty">This session is no longer in History.</p>
      </Section>
    );
  }
  return (
    <>
      <Section id="details" title="Details">
        <dl>
          <Row label="Name" value={item.name} />
          <Row label="Title" value={item.title || "Untitled"} />
          {item.description && <Row label="Description" value={item.description} />}
          <Row label="Tool" value={item.tool} />
          <Row label="Directory" value={item.cwd || "Unknown"} copy={item.cwd || undefined} />
          <Row label="Started" value={when(item.created_at)} />
          <Row label="Last used" value={when(item.last_active)} />
          <Row label="Session ID" value={item.session_id} copy={item.session_id} />
        </dl>
      </Section>
      <Section id="transcript" title="Transcript">
        <TranscriptStats item={item} />
      </Section>
      {item.cwd && <FilesSection key={`files/${item.id}`} sessionId={item.id} tool={item.tool} />}
    </>
  );
}

/** A past session's conversation, read again when the session was used since. */
function useConversation(item: HistoryItem | undefined, reloads: number): HistoryConversation | null | undefined {
  const act = useApp((s) => s.act);
  const [chat, setChat] = useState<HistoryConversation | null | undefined>(undefined);
  const id = item?.id;
  const stamp = item?.last_active;
  useEffect(() => {
    if (!id) return;
    let live = true;
    void act((client) => client.historyConversation(id)).then((found) => {
      if (live) setChat(found ?? null);
    });
    return () => {
      live = false;
    };
  }, [act, id, stamp, reloads]);
  return chat;
}

/** A past session in the main area: its conversation, with Resume and Delete above it. */
export function PastSessionView({ historyId, onClose }: { historyId: string; onClose(): void }) {
  const item = useItem(historyId);
  const loaded = useApp((s) => s.history.items !== null);
  const resumeHistory = useApp((s) => s.resumeHistory);
  const removeHistory = useApp((s) => s.removeHistory);
  const { atLimit, max } = useLimit();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reloads, setReloads] = useState(0);
  const chat = useConversation(item, reloads);
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
  const why = !item.cwd ? "Its directory isn't known, so it can't be resumed" : atLimit ? `${LIMIT_MESSAGE} (at most ${max})` : "Resume this session in a new terminal";
  return (
    <section className="viewer past-session" aria-label={`Past session ${item.name}`}>
      <div className="viewer-bar">
        <span className="viewer-path" title={item.cwd}>
          <span className="viewer-source">Past session</span>
          {item.name}
        </span>
        {chat && <span className="viewer-meta">{chat.total} {chat.total === 1 ? "message" : "messages"}</span>}
        {confirming ? (
          <>
            <span className="viewer-confirm">Delete its transcript from {item.tool}? It can't be resumed afterwards.</span>
            <button type="button" onClick={() => setConfirming(false)}>Keep</button>
            <button type="button" className="primary" disabled={busy} onClick={() => void run(() => removeHistory(item))}>Delete for good</button>
          </>
        ) : (
          <>
            <button type="button" className="primary" title={why} disabled={!resumable || busy} onClick={() => void run(() => resumeHistory(item))}>
              Resume
            </button>
            <button type="button" onClick={() => setConfirming(true)}>Delete</button>
            <button type="button" onClick={() => setReloads((n) => n + 1)}>Reload</button>
          </>
        )}
      </div>
      {atLimit && !confirming && <p className="viewer-note" role="status">{LIMIT_MESSAGE}</p>}
      {chat === undefined ? null : chat === null ? (
        <div className="placard">
          <h2>Can't read this conversation</h2>
          <p>The transcript couldn't be read.</p>
        </div>
      ) : (
        <ChatView messages={chat.messages} total={chat.total} label={`Conversation in ${item.name}`} />
      )}
    </section>
  );
}

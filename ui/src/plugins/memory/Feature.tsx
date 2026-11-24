/**
 * The Memory feature (docs/0_12_03_memory.md, section 6): a search tab of recent sessions and
 * results, a conversation beside it opened at a hit, and its session's summary.
 */
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { platform } from "../../platform";
import type { PluginContext } from "../types";
import { Section } from "../../components/Section";
import {
  keyOf,
  loadRecent,
  openConversation,
  runSearch,
  SINCE_DAYS,
  splitKey,
  useClientState,
  useCurrentConversation,
  useMemory,
  type Message,
  type MessageHit,
  type Miss,
  type RecentSession,
  type Scope,
  type SessionHit,
  type SessionInfo,
} from "./state";

const TOOLS = ["claude", "codex", "antigravity"];
const SINCE_LABELS: Record<number, string> = {
  0: "Any time",
  7: "Last week",
  30: "Last month",
  90: "Last 3 months",
  365: "Last year",
};

function when(at: number | null): string {
  return at ? new Date(at * 1000).toLocaleString() : "";
}

function day(at: number | null): string {
  return at ? new Date(at * 1000).toLocaleDateString() : "";
}

function shortPath(path: string | null): string {
  if (!path) return "";
  return path.replace(/^\/home\/[^/]+/, "~").replace(/^\/Users\/[^/]+/, "~");
}

function message(failure: unknown): string {
  return failure instanceof Error ? failure.message : String(failure);
}

function escape(term: string): string {
  return term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Text with the search words marked, ignoring case, as the service matches them. */
function Marked({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return <>{text}</>;
  const pattern = new RegExp(
    `(${terms
      .map(escape)
      .sort((a, b) => b.length - a.length)
      .join("|")})`,
    "gi",
  );
  const parts = text.split(pattern);
  return <>{parts.map((part, i) => (i % 2 === 1 ? <mark key={i}>{part}</mark> : part))}</>;
}

const URL_PATTERN = /https?:\/\/[^\s<>"'`)\]]+/g;

/** A message's text with its links made clickable and the search words marked. */
function Rich({ text, terms }: { text: string; terms: string[] }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const url = match[0].replace(/[.,;:!?]+$/, "");
    const start = match.index ?? 0;
    parts.push(<Marked key={`t${start}`} text={text.slice(last, start)} terms={terms} />);
    parts.push(
      <a key={start} href={url} target="_blank" rel="noreferrer noopener">
        <Marked text={url} terms={terms} />
      </a>,
    );
    last = start + url.length;
  }
  parts.push(<Marked key="end" text={text.slice(last)} terms={terms} />);
  return <>{parts}</>;
}

function ToolBadge({ tool }: { tool: string }) {
  return (
    <span className="tool-badge" data-tool={TOOLS.includes(tool) ? tool : undefined}>
      {tool}
    </span>
  );
}

// ------------------------------------------------------------------ the search tab

/** Opens the conversation beside the search; Ctrl or Cmd-click in a tab of its own. */
function open(tool: string, session: string, at: number | undefined, event: { ctrlKey: boolean; metaKey: boolean }) {
  openConversation(tool, session, at, event.ctrlKey || event.metaKey);
}

function SessionResult({ hit, terms, current }: { hit: RecentSession | SessionHit; terms: string[]; current: boolean }) {
  const found = "mentions" in hit ? hit : null;
  const text = found?.snippet || hit.summary || "";
  return (
    <li>
      <button type="button" className="memory-result" aria-current={current || undefined} onClick={(event) => open(hit.tool, hit.session, undefined, event)}>
        <span className="memory-result-meta">
          <ToolBadge tool={hit.tool} />
          {hit.project && <span className="memory-hit-project">{shortPath(hit.project)}</span>}
          <span>{when(hit.last_at)}</span>
          <span>{hit.messages.toLocaleString()} messages</span>
        </span>
        <span className="memory-result-title">
          <Marked text={hit.title || hit.prompt || `${hit.tool} session ${hit.session.slice(0, 8)}`} terms={terms} />
        </span>
        {text && (
          <span className="memory-hit-text">
            <Marked text={text} terms={terms} />
          </span>
        )}
        {found && (found.matched.length > 0 || found.mentions > 0) && (
          <span className="memory-hit-keywords">
            {found.mentions > 0 && <span className="memory-mentions">{found.mentions === 1 ? "1 mention" : `${found.mentions} mentions`}</span>}
            {found.matched.slice(0, 6).map((k) => (
              <span key={k} className="memory-keyword">
                {k}
              </span>
            ))}
          </span>
        )}
      </button>
    </li>
  );
}

function MentionResult({ hit, terms, current }: { hit: MessageHit; terms: string[]; current: boolean }) {
  const title = useMemory((s) => s.titles[keyOf(hit.tool, hit.session)]);
  return (
    <li>
      <button
        type="button"
        className="memory-result"
        data-role={hit.role}
        aria-current={current || undefined}
        onClick={(event) => open(hit.tool, hit.session, hit.id, event)}
      >
        <span className="memory-result-meta">
          <ToolBadge tool={hit.tool} />
          {hit.project && <span className="memory-hit-project">{shortPath(hit.project)}</span>}
          <span>{when(hit.at)}</span>
        </span>
        <span className="memory-result-session">{title || `${hit.tool} session ${hit.session.slice(0, 8)}`}</span>
        {hit.prompt && (
          <span className="memory-hit-prompt">
            You asked: <Marked text={hit.prompt} terms={terms} />
          </span>
        )}
        <span className="memory-hit-text">
          <strong>{hit.role === "user" ? "You" : "Agent"}:</strong> <Marked text={hit.snippet} terms={terms} />
        </span>
      </button>
    </li>
  );
}

/** Note that the search didn't find what was wanted, for deciding on embeddings (docs/0_12_03_memory.md, section 9). */
function MissForm({ plugin }: { plugin: PluginContext }) {
  const query = useMemory((s) => s.terms.join(" "));
  const [open, setOpen] = useState(false);
  const [lookedFor, setLookedFor] = useState("");
  const [kind, setKind] = useState<Miss["kind"]>("nothing");
  const [busy, setBusy] = useState(false);
  const [noted, setNoted] = useState<Miss | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setNoted(null);
    setOpen(false);
  }, [query]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setNoted(
        await plugin.client.post<Miss>("/misses", {
          looked_for: lookedFor,
          kind,
        }),
      );
      setOpen(false);
      setLookedFor("");
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }

  if (noted) {
    return (
      <p className="memory-miss-note" role="status">
        Noted. {noted.tried.length > 0 && `With the searches for ${noted.tried.map((t) => `“${t}”`).join(", ")}.`}
      </p>
    );
  }
  if (!open) {
    return (
      <button type="button" className="memory-miss-open" onClick={() => setOpen(true)}>
        Didn't find it?
      </button>
    );
  }
  return (
    <form className="memory-miss" onSubmit={(event) => void submit(event)} aria-label="Note a miss">
      <label>
        What were you looking for?
        <input
          type="text"
          value={lookedFor}
          required
          autoFocus
          placeholder="The sushi place a friend recommended"
          onChange={(event) => setLookedFor(event.target.value)}
        />
      </label>
      <label className="memory-miss-kind">
        <input type="radio" name="kind" checked={kind === "nothing"} onChange={() => setKind("nothing")} />
        Nothing that fits came up
      </label>
      <label className="memory-miss-kind">
        <input type="radio" name="kind" checked={kind === "wrong"} onChange={() => setKind("wrong")} />
        It found the wrong thing
      </label>
      <p className="memory-miss-hint">The searches you just made are noted with it. Misses decide whether keyword search needs help from embeddings.</p>
      {error && <p className="form-error">{error}</p>}
      <div className="memory-miss-actions">
        <button type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button type="submit" className="primary" disabled={busy || !lookedFor.trim()}>
          Note the miss
        </button>
      </div>
    </form>
  );
}

/** Sessions shown first under All, as a web search shows a few images above the pages. */
const ALL_SESSIONS = 3;

function Results({ plugin }: { plugin: PluginContext }) {
  const { results, terms, scope, searching } = useMemory();
  const current = useCurrentConversation();
  const currentKey = current ? keyOf(...current) : null;
  const target = useMemory((s) => (currentKey ? s.targets[currentKey] : undefined));
  if (!results) return null;
  const { sessions, messages } = results;
  const isCurrent = (tool: string, session: string, at?: number) => keyOf(tool, session) === currentKey && target === at;
  const sessionList = (shown: SessionHit[]) => (
    <ul className="memory-results-list" aria-label="Sessions">
      {shown.map((hit) => (
        <SessionResult key={keyOf(hit.tool, hit.session)} hit={hit} terms={terms} current={isCurrent(hit.tool, hit.session)} />
      ))}
    </ul>
  );
  const mentionList = (
    <ul className="memory-results-list" aria-label="Mentions">
      {messages.map((hit) => (
        <MentionResult key={hit.id} hit={hit} terms={terms} current={isCurrent(hit.tool, hit.session, hit.id)} />
      ))}
    </ul>
  );
  const nothing = sessions.length === 0 && messages.length === 0;
  return (
    <div className="memory-results" aria-busy={searching || undefined}>
      {nothing ? (
        <p className="memory-empty">Nothing has all of: {terms.join(" ")}. Try part of a word, another spelling, or another language.</p>
      ) : scope === "sessions" ? (
        sessions.length ? sessionList(sessions) : <p className="memory-empty">No session matches.</p>
      ) : scope === "mentions" ? (
        messages.length ? mentionList : <p className="memory-empty">No message has all of: {terms.join(" ")}</p>
      ) : (
        <>
          {sessions.length > 0 && (
            <div className="memory-group">
              <h3 className="memory-group-title">Sessions</h3>
              {sessionList(sessions.slice(0, ALL_SESSIONS))}
              {sessions.length > ALL_SESSIONS && (
                <button type="button" className="memory-more" onClick={() => useMemory.setState({ scope: "sessions" })}>
                  All {sessions.length} sessions
                </button>
              )}
            </div>
          )}
          {messages.length > 0 && mentionList}
        </>
      )}
      <div className="memory-miss-row">
        <MissForm plugin={plugin} />
      </div>
    </div>
  );
}

function Recent({ plugin }: { plugin: PluginContext }) {
  const recent = useMemory((s) => s.recent);
  const more = useMemory((s) => s.recentMore);
  const current = useCurrentConversation();
  const currentKey = current ? keyOf(...current) : null;
  const target = useMemory((s) => (currentKey ? s.targets[currentKey] : undefined));
  if (!recent) return <p className="memory-empty">Loading…</p>;
  if (!recent.length) return <p className="memory-empty">No conversations recorded yet. They come in with each sweep, every 5 minutes.</p>;
  return (
    <div className="memory-results">
      <ul className="memory-results-list" aria-label="Recent sessions">
        {recent.map((hit) => (
          <SessionResult
            key={keyOf(hit.tool, hit.session)}
            hit={hit}
            terms={[]}
            current={keyOf(hit.tool, hit.session) === currentKey && target === undefined}
          />
        ))}
      </ul>
      {more && (
        <button type="button" className="memory-more" onClick={() => void loadRecent(plugin.client, true)}>
          More sessions
        </button>
      )}
    </div>
  );
}

const SCOPES: [Scope, string][] = [
  ["all", "All"],
  ["sessions", "Sessions"],
  ["mentions", "Mentions"],
];

/** The search tab, pinned first in the left area: the box, then recent sessions, or the results by scope. */
export function SearchView({ plugin }: { plugin: PluginContext }) {
  useClientState(plugin.client);
  const { query, tool, sinceDays, results, scope, searching, error } = useMemory();
  const sessions = useMemory((s) => s.recent?.length ?? 0);

  useEffect(() => {
    void loadRecent(plugin.client);
    // A sweep brings in new messages, and a session's summary after it.
    return plugin.onEvent(() => void loadRecent(plugin.client));
  }, [plugin]);

  function submit(event: FormEvent) {
    event.preventDefault();
    useMemory.setState({ scope: "all" });
    void runSearch(plugin.client);
  }

  function setFilter(change: Partial<{ tool: string; sinceDays: number }>) {
    useMemory.setState(change);
    void loadRecent(plugin.client);
    if (useMemory.getState().results) void runSearch(plugin.client);
  }

  function clear() {
    useMemory.setState({ query: "", results: null, terms: [], error: null });
  }

  const counts: Record<Scope, number | undefined> = {
    all: undefined,
    sessions: results?.sessions.length,
    mentions: results?.messages.length,
  };
  return (
    <div className="memory-search">
      <div className="memory-search-inner">
        <form className="memory-search-form" role="search" onSubmit={submit}>
          <input
            type="search"
            aria-label="Search past conversations"
            placeholder="Words, part of a name, a link…"
            value={query}
            autoFocus
            onChange={(event) => {
              useMemory.setState({ query: event.target.value });
              if (!event.target.value) clear();
            }}
          />
          <button type="submit" className="primary" disabled={searching || !query.trim()}>
            Search
          </button>
        </form>
        <div className="memory-search-bar">
          {results ? (
            <div className="memory-scopes" role="tablist" aria-label="Results">
              {SCOPES.map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  className="memory-scope"
                  aria-selected={scope === id}
                  onClick={() => useMemory.setState({ scope: id })}
                >
                  {label}
                  {counts[id] !== undefined && <span className="memory-scope-count">{counts[id]}</span>}
                </button>
              ))}
            </div>
          ) : (
            <h2 className="memory-recent-title">Recent sessions{sessions > 0 && <span className="memory-scope-count">{sessions}</span>}</h2>
          )}
          <div className="memory-filters">
            <select aria-label="CLI" value={tool} onChange={(event) => setFilter({ tool: event.target.value })}>
              <option value="">All CLIs</option>
              {TOOLS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select aria-label="Since" value={sinceDays} onChange={(event) => setFilter({ sinceDays: Number(event.target.value) })}>
              {SINCE_DAYS.map((d) => (
                <option key={d} value={d}>
                  {SINCE_LABELS[d]}
                </option>
              ))}
            </select>
          </div>
        </div>
        {error && <p className="form-error">{error}</p>}
        {results ? <Results plugin={plugin} /> : <Recent plugin={plugin} />}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ the conversations

export function ConversationView({ plugin, viewKey = "" }: { plugin: PluginContext; viewKey?: string }) {
  const [tool, session] = splitKey(viewKey);
  const target = useMemory((s) => s.targets[viewKey]);
  const terms = useMemory((s) => s.terms);
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [info, setInfo] = useState<SessionInfo | null>(null);
  const [error, setError] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    setMessages(null);
    setError("");
    const params = new URLSearchParams({ session });
    if (tool) params.set("tool", tool);
    plugin.client.get<Message[]>(`/conversation?${params}`).then(
      (found) => live && setMessages(found),
      (failure: unknown) => live && setError(message(failure)),
    );
    plugin.client.get<SessionInfo | null>(`/session?${params}`).then(
      (found) => live && setInfo(found),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [plugin.client, tool, session]);

  // Open at the hit, in the middle of the view; a session found by its summary opens at its start.
  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element || !messages) return;
    const hit = target === undefined ? null : element.querySelector<HTMLElement>(`[data-id="${target}"]`);
    // Only the conversation scrolls: scrollIntoView would move the page around it too.
    if (hit) {
      const offset = hit.getBoundingClientRect().top - element.getBoundingClientRect().top + element.scrollTop;
      element.scrollTop = Math.max(0, offset - (element.clientHeight - hit.offsetHeight) / 2);
    } else element.scrollTop = 0;
  }, [messages, target]);

  const known = useMemory((s) => s.titles[viewKey]);
  const title = info?.summary?.title || known || `${tool} session ${session.slice(0, 8)}`;
  let body: ReactNode;
  if (error) {
    body = (
      <div className="placard">
        <h2>Can't show this conversation</h2>
        <p>{error}</p>
      </div>
    );
  } else if (!messages) {
    body = <p className="view-empty memory-pad">Loading…</p>;
  } else if (messages.length === 0) {
    body = (
      <div className="placard">
        <h2>No messages recorded</h2>
        <p>Nothing of this session is in the record.</p>
      </div>
    );
  } else {
    body = (
      <div className="conversation" ref={scroller} tabIndex={0} aria-label={`Conversation: ${title}`}>
        <ol>
          {messages.map((m) => (
            <li key={m.id} className="message" data-role={m.role} data-id={m.id} data-hit={m.id === target || undefined}>
              <div className="message-head">
                <strong>{m.role === "user" ? "You" : "Agent"}</strong>
                {m.at !== null && <time dateTime={new Date(m.at * 1000).toISOString()}>{when(m.at)}</time>}
                <span className="memory-message-id">#{m.id}</span>
              </div>
              <div className="message-text">
                <Rich text={m.text} terms={terms} />
              </div>
            </li>
          ))}
        </ol>
      </div>
    );
  }
  return (
    <div className="viewer memory-conversation">
      <div className="viewer-bar">
        <ToolBadge tool={tool} />
        <strong className="viewer-path" title={title}>
          {title}
        </strong>
        {messages && <span className="viewer-meta">{messages.length} messages</span>}
      </div>
      {body}
    </div>
  );
}

export function tabLabel(viewKey: string): string {
  const title = useMemory.getState().titles[viewKey];
  if (title) return title;
  const [tool, session] = splitKey(viewKey);
  return `${tool} ${session.slice(0, 8)}`;
}

// ------------------------------------------------------------------ the right panel

function Row({ label, children, copy }: { label: string; children: ReactNode; copy?: string }) {
  return (
    <div className="detail-row">
      <dt>{label}</dt>
      <dd>
        {copy ? (
          <button type="button" className="copyable" title="Copy" onClick={() => void platform.copyText(copy)}>
            {children}
          </button>
        ) : (
          children
        )}
      </dd>
    </div>
  );
}

function SessionDetails({ plugin, tool, session }: { plugin: PluginContext; tool: string; session: string }) {
  const [info, setInfo] = useState<SessionInfo | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    setInfo(undefined);
    const params = new URLSearchParams({ session });
    if (tool) params.set("tool", tool);
    plugin.client.get<SessionInfo | null>(`/session?${params}`).then(
      (found) => {
        if (!live) return;
        setInfo(found);
        if (found?.summary)
          useMemory.setState((s) => ({
            titles: {
              ...s.titles,
              [keyOf(tool, session)]: found.summary!.title,
            },
          }));
      },
      () => live && setInfo(null),
    );
    return () => {
      live = false;
    };
  }, [plugin.client, tool, session]);

  if (info === undefined) return null;
  if (info === null) {
    return (
      <Section id="memory:session" title="Session">
        <p className="detail-empty">Nothing of this session is recorded.</p>
      </Section>
    );
  }
  const summary = info.summary;
  return (
    <div className="memory-details">
      <Section id="memory:summary" title="Summary">
      {summary ? (
        <>
          <p className="memory-summary-title">{summary.title}</p>
          <p className="memory-summary">{summary.summary}</p>
          {summary.keywords.length > 0 && (
            <p className="memory-keywords" aria-label="Keywords">
              {summary.keywords.map((k) => (
                <span key={k} className="memory-keyword">
                  {k}
                </span>
              ))}
            </p>
          )}
          <p className="memory-made">
            Written by {summary.made_by} on {day(summary.made_at)}, from {summary.messages} messages. A summary is a paraphrase: quote the messages.
          </p>
        </>
      ) : (
        <p className="detail-empty">Not summarized yet. A session gets a summary once it has been quiet for 30 minutes.</p>
      )}
      </Section>
      <Section id="memory:session" title="Session">
      <dl>
        <Row label="Tool">{info.tool}</Row>
        {info.project && (
          <Row label="Directory" copy={info.project}>
            {shortPath(info.project)}
          </Row>
        )}
        <Row label="Session ID" copy={info.session}>
          {info.session}
        </Row>
        <Row label="First">{when(info.first_at)}</Row>
        <Row label="Last">{when(info.last_at)}</Row>
        <Row label="Messages">{info.messages.toLocaleString()}</Row>
      </dl>
      </Section>
    </div>
  );
}

export function DetailsPanel({ plugin }: { plugin: PluginContext }) {
  const current = useCurrentConversation();
  if (!current) {
    return (
      <Section id="memory:session" title="Session">
        <p className="detail-empty">Open a session to see its summary and details.</p>
      </Section>
    );
  }
  const [tool, session] = current;
  return <SessionDetails key={keyOf(tool, session)} plugin={plugin} tool={tool} session={session} />;
}

/**
 * The Memory feature (design 12.11.3, phase 6): search every past conversation on the
 * service, read the one around a hit, and see its session's summary. A
 * search that didn't find what was wanted can be noted as a miss.
 */
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { platform } from "../../platform";
import type { PluginContext } from "../types";
import { Section } from "../../components/Section";
import {
  keyOf,
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

// ------------------------------------------------------------------ the left panel

function SessionRow({ hit, terms, current }: { hit: SessionHit; terms: string[]; current: boolean }) {
  return (
    <li>
      <button
        type="button"
        className="memory-hit"
        aria-current={current || undefined}
        onClick={(event) => openConversation(hit.tool, hit.session, undefined, event.ctrlKey || event.metaKey)}
      >
        <span className="memory-hit-title">
          <Marked text={hit.title} terms={terms} />
        </span>
        <span className="memory-hit-text">
          <Marked text={hit.snippet} terms={terms} />
        </span>
        {hit.matched.length > 0 && (
          <span className="memory-hit-keywords">
            {hit.matched.slice(0, 6).map((k) => (
              <span key={k} className="memory-keyword">
                {k}
              </span>
            ))}
          </span>
        )}
        <span className="memory-hit-meta">
          <ToolBadge tool={hit.tool} />
          <span>{day(hit.last_at)}</span>
          {hit.project && <span className="memory-hit-project">{shortPath(hit.project)}</span>}
        </span>
      </button>
    </li>
  );
}

function MessageRow({ hit, terms, current }: { hit: MessageHit; terms: string[]; current: boolean }) {
  return (
    <li>
      <button
        type="button"
        className="memory-hit"
        data-role={hit.role}
        aria-current={current || undefined}
        onClick={(event) => openConversation(hit.tool, hit.session, hit.id, event.ctrlKey || event.metaKey)}
      >
        {hit.prompt && (
          <span className="memory-hit-prompt">
            You asked: <Marked text={hit.prompt} terms={terms} />
          </span>
        )}
        <span className="memory-hit-text">
          <strong>{hit.role === "user" ? "You" : "Agent"}:</strong> <Marked text={hit.snippet} terms={terms} />
        </span>
        <span className="memory-hit-meta">
          <ToolBadge tool={hit.tool} />
          <span>{when(hit.at)}</span>
          {hit.project && <span className="memory-hit-project">{shortPath(hit.project)}</span>}
        </span>
      </button>
    </li>
  );
}

/** Note that the search didn't find what was wanted, for deciding on embeddings (design 12.11.4). */
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

export function SearchPanel({ plugin }: { plugin: PluginContext }) {
  useClientState(plugin.client);
  const { query, tool, sinceDays, terms, results, searching, error } = useMemory();
  const current = useCurrentConversation();
  const currentKey = current ? keyOf(...current) : null;
  const target = useMemory((s) => (currentKey ? s.targets[currentKey] : undefined));

  function submit(event: FormEvent) {
    event.preventDefault();
    void runSearch(plugin.client);
  }

  function setFilter(change: Partial<{ tool: string; sinceDays: number }>) {
    useMemory.setState(change);
    if (useMemory.getState().results) void runSearch(plugin.client);
  }

  return (
    <>
      <Section id="memory:search" title="Search">
      <form className="memory-search-form" role="search" onSubmit={submit}>
        <div className="memory-search-row">
          <input
            type="search"
            aria-label="Search past conversations"
            placeholder="Words, part of a name, a link…"
            value={query}
            autoFocus
            onChange={(event) => useMemory.setState({ query: event.target.value })}
          />
          <button type="submit" className="primary" disabled={searching || !query.trim()}>
            Search
          </button>
        </div>
        <div className="memory-search-row">
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
      </form>
        {error && <p className="form-error memory-pad">{error}</p>}
        {!results && <p className="session-empty">Every word must match, anywhere in a message, ignoring case. Quotes keep a phrase together.</p>}
      </Section>
        {results && (
          <>
            <Section id="memory:sessions" title="Sessions about this" extra={<span className="session-count">{results.sessions.length}</span>}>
            {results.sessions.length === 0 ? (
              <p className="session-empty">No session summary matches.</p>
            ) : (
              <ul className="memory-hits" aria-label="Sessions">
                {results.sessions.map((hit) => (
                  <SessionRow
                    key={keyOf(hit.tool, hit.session)}
                    hit={hit}
                    terms={terms}
                    current={keyOf(hit.tool, hit.session) === currentKey && target === undefined}
                  />
                ))}
              </ul>
            )}
            </Section>
            <Section id="memory:messages" title="Messages" extra={<span className="session-count">{results.messages.length}</span>}>
            {results.messages.length === 0 ? (
              <p className="session-empty">No message has all of: {terms.join(" ")}</p>
            ) : (
              <ul className="memory-hits" aria-label="Messages">
                {results.messages.map((hit) => (
                  <MessageRow key={hit.id} hit={hit} terms={terms} current={keyOf(hit.tool, hit.session) === currentKey && target === hit.id} />
                ))}
              </ul>
            )}
            <div className="memory-pad">
              <MissForm plugin={plugin} />
            </div>
            </Section>
          </>
        )}
    </>
  );
}

// ------------------------------------------------------------------ the main area

interface Counts {
  messages: { messages: number; sessions: number };
  summaries: { summaries: number };
  recall: { searches: number; misses: number };
}

export function Background({ plugin }: { plugin: PluginContext }) {
  const [counts, setCounts] = useState<Counts | null>(null);
  useEffect(() => {
    let live = true;
    plugin.client.get<Counts>("/status").then(
      (found) => live && setCounts(found),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [plugin.client]);
  return (
    <div className="placard">
      <h2>Search past conversations</h2>
      <p>
        Find a name, a place or a link from any conversation with an agent on this service, then read the conversation around it.
        {counts &&
          ` ${counts.messages.messages.toLocaleString()} prompts and replies from ${counts.messages.sessions.toLocaleString()} sessions are kept, ${counts.summaries.summaries.toLocaleString()} of them summarized.`}
      </p>
    </div>
  );
}

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

  const title = info?.summary?.title || `${tool} session ${session.slice(0, 8)}`;
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
        <p className="detail-empty">Open a search result to see its session.</p>
      </Section>
    );
  }
  const [tool, session] = current;
  return <SessionDetails key={keyOf(tool, session)} plugin={plugin} tool={tool} session={session} />;
}

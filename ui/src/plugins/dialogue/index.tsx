import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { activeClient, useApp } from "../../store";
import { formatBytes } from "../../components/format";
import { TerminalView } from "../../terminal/TerminalView";
import type { PluginClient, PluginContext, UiPlugin } from "../types";
import { NewDialogue } from "./NewDialogue";
import {
  dialoguePath,
  forgetDialogue,
  openFile,
  openWizard,
  refreshDialogues,
  selectDialogue,
  splitKey,
  useCurrentDialogue,
  useDialogue,
  useDialogueList,
  useDialogues,
  type DialogueDetail,
  type DialogueFile,
  type DialogueSummary,
  type FileText,
  type Side,
  type Turn,
} from "./state";

const DETAIL_POLL_MS = 5_000;

// Its libraries load the first time a message or a Markdown file is drawn.
const Text = lazy(() => import("./Text"));

const STATUS_LABELS: Record<string, string> = {
  starting: "Starting",
  running: "Running",
  completed: "Completed",
  stopped: "Stopped",
  error: "Failed",
  agent_missing_delimiter: "Failed: an agent kept missing the reply format",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/** The mark beside a dialogue, in the colours the session list uses. */
function markOf(d: DialogueSummary): string {
  if (d.status === "running") return "running";
  if (d.status === "starting") return "starting";
  if (d.status === "completed" || d.status === "stopped") return "exited";
  return "failed";
}

function when(stamp: string | null): string {
  return stamp ? new Date(stamp).toLocaleString() : "";
}

function duration(seconds: number | null): string {
  if (seconds == null) return "";
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return `${Math.round(seconds)}s`;
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

const sideName = (d: DialogueSummary, side: Side) => (side === "a" ? d.name_a : d.name_b);
const sideTool = (d: DialogueSummary, side: Side) => (side === "a" ? d.agent_a : d.agent_b);
const sideSession = (d: DialogueSummary, side: Side) => (side === "a" ? d.session_a : d.session_b);
const sideAlive = (d: DialogueSummary, side: Side) => (side === "a" ? d.session_a_alive : d.session_b_alive);

// ------------------------------------------------------------------ the left panel

function DialogueRow({ d, current }: { d: DialogueSummary; current: boolean }) {
  return (
    <button type="button" className="session-row dialogue-row" data-status={markOf(d)} aria-current={current || undefined} onClick={() => selectDialogue(d.id)} title={d.id}>
      <span className={`mark mark-${markOf(d)}`} aria-hidden="true" />
      <span className="session-name">{d.title}</span>
      <span className="session-meta">
        <span>{d.scenario}</span>
        <span className="session-summary">{d.running ? statusLabel(d.status) : when(d.end_time ?? d.start_time)}</span>
      </span>
    </button>
  );
}

function DialogueList({ plugin }: { plugin: PluginContext }) {
  const items = useDialogueList(plugin.client);
  const current = useCurrentDialogue();
  if (items === null) return <p className="session-empty">Loading…</p>;
  const running = items.filter((d) => d.running);
  const finished = items.filter((d) => !d.running);
  return (
    <nav className="session-list" aria-label="Dialogues">
      <h3 className="session-group">In progress</h3>
      {running.length === 0 && <p className="session-empty">None running.</p>}
      {running.map((d) => (
        <DialogueRow key={d.id} d={d} current={d.id === current} />
      ))}
      <h3 className="session-group">Finished</h3>
      {finished.length === 0 && <p className="session-empty">None yet.</p>}
      {finished.map((d) => (
        <DialogueRow key={d.id} d={d} current={d.id === current} />
      ))}
    </nav>
  );
}

/** What the workspace shows with no tabs open, as the Sessions workspace does. */
function EmptyView() {
  return (
    <div className="placard">
      <h2>Create a new dialogue, or pick one from the list</h2>
      <div className="placard-actions">
        <button type="button" className="primary" onClick={openWizard}>
          New dialogue
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ an agent's tab

function Messages({ turns, d, side }: { turns: Turn[]; d: DialogueSummary; side: Side }) {
  const mine = turns.filter((t) => t.side === side);
  if (mine.length === 0) return <p className="view-empty dialogue-none">{sideName(d, side)} sent no messages.</p>;
  return (
    <ol className="dialogue-messages">
      {mine.map((t) => (
        <li key={`${t.turn}-${t.at}`} className="dialogue-message" data-side={side} data-status={t.status}>
          <div className="message-head">
            <strong>Turn {t.turn}</strong>
            {t.at && <time dateTime={t.at}>{new Date(t.at).toLocaleString()}</time>}
            {t.status === "complete" && <span className="dialogue-done">Marked the dialogue complete</span>}
          </div>
          <Suspense fallback={<pre className="message-text">{t.body}</pre>}>
            <Text text={t.body} className="markdown dialogue-body" />
          </Suspense>
        </li>
      ))}
    </ol>
  );
}

function Transcript({ plugin, d, side }: { plugin: PluginContext; d: DialogueSummary; side: Side }) {
  const [turns, setTurns] = useState<Turn[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    plugin.client.get<Turn[]>(`${dialoguePath(d.id)}/turns`).then(
      (found) => live && setTurns(found),
      (err: unknown) => live && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      live = false;
    };
  }, [plugin.client, d.id, d.status]);
  if (error) return <p className="form-error dialogue-none">{error}</p>;
  if (turns === null) return <p className="view-empty dialogue-none">Loading…</p>;
  return (
    <div className="conversation">
      <Messages turns={turns} d={d} side={side} />
    </div>
  );
}

function LiveTerminal({ session }: { session: string }) {
  const connections = useApp((s) => s.connections);
  const activeId = useApp((s) => s.activeId);
  const settings = useApp((s) => s.settings);
  const client = useMemo(() => activeClient({ connections, activeId }), [connections, activeId]);
  return (
    <TerminalView
      key={`${activeId}/${session}/${settings.theme}/${settings.renderer}/${settings.fontSize}`}
      client={client}
      sessionId={session}
      focusRequest={0}
      searchable={false}
      readOnly
    />
  );
}

function AgentView({ plugin, viewKey = "" }: { plugin: PluginContext; viewKey?: string }) {
  const [id, rest] = splitKey(viewKey);
  const side: Side = rest === "b" ? "b" : "a";
  const d = useDialogue(plugin.client, id);
  if (d === undefined) return null;
  if (d === null) {
    return (
      <div className="placard">
        <h2>This dialogue is gone</h2>
        <p>It was removed.</p>
      </div>
    );
  }
  const session = sideSession(d, side);
  const live = d.running && !!session && sideAlive(d, side);
  return (
    <div className="dialogue-agent" data-side={side}>
      <div className="viewer-bar">
        <span className="dialogue-side">{side.toUpperCase()}</span>
        <strong>{sideName(d, side)}</strong>
        {sideTool(d, side) && (
          <span className="tool-badge" data-tool={sideTool(d, side)}>
            {sideTool(d, side)}
          </span>
        )}
        <span className="viewer-path">{d.title}</span>
        <span className="viewer-meta">{live ? "Live, read-only" : d.running ? statusLabel(d.status) : "Transcript"}</span>
      </div>
      {live ? (
        <LiveTerminal session={session!} />
      ) : d.running ? (
        <div className="placard">
          <h2>{d.status === "starting" ? `Starting ${sideName(d, side)}` : `${sideName(d, side)} isn't running`}</h2>
          <p>{d.status === "starting" ? "The agents are booting. Their screens show here once they run." : "Its session has ended. The transcript shows once the dialogue finishes."}</p>
        </div>
      ) : (
        <Transcript plugin={plugin} d={d} side={side} />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ a file's tab

function FileView({ plugin, viewKey = "" }: { plugin: PluginContext; viewKey?: string }) {
  const [id, path] = splitKey(viewKey);
  const [file, setFile] = useState<FileText | null>(null);
  const [error, setError] = useState("");
  const [raw, setRaw] = useState(false);
  useEffect(() => {
    let live = true;
    setFile(null);
    setError("");
    plugin.client.get<FileText>(`${dialoguePath(id)}/file?path=${encodeURIComponent(path)}`).then(
      (found) => live && setFile(found),
      (err: unknown) => live && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      live = false;
    };
  }, [plugin.client, id, path]);
  const markdown = /\.(md|markdown)$/i.test(path);
  return (
    <div className="viewer">
      <div className="viewer-bar">
        <span className="viewer-path" title={`${path} (${id})`}>
          {path}
        </span>
        {file && <span className="viewer-meta">{formatBytes(file.size)}</span>}
        {markdown && file && !file.binary && (
          <span className="viewer-modes">
            <button type="button" aria-pressed={!raw} onClick={() => setRaw(false)}>
              Preview
            </button>
            <button type="button" aria-pressed={raw} onClick={() => setRaw(true)}>
              Raw
            </button>
          </span>
        )}
      </div>
      {file?.truncated && <p className="viewer-note">Only the first 1 MB is shown.</p>}
      {error ? (
        <div className="placard">
          <h2>Can't show this file</h2>
          <p>{error}</p>
        </div>
      ) : !file ? null : file.binary ? (
        <div className="placard">
          <h2>Not a text file</h2>
          <p>{path} can't be shown here.</p>
        </div>
      ) : markdown && !raw ? (
        <div className="markdown-scroll">
          <Suspense fallback={null}>
            <Text text={file.text} />
          </Suspense>
        </div>
      ) : (
        <div className="viewer-scroll" data-wrap="">
          <pre className="viewer-text">{file.text}</pre>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ the right panel

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="detail-row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function FileLinks({ id, files }: { id: string; files: DialogueFile[] }) {
  return (
    <ul className="file-list dialogue-files">
      {files.map((f) => (
        <li key={f.path}>
          <button type="button" className="file-row" onClick={() => openFile(id, f.path)} title={`Open ${f.path}`}>
            <span className="file-glyph" aria-hidden="true">·</span>
            <span>{f.path.replace(/^deliverables\//, "")}</span>
            <span className="file-size">{formatBytes(f.size)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function Actions({ plugin, d }: { plugin: PluginContext; d: DialogueDetail }) {
  const act = useApp((s) => s.act);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function run(action: (client: PluginClient) => Promise<unknown>, after?: () => void) {
    setBusy(true);
    const done = await act(() => action(plugin.client));
    setBusy(false);
    setConfirming(false);
    if (done !== undefined) after?.();
    await refreshDialogues(plugin.client);
  }

  return (
    <div className="details-actions">
      {d.running && (
        <p className="stop-buttons">
          <button type="button" disabled={busy} onClick={() => void run((c) => c.post(`${dialoguePath(d.id)}/stop`))}>
            Stop dialogue
          </button>
        </p>
      )}
      {confirming ? (
        <div className="plugin-warning" role="alert">
          <p>
            Remove this dialogue and everything in it: the brief, both transcripts, the log and every artifact.
            {d.running && " Both agents are stopped first."} This can't be undone.
          </p>
          <div className="plugin-warning-actions">
            <button type="button" disabled={busy} onClick={() => setConfirming(false)}>
              Keep
            </button>
            <button type="button" className="primary" disabled={busy} onClick={() => void run((c) => c.delete(dialoguePath(d.id)), () => forgetDialogue(d.id))}>
              {busy ? "Removing…" : "Remove for good"}
            </button>
          </div>
        </div>
      ) : (
        <button type="button" disabled={busy} onClick={() => setConfirming(true)}>
          Remove dialogue…
        </button>
      )}
    </div>
  );
}

function Info({ plugin, id }: { plugin: PluginContext; id: string }) {
  const summary = useDialogue(plugin.client, id);
  const [detail, setDetail] = useState<DialogueDetail | null>(null);
  const [files, setFiles] = useState<DialogueFile[]>([]);
  const running = !!summary?.running;
  const changed = summary ? `${summary.status}/${summary.end_time}` : "";

  // Fetched again when the dialogue's status changes, and every few seconds while it runs.
  useEffect(() => {
    if (!changed) return;
    let live = true;
    const load = () => {
      void plugin.client.get<DialogueDetail>(dialoguePath(id)).then((found) => live && setDetail(found), () => undefined);
      void plugin.client.get<DialogueFile[]>(`${dialoguePath(id)}/files`).then((found) => live && setFiles(found), () => undefined);
    };
    load();
    const timer = running ? setInterval(load, DETAIL_POLL_MS) : undefined;
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [plugin.client, id, changed, running]);

  if (summary === undefined) return null;
  if (summary === null || !detail || detail.id !== id) return summary === null ? <p className="detail-empty">This dialogue was removed.</p> : null;

  const d = detail;
  const s = d.stats;
  const artifacts = files.filter((f) => f.path.startsWith("deliverables/"));
  const own = files.filter((f) => !f.path.startsWith("deliverables/"));

  return (
    <div className="dialogue-info">
      <h3>Dialogue</h3>
      <p className="dialogue-title">{d.title}</p>
      <dl>
        <Row label="Status">
          <span className="dialogue-status" data-status={markOf(d)}>
            {statusLabel(d.status)}
          </span>
        </Row>
        <Row label="Scenario">{d.scenario}</Row>
        <Row label="A">
          {d.name_a} <span className="tool-badge" data-tool={d.agent_a ?? undefined}>{d.agent_a}</span>
        </Row>
        <Row label="B">
          {d.name_b} <span className="tool-badge" data-tool={d.agent_b ?? undefined}>{d.agent_b}</span>
        </Row>
        <Row label="Started">{when(d.start_time)}</Row>
        {d.end_time && <Row label="Ended">{when(d.end_time)}</Row>}
        <Row label="Max turns">{d.max_turns}</Row>
        <Row label="Folder">
          <span className="dialogue-folder">{d.cwd}</span>
        </Row>
      </dl>
      {d.error && <p className="form-error">{d.error}</p>}

      <h3>{d.running ? "So far" : "Stats"}</h3>
      <dl>
        <Row label="Turns">
          {s.turns} of {d.max_turns}
        </Row>
        <Row label={d.name_a}>
          {s.turns_a} messages, {s.words_a.toLocaleString()} words
        </Row>
        <Row label={d.name_b}>
          {s.turns_b} messages, {s.words_b.toLocaleString()} words
        </Row>
        {s.duration_seconds != null && <Row label="Took">{duration(s.duration_seconds)}</Row>}
        {!d.running && <Row label="Ended by">{s.completed_by ? `${s.completed_by} marked it complete` : d.status === "completed" ? "Reached max turns" : statusLabel(d.status)}</Row>}
        {s.retries > 0 && <Row label="Retries">{s.retries} replies missed the format and were asked again</Row>}
      </dl>

      <h3>Artifacts</h3>
      {artifacts.length ? <FileLinks id={id} files={artifacts} /> : <p className="detail-empty">{d.running ? "None yet." : "The agents saved no files."}</p>}
      {own.length > 0 && (
        <>
          <h3>Brief and progress</h3>
          <FileLinks id={id} files={own} />
        </>
      )}

      <Actions plugin={plugin} d={d} />
    </div>
  );
}

function RightPanel({ plugin }: { plugin: PluginContext }) {
  const id = useCurrentDialogue();
  useDialogueList(plugin.client);
  if (!id) return <p className="detail-empty">Pick a dialogue to see its details.</p>;
  return <Info key={id} plugin={plugin} id={id} />;
}

// ------------------------------------------------------------------ in the session list

/**
 * The sessions in a dialogue, fetched once per client for each set of sessions,
 * however many rows ask: a dialogue can only start or end when that set changes.
 * Dialogues started from the app are left out of the list; this marks the ones
 * started before that, or from the command line with an older agentop.
 */
const sessionsInDialogues = new WeakMap<PluginClient, { key: string; names: Promise<Set<string>> }>();

function dialogueSessions(client: PluginClient, key: string): Promise<Set<string>> {
  const known = sessionsInDialogues.get(client);
  if (known?.key === key) return known.names;
  const names = client.get<DialogueSummary[]>("/dialogues").then(
    (found) => new Set(found.flatMap((d) => [d.session_a, d.session_b]).filter((s): s is string => !!s)),
    () => new Set<string>(),
  );
  sessionsInDialogues.set(client, { key, names });
  return names;
}

function DialogueBadge({ session, plugin }: { session: { id: string }; plugin: PluginContext }) {
  const key = useApp((s) => Object.keys(s.sessions).sort().join("\n"));
  const [inDialogue, setInDialogue] = useState(false);
  useEffect(() => {
    let live = true;
    void dialogueSessions(plugin.client, key).then((names) => live && setInDialogue(names.has(session.id)));
    return () => {
      live = false;
    };
  }, [plugin.client, key, session.id]);
  return inDialogue ? <span className="session-tag">Dialogue</span> : null;
}

async function beforeDisable(plugin: PluginContext): Promise<string | null> {
  const running = (await plugin.client.get<DialogueSummary[]>("/dialogues").catch(() => [])).filter((d) => d.running);
  if (!running.length) return null;
  const count = running.length === 1 ? "A dialogue is" : `${running.length} dialogues are`;
  return `${count} running. Turning Dialogues off stops ${running.length === 1 ? "it" : "them"}; what was said so far is kept.`;
}

function agentLabel(viewKey: string): string {
  const [id, side] = splitKey(viewKey);
  const d = useDialogues.getState().items?.find((item) => item.id === id);
  const name = d ? sideName(d, side === "b" ? "b" : "a") : `Agent ${side.toUpperCase()}`;
  return d ? `${name}: ${d.title}` : name;
}

function fileLabel(viewKey: string): string {
  const [, path] = splitKey(viewKey);
  return path.split("/").pop() || path;
}

export const plugin: UiPlugin = {
  workspace: {
    label: "Dialogues",
    views: {
      new: { label: "New dialogue", Component: NewDialogue },
      agent: { label: "Agent", Component: AgentView, tabLabel: agentLabel },
      file: { label: "File", Component: FileView, tabLabel: fileLabel },
    },
    empty: EmptyView,
    left: DialogueList,
    right: RightPanel,
    action: { label: "New dialogue", run: openWizard },
  },
  sessionBadge: DialogueBadge,
  beforeDisable,
};

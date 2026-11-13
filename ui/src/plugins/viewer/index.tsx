import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { platform } from "../../platform";
import { useApp } from "../../store";
import type { FileRef } from "../../tabs";
import type { PluginContext, UiPlugin } from "../types";
import { parseDiff, type DiffRow } from "./diff";
import { isMarkdown } from "./paths";

// Its libraries load the first time a Markdown file is opened.
const Markdown = lazy(() => import("./Markdown"));

interface FileText {
  path: string;
  absolute: string;
  size: number;
  binary: boolean;
  truncated: boolean;
  /** A transcript the service can also read as a conversation. */
  conversation: boolean;
  text: string;
}

interface Message {
  role: "user" | "assistant";
  text: string;
  at: number | null;
}

interface Conversation {
  path: string;
  total: number;
  messages: Message[];
}

/** How the file is drawn. `raw` is the text with line numbers, which every text file has. */
type Mode = "conversation" | "preview" | "raw";

const MODE_LABELS: Record<Mode, string> = { conversation: "Conversation", preview: "Preview", raw: "Raw" };

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

function message(failure: unknown): string {
  return failure instanceof Error ? failure.message : String(failure);
}

const URL_PATTERN = /https?:\/\/[^\s<>"'`)\]]+/g;

/** Text with its links made clickable. */
function Linked({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const url = match[0].replace(/[.,;:!?]+$/, "");
    const start = match.index ?? 0;
    parts.push(text.slice(last, start));
    parts.push(
      <a key={start} href={url} target="_blank" rel="noreferrer noopener">
        {url}
      </a>,
    );
    last = start + url.length;
  }
  parts.push(text.slice(last));
  return <>{parts}</>;
}

/** The prompts and replies in a transcript, oldest first, opened at the latest. */
function ConversationView({ conversation, label }: { conversation: Conversation; label: string }) {
  const scroller = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [conversation]);

  if (conversation.messages.length === 0) {
    return (
      <div className="placard">
        <h2>No messages yet</h2>
        <p>This transcript has no prompts or replies in it.</p>
      </div>
    );
  }
  const hidden = conversation.total - conversation.messages.length;
  return (
    <div className="conversation" ref={scroller} tabIndex={0} aria-label={label}>
      {hidden > 0 && (
        <p className="viewer-note" role="status">
          Showing the latest {conversation.messages.length} of {conversation.total} messages.
        </p>
      )}
      <ol>
        {conversation.messages.map((m, index) => (
          <li key={index} className="message" data-role={m.role}>
            <div className="message-head">
              <strong>{m.role === "user" ? "You" : "Agent"}</strong>
              {m.at !== null && <time dateTime={new Date(m.at * 1000).toISOString()}>{new Date(m.at * 1000).toLocaleString()}</time>}
            </div>
            <div className="message-text">
              <Linked text={m.text} />
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

interface FileDiff {
  path: string;
  absolute: string;
  binary: boolean;
  truncated: boolean;
  text: string;
}

function DiffLine({ row }: { row: DiffRow }) {
  if (row.kind === "hunk" || row.kind === "note") {
    return (
      <div className="diff-row" data-kind={row.kind}>
        <span className="diff-num" />
        <span className="diff-num" />
        <span className="diff-text">{row.text}</span>
      </div>
    );
  }
  return (
    <div className="diff-row" data-kind={row.kind}>
      <span className="diff-num">{row.old ?? ""}</span>
      <span className="diff-num">{row.new ?? ""}</span>
      <span className="diff-text">
        <span className="diff-sign" aria-hidden="true">{row.kind === "add" ? "+" : row.kind === "del" ? "-" : " "}</span>
        {row.text}
      </span>
    </div>
  );
}

/** A changed file's diff against HEAD, staged and unstaged together, with old and new line numbers. */
function DiffViewer({ file, plugin }: { file: FileRef; plugin: PluginContext }) {
  const [result, setResult] = useState<FileDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wrap, setWrap] = useState(false);
  const openTab = useApp((s) => s.openTab);

  const load = useCallback(() => {
    const query = new URLSearchParams({ session: file.sessionId, path: file.path });
    plugin.client.get<FileDiff>(`/diff?${query}`).then(
      (value) => {
        setResult(value);
        setError(null);
      },
      (failure: unknown) => setError(message(failure)),
    );
  }, [file.sessionId, file.path, plugin.client]);
  useEffect(load, [load]);

  const diff = useMemo(() => (result ? parseDiff(result.text) : null), [result]);
  const deleted = diff?.notes.some((n) => n.startsWith("deleted file mode")) ?? false;

  let body: ReactNode = null;
  if (error) {
    body = (
      <div className="placard">
        <h2>No diff for {file.path.split("/").pop()}</h2>
        <p>{error}</p>
      </div>
    );
  } else if (diff && result) {
    body = (
      <>
        {diff.notes.length > 0 && <p className="viewer-note diff-notes">{diff.notes.join(". ")}</p>}
        {result.truncated && (
          <p className="viewer-note" role="status">
            Showing the first {formatSize(result.text.length)} of the diff.
          </p>
        )}
        {diff.rows.length === 0 ? (
          <div className="placard">
            <h2>{result.binary ? "This is a binary file" : "No line changes"}</h2>
            <p>{result.binary ? "Only text changes can be shown." : "Only the file's mode or name changed."}</p>
          </div>
        ) : (
          <div className="diff" data-wrap={wrap || undefined} tabIndex={0} aria-label={`Changes to ${file.path}`}>
            {diff.rows.map((row, index) => (
              <DiffLine key={index} row={row} />
            ))}
          </div>
        )}
      </>
    );
  }

  return (
    <div className="viewer">
      <div className="viewer-bar">
        <span className="viewer-path" title={result?.absolute ?? file.path}>
          <span className="viewer-source">Diff</span>
          {file.path}
        </span>
        {diff && (diff.added > 0 || diff.removed > 0) && (
          <span className="viewer-meta">
            <span className="diff-added">+{diff.added}</span> <span className="diff-removed">−{diff.removed}</span>
          </span>
        )}
        <button type="button" aria-pressed={wrap} onClick={() => setWrap(!wrap)}>
          Wrap
        </button>
        {!deleted && (
          <button
            type="button"
            title="Open the file as it is now"
            onClick={(event) => openTab({ kind: "file", file: { ...file, view: undefined } }, event.ctrlKey || event.metaKey ? "side" : "tab")}
          >
            Open file
          </button>
        )}
        <button type="button" onClick={load}>
          Reload
        </button>
        {result && (
          <button type="button" onClick={() => void platform.copyText(result.absolute)}>
            Copy path
          </button>
        )}
      </div>
      {body}
    </div>
  );
}

/** A file in a tab: its diff when opened from the Changed list, otherwise its text. */
function FileViewer({ file, plugin }: { file: FileRef; plugin: PluginContext }) {
  return file.view === "diff" ? <DiffViewer file={file} plugin={plugin} /> : <TextViewer file={file} plugin={plugin} />;
}

/** A file's text with line numbers, or drawn: a transcript as its conversation, Markdown as a page. */
function TextViewer({ file, plugin }: { file: FileRef; plugin: PluginContext }) {
  const [result, setResult] = useState<FileText | null>(null);
  const [chat, setChat] = useState<Conversation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wrap, setWrap] = useState(false);
  // null until a mode is picked: the first of the file's modes.
  const [mode, setMode] = useState<Mode | null>(null);
  const openTab = useApp((s) => s.openTab);

  const load = useCallback(() => {
    const query = new URLSearchParams({ session: file.sessionId, source: file.source, path: file.path });
    plugin.client.get<FileText>(`/file?${query}`).then(
      (value) => {
        setResult(value);
        setError(null);
        if (value.conversation) {
          const params = new URLSearchParams({ session: file.sessionId, path: file.path });
          plugin.client.get<Conversation>(`/conversation?${params}`).then(setChat, (failure: unknown) => setError(message(failure)));
        }
      },
      (failure: unknown) => setError(message(failure)),
    );
  }, [file.sessionId, file.source, file.path, plugin.client]);
  useEffect(load, [load]);

  const modes = useMemo((): Mode[] => {
    if (!result || result.binary) return ["raw"];
    if (result.conversation) return ["conversation", "raw"];
    if (isMarkdown(file.path)) return ["preview", "raw"];
    return ["raw"];
  }, [result, file.path]);
  const showing: Mode = mode && modes.includes(mode) ? mode : modes[0];
  const openBeside = useCallback(
    (path: string, side: boolean) => openTab({ kind: "file", file: { sessionId: file.sessionId, source: file.source, path } }, side ? "side" : "tab"),
    [file, openTab],
  );
  const lines = useMemo(() => {
    if (!result || result.binary) return 0;
    const count = result.text.split("\n").length;
    return result.text.endsWith("\n") ? count - 1 : count;
  }, [result]);
  const gutter = useMemo(() => Array.from({ length: lines }, (_, i) => i + 1).join("\n"), [lines]);

  let body: ReactNode = null;
  if (error) {
    body = (
      <div className="placard">
        <h2>Can't open {file.path.split("/").pop()}</h2>
        <p>{error}</p>
      </div>
    );
  } else if (!result) {
    body = null;
  } else if (showing === "conversation") {
    body = chat && <ConversationView conversation={chat} label={`Conversation in ${file.path}`} />;
  } else if (showing === "preview") {
    body = (
      <>
        {result.truncated && (
          <p className="viewer-note" role="status">
            Showing the first {formatSize(result.text.length)} of {formatSize(result.size)}.
          </p>
        )}
        <Suspense fallback={null}>
          <Markdown text={result.text} file={file} plugin={plugin} label={`Preview of ${file.path}`} onOpen={openBeside} />
        </Suspense>
      </>
    );
  } else if (result.binary) {
    body = (
      <div className="placard">
        <h2>This is a binary file</h2>
        <p>Only text files can be shown.</p>
      </div>
    );
  } else {
    body = (
      <>
        {result.truncated && (
          <p className="viewer-note" role="status">
            Showing the first {formatSize(result.text.length)} of {formatSize(result.size)}.
          </p>
        )}
        <div className="viewer-scroll" data-wrap={wrap || undefined}>
          {!wrap && (
            <pre className="viewer-gutter" aria-hidden="true">
              {gutter}
            </pre>
          )}
          <pre className="viewer-text" tabIndex={0} aria-label={`Contents of ${file.path}`}>
            {result.text}
          </pre>
        </div>
      </>
    );
  }

  return (
    <div className="viewer">
      <div className="viewer-bar">
        <span className="viewer-path" title={result?.absolute ?? file.path}>
          {file.source === "transcript" && <span className="viewer-source">Transcript</span>}
          {file.path}
        </span>
        {result && (
          <span className="viewer-meta">
            {formatSize(result.size)}
            {showing === "conversation" ? chat && `, ${chat.total} messages` : !result.binary && `, ${lines} lines`}
          </span>
        )}
        {modes.length > 1 && (
          <span className="viewer-modes" role="group" aria-label="Show as">
            {modes.map((m) => (
              <button key={m} type="button" aria-pressed={showing === m} onClick={() => setMode(m)}>
                {MODE_LABELS[m]}
              </button>
            ))}
          </span>
        )}
        {showing === "raw" && (
          <button type="button" aria-pressed={wrap} onClick={() => setWrap(!wrap)}>
            Wrap
          </button>
        )}
        <button type="button" onClick={load}>
          Reload
        </button>
        {result && (
          <button type="button" onClick={() => void platform.copyText(result.absolute)}>
            Copy path
          </button>
        )}
      </div>
      {body}
    </div>
  );
}

export const plugin: UiPlugin = {
  fileViewer: FileViewer,
};

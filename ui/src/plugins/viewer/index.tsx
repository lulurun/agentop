import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { platform } from "../../platform";
import type { FileRef } from "../../tabs";
import type { PluginContext, UiPlugin } from "../types";

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

type Mode = "conversation" | "raw";

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

/** A file's text with line numbers, or, for a transcript, its conversation. */
function TextViewer({ file, plugin }: { file: FileRef; plugin: PluginContext }) {
  const [result, setResult] = useState<FileText | null>(null);
  const [chat, setChat] = useState<Conversation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wrap, setWrap] = useState(false);
  const [mode, setMode] = useState<Mode>("conversation");

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

  const showing: Mode = result?.conversation ? mode : "raw";
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
        {result?.conversation && (
          <span className="viewer-modes" role="group" aria-label="Show as">
            <button type="button" aria-pressed={showing === "conversation"} onClick={() => setMode("conversation")}>
              Conversation
            </button>
            <button type="button" aria-pressed={showing === "raw"} onClick={() => setMode("raw")}>
              Raw
            </button>
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
  fileViewer: TextViewer,
};

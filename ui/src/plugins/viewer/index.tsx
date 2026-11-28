import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { platform } from "../../platform";
import { useApp } from "../../store";
import type { FileRef } from "../../tabs";
import { ChatView, type ChatMessage } from "../../components/Conversation";
import { useArea } from "../../components/Workbench";
import type { PluginContext, UiPlugin } from "../types";
import { parseDiff, type DiffRow } from "./diff";
import { fileQuery, isImage, isMarkdown, languageFor } from "./paths";

// Its libraries load the first time a Markdown file is opened.
const Markdown = lazy(() => import("./Markdown"));
const Code = lazy(() => import("./Code"));

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

interface Conversation {
  path: string;
  total: number;
  messages: ChatMessage[];
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
  const area = useArea();

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
            onClick={() => openTab({ kind: "file", file: { ...file, view: undefined } }, area)}
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

interface FileImage {
  path: string;
  absolute: string;
  type: string;
  size: number;
  data: string;
}

/** An image file, drawn by the browser in the middle of the tab. */
function ImageViewer({ file, plugin }: { file: FileRef; plugin: PluginContext }) {
  const [result, setResult] = useState<FileImage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    const query = fileQuery(file);
    // The page can't put its token on an <img>, so the image comes as data.
    plugin.client.get<FileImage>(`/image?${query}`).then(
      (value) => {
        setResult(value);
        setError(null);
      },
      (failure: unknown) => setError(message(failure)),
    );
  }, [file.sessionId, file.source, file.path, plugin.client]);
  useEffect(load, [load]);

  let body: ReactNode = null;
  if (error) {
    body = (
      <div className="placard">
        <h2>Can't open {file.path.split("/").pop()}</h2>
        <p>{error}</p>
      </div>
    );
  } else if (result) {
    body = (
      <div className="viewer-image">
        <img src={`data:${result.type};base64,${result.data}`} alt={file.path} />
      </div>
    );
  }

  return (
    <div className="viewer">
      <div className="viewer-bar">
        <span className="viewer-path" title={result?.absolute ?? file.path}>
          {file.path}
        </span>
        {result && <span className="viewer-meta">{formatSize(result.size)}</span>}
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

/** A file in a tab: its diff when opened from the Changed list, an image drawn, otherwise its text. */
function FileViewer({ file, plugin }: { file: FileRef; plugin: PluginContext }) {
  if (file.view === "diff") return <DiffViewer file={file} plugin={plugin} />;
  if (isImage(file.path)) return <ImageViewer file={file} plugin={plugin} />;
  return <TextViewer file={file} plugin={plugin} />;
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
  const area = useArea();

  const load = useCallback(() => {
    const query = fileQuery(file);
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
  const openLinked = useCallback(
    (path: string) => openTab({ kind: "file", file: { sessionId: file.sessionId, source: file.source, path } }, area),
    [file, openTab, area],
  );
  const language = languageFor(file.path);
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
    body = chat && <ChatView messages={chat.messages} total={chat.total} label={`Conversation in ${file.path}`} />;
  } else if (showing === "preview") {
    body = (
      <>
        {result.truncated && (
          <p className="viewer-note" role="status">
            Showing the first {formatSize(result.text.length)} of {formatSize(result.size)}.
          </p>
        )}
        <Suspense fallback={null}>
          <Markdown text={result.text} file={file} plugin={plugin} label={`Preview of ${file.path}`} onOpen={openLinked} />
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
            {language ? (
              <Suspense fallback={result.text}>
                <Code text={result.text} language={language} />
              </Suspense>
            ) : (
              result.text
            )}
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
          {file.source === "project" && <span className="viewer-source">Project</span>}
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

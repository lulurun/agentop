import { useCallback, useEffect, useMemo, useState } from "react";
import { platform } from "../../platform";
import type { FileRef } from "../../tabs";
import type { PluginContext, UiPlugin } from "../types";

interface FileText {
  path: string;
  absolute: string;
  size: number;
  binary: boolean;
  truncated: boolean;
  text: string;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** Plain text with line numbers. Syntax highlighting and other renderings come later, per file type. */
function TextViewer({ file, plugin }: { file: FileRef; plugin: PluginContext }) {
  const [result, setResult] = useState<FileText | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wrap, setWrap] = useState(false);

  const load = useCallback(() => {
    const query = new URLSearchParams({ session: file.sessionId, source: file.source, path: file.path });
    plugin.client.get<FileText>(`/file?${query}`).then(
      (value) => {
        setResult(value);
        setError(null);
      },
      (failure: unknown) => setError(failure instanceof Error ? failure.message : String(failure)),
    );
  }, [file.sessionId, file.source, file.path, plugin.client]);
  useEffect(load, [load]);

  const lines = useMemo(() => {
    if (!result || result.binary) return 0;
    const count = result.text.split("\n").length;
    return result.text.endsWith("\n") ? count - 1 : count;
  }, [result]);
  const gutter = useMemo(() => Array.from({ length: lines }, (_, i) => i + 1).join("\n"), [lines]);

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
            {!result.binary && `, ${lines} lines`}
          </span>
        )}
        <button type="button" aria-pressed={wrap} onClick={() => setWrap(!wrap)}>
          Wrap
        </button>
        <button type="button" onClick={load}>
          Reload
        </button>
        {result && (
          <button type="button" onClick={() => void platform.copyText(result.absolute)}>
            Copy path
          </button>
        )}
      </div>
      {error ? (
        <div className="placard">
          <h2>Can't open {file.path.split("/").pop()}</h2>
          <p>{error}</p>
        </div>
      ) : !result ? null : result.binary ? (
        <div className="placard">
          <h2>This is a binary file</h2>
          <p>Only text files can be shown.</p>
        </div>
      ) : (
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
      )}
    </div>
  );
}

export const plugin: UiPlugin = {
  fileViewer: TextViewer,
};

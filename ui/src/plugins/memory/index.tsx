import { useCallback, useEffect, useState } from "react";
import type { PluginContext, UiPlugin } from "../types";

interface Status {
  folder: string;
  files: number;
  bytes: number;
  tools: Record<string, { files: number; bytes: number }>;
  last_sweep: { at: number; seconds: number; copied: number; appended: number; replaced: number; errors: string[] } | null;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** What the archive holds, and when it was last brought up to date. */
function ArchiveStatus({ plugin }: { plugin: PluginContext }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sweeping, setSweeping] = useState(false);

  const load = useCallback(() => {
    plugin.client.get<Status>("/status").then(
      (value) => {
        setStatus(value);
        setError(null);
      },
      (failure: unknown) => setError(failure instanceof Error ? failure.message : String(failure)),
    );
  }, [plugin.client]);
  useEffect(load, [load]);
  useEffect(() => plugin.onEvent(load), [plugin, load]);

  async function sweepNow() {
    setSweeping(true);
    try {
      setStatus(await plugin.client.post<Status>("/sweep"));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSweeping(false);
    }
  }

  if (error) return <p className="memory-note">{error}</p>;
  if (!status) return null;
  const last = status.last_sweep;
  return (
    <div className="memory-status">
      <p className="memory-note">
        {status.files} transcript files, {formatSize(status.bytes)}
        {Object.keys(status.tools).length > 0 &&
          ` (${Object.entries(status.tools)
            .map(([tool, t]) => `${tool} ${t.files}`)
            .join(", ")})`}
        , kept in <code>{status.folder}</code>.
      </p>
      <p className="memory-note">
        {last
          ? `Last sweep ${new Date(last.at * 1000).toLocaleString()}: ${last.copied} new, ${last.appended} grown, ${last.replaced} replaced, in ${last.seconds}s.`
          : "Not swept yet."}
      </p>
      {last && last.errors.length > 0 && (
        <ul className="memory-errors">
          {last.errors.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      <button type="button" disabled={sweeping} onClick={() => void sweepNow()}>
        {sweeping ? "Sweeping…" : "Sweep now"}
      </button>
    </div>
  );
}

export const plugin: UiPlugin = {
  settingsSection: ArchiveStatus,
};

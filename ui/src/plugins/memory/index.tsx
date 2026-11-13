import { useCallback, useEffect, useState } from "react";
import type { PluginContext, UiPlugin } from "../types";

interface Status {
  folder: string;
  files: number;
  bytes: number;
  tools: Record<string, { files: number; bytes: number }>;
  last_sweep: { at: number; seconds: number; copied: number; appended: number; replaced: number; errors: string[] } | null;
  messages: {
    messages: number;
    sessions: number;
    tools: Record<string, { messages: number; sessions: number }>;
    last_index: { errors: string[] } | null;
  };
  summaries: {
    summaries: number;
    due: number;
    failed: number;
    enabled: boolean;
    /** The tool and model that writes them, or null when no installed tool can. */
    by: string | null;
    last_run: { errors: string[] } | null;
  };
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
  const [switching, setSwitching] = useState(false);

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

  async function setSummaries(enabled: boolean) {
    setSwitching(true);
    try {
      setStatus(await plugin.client.patch<Status>("/summaries", { enabled }));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSwitching(false);
    }
  }

  if (error) return <p className="memory-note">{error}</p>;
  if (!status) return null;
  const last = status.last_sweep;
  const summaries = status.summaries;
  const errors = [
    ...(last?.errors ?? []),
    ...(status.messages.last_index?.errors ?? []),
    ...(summaries.last_run?.errors ?? []),
  ];
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
      <p className="memory-note">
        {status.messages.messages} prompts and replies from {status.messages.sessions} sessions
        {Object.keys(status.messages.tools).length > 0 &&
          ` (${Object.entries(status.messages.tools)
            .map(([tool, t]) => `${tool} ${t.messages}`)
            .join(", ")})`}
        .
      </p>
      <label className="memory-switch">
        <input
          type="checkbox"
          checked={summaries.enabled}
          disabled={switching || !summaries.by}
          onChange={(event) => void setSummaries(event.target.checked)}
        />
        Summarize sessions for search
      </label>
      <p className="memory-note">
        {!summaries.by
          ? "No installed agent tool can write summaries. Claude Code can."
          : `${summaries.summaries} sessions summarized${summaries.due ? `, ${summaries.due} waiting` : ""}${
              summaries.failed ? `, ${summaries.failed} failed` : ""
            }. Each session is sent to ${summaries.by} once it has been quiet for 30 minutes, whichever tool it was with.`}
      </p>
      {errors.length > 0 && (
        <ul className="memory-errors">
          {errors.map((line) => (
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

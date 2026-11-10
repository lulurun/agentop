import { useState } from "react";
import type { Session } from "../api/client";
import { platform } from "../platform";
import { PluginSlot } from "../plugins/Slot";
import { useApp } from "../store";
import { FilesSection, TranscriptsSection } from "./FilesSection";
import { formatDuration } from "./format";
import { selectedSession } from "./MainArea";

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

function Row({ label, value, copy }: { label: string; value: string; copy?: string }) {
  return (
    <div className="detail-row">
      <dt>{label}</dt>
      <dd>
        {copy ? (
          <button type="button" className="copyable" title="Copy" onClick={() => void platform.copyText(copy)}>
            {value}
          </button>
        ) : (
          value
        )}
      </dd>
    </div>
  );
}

/** Shows a value that becomes a text box on click. Enter or leaving it saves, Escape cancels. */
function EditableRow({
  label,
  value,
  placeholder,
  maxLength,
  onSave,
}: {
  label: string;
  value: string;
  placeholder: string;
  maxLength: number;
  onSave(value: string): void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const finish = () => {
    if (draft !== null && draft.trim() !== value) onSave(draft);
    setDraft(null);
  };

  return (
    <div className="detail-row">
      <dt>{label}</dt>
      <dd>
        {draft === null ? (
          <button type="button" className="editable" title={`Edit ${label.toLowerCase()}`} onClick={() => setDraft(value)}>
            {value || <span className="editable-empty">{placeholder}</span>}
          </button>
        ) : (
          <input
            autoFocus
            className="editable-input"
            aria-label={label}
            maxLength={maxLength}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={finish}
            onKeyDown={(event) => {
              if (event.key === "Enter") finish();
              if (event.key === "Escape") setDraft(null);
            }}
          />
        )}
      </dd>
    </div>
  );
}

function BridgeRow({ url }: { url: string }) {
  if (!/^https?:\/\//i.test(url)) return null; // reported by the agent, so only ever a web link
  return (
    <div className="detail-row">
      <dt>Bridge</dt>
      <dd>
        <a href={url} target="_blank" rel="noopener noreferrer">
          {url}
        </a>
      </dd>
    </div>
  );
}

function Usage({ session }: { session: Session }) {
  const usage = session.token_usage;
  if (!usage) return <p className="detail-empty">Not reported by this tool yet.</p>;
  return (
    <dl>
      <Row label="Input" value={formatTokens(usage.input_tokens ?? 0)} />
      <Row label="Output" value={formatTokens(usage.output_tokens ?? 0)} />
      <Row label="Cache read" value={formatTokens(usage.cache_read_input_tokens ?? 0)} />
      <Row label="Cache write" value={formatTokens(usage.cache_creation_input_tokens ?? 0)} />
    </dl>
  );
}

function StopSession({ id, name }: { id: string; name: string }) {
  const act = useApp((s) => s.act);
  const setError = useApp((s) => s.setError);
  const [confirming, setConfirming] = useState(false);
  const [stopping, setStopping] = useState(false);

  async function stop() {
    setStopping(true);
    const result = (await act((client) => client.stop(id))) as { forced?: boolean } | undefined;
    if (result?.forced) setError(`${name} didn't exit within 10 seconds, so its terminal was closed.`);
    setStopping(false);
    setConfirming(false);
  }

  if (stopping) {
    return (
      <section className="details-actions">
        <p className="stop-note" role="status">Stopping {name}…</p>
      </section>
    );
  }
  if (!confirming) {
    return (
      <section className="details-actions">
        <button type="button" onClick={() => setConfirming(true)}>
          Stop session
        </button>
      </section>
    );
  }
  return (
    <section className="details-actions" aria-label={`Stop ${name}?`}>
      <p className="stop-note">
        Stop {name}? Anything typed in its input is cleared, then the agent is asked to exit. If it's still running
        after 10 seconds, its terminal is closed.
      </p>
      <div className="stop-buttons">
        <button type="button" onClick={() => setConfirming(false)}>
          Cancel
        </button>
        <button type="button" className="primary" onClick={() => void stop()}>
          Stop {name}
        </button>
      </div>
    </section>
  );
}

export function DetailsPanel() {
  const session = useApp(selectedSession);
  const act = useApp((s) => s.act);

  if (!session) return <aside className="details"><p className="detail-empty">Select a session to see its details.</p></aside>;
  const attach = session.tmux ? `tmux attach-session -t '=${session.tmux.session}'` : null;

  return (
    <aside className="details">
      <section>
        <h3>Details</h3>
        <dl>
          {session.managed ? (
            <>
              <EditableRow
                key={`${session.id}/name`}
                label="Name"
                value={session.display_name}
                placeholder="Add a name"
                maxLength={64}
                onSave={(display_name) => void act((client) => client.patch(session.id, { display_name }))}
              />
              <EditableRow
                key={`${session.id}/description`}
                label="Description"
                value={session.description ?? ""}
                placeholder="Add a description"
                maxLength={500}
                onSave={(description) => void act((client) => client.patch(session.id, { description }))}
              />
            </>
          ) : (
            <Row label="Name" value={session.display_name} />
          )}
          {session.ai_title && <Row label="Title" value={session.ai_title} />}
          <Row label="Tool" value={session.tool ?? "Unknown"} />
          <Row label="Directory" value={session.cwd || "Unknown"} copy={session.cwd} />
          {session.bridge_url && <BridgeRow url={session.bridge_url} />}
          {attach && <Row label="Attach" value={session.tmux!.session} copy={attach} />}
          {session.pid != null && <Row label="Process" value={String(session.pid)} />}
          {session.runtime_seconds != null && <Row label="Running for" value={formatDuration(session.runtime_seconds)} />}
          {session.memory_mb != null && <Row label="Memory" value={`${Math.round(session.memory_mb)} MB`} />}
        </dl>
      </section>
      <section>
        <h3>Token usage</h3>
        <Usage session={session} />
      </section>
      {session.cwd && <FilesSection key={`files/${session.id}`} sessionId={session.id} />}
      <TranscriptsSection key={`transcripts/${session.id}`} sessionId={session.id} />
      <PluginSlot slot="right-panel" session={session} />
      {session.managed && session.status === "running" && (
        <StopSession key={`stop/${session.id}`} id={session.id} name={session.display_name} />
      )}
    </aside>
  );
}

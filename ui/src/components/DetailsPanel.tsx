import type { Session } from "../api/client";
import { platform } from "../platform";
import { useApp } from "../store";
import { selectedSession } from "./MainArea";

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

function formatDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (seconds < 60) return "Under a minute";
  if (hours >= 24) return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
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
          <Row label="Name" value={session.display_name} />
          {session.ai_title && <Row label="Title" value={session.ai_title} />}
          <Row label="Tool" value={session.tool ?? "Unknown"} />
          <Row label="Directory" value={session.cwd || "Unknown"} copy={session.cwd} />
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
      {session.managed && session.status === "running" && (
        <section className="details-actions">
          <button type="button" onClick={() => void act((client) => client.stop(session.id))}>
            Stop session
          </button>
        </section>
      )}
    </aside>
  );
}

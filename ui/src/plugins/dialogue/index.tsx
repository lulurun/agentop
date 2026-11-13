import { useCallback, useEffect, useState } from "react";
import { useApp } from "../../store";
import type { PluginClient, PluginContext, UiPlugin } from "../types";

const POLL_MS = 5_000;

interface DialogueSummary {
  id: string;
  status: string;
  agent_a: string | null;
  agent_b: string | null;
  session_a: string | null;
  session_b: string | null;
  session_a_alive: boolean;
  session_b_alive: boolean;
  start_time: string | null;
  max_turns: number | null;
  error: string | null;
}

interface DialogueDetail extends DialogueSummary {
  cwd: string;
  brief: string;
  progress: string;
  log_tail: string;
}

/** The statuses of a dialogue whose relay is still going. */
const RUNNING = new Set(["starting", "running"]);

function isRunning(dialogue: DialogueSummary): boolean {
  return dialogue.session_a_alive || dialogue.session_b_alive;
}

const list = (client: PluginClient) => client.get<DialogueSummary[]>("/dialogues");
const path = (id: string) => `/dialogues/${encodeURIComponent(id)}`;

function Detail({ dialogue, plugin, onChanged }: { dialogue: DialogueDetail; plugin: PluginContext; onChanged(): void }) {
  const act = useApp((s) => s.act);
  const [removing, setRemoving] = useState(false);

  return (
    <div className="dialogue-detail">
      <div className="view-tools">
        {isRunning(dialogue) && (
          <button type="button" onClick={() => void act(() => plugin.client.post(`${path(dialogue.id)}/stop`)).then(onChanged)}>
            Stop dialogue
          </button>
        )}
        {removing ? (
          <>
            <button type="button" onClick={() => setRemoving(false)}>Keep</button>
            <button
              type="button"
              className="primary"
              onClick={() => void act(() => plugin.client.delete(path(dialogue.id))).then(onChanged)}
            >
              Remove for good
            </button>
          </>
        ) : (
          <button type="button" disabled={isRunning(dialogue)} onClick={() => setRemoving(true)}>
            Remove
          </button>
        )}
      </div>
      {dialogue.error && <p className="form-error">{dialogue.error}</p>}
      {dialogue.brief && (
        <>
          <h3>Brief</h3>
          <pre className="dialogue-text">{dialogue.brief}</pre>
        </>
      )}
      {dialogue.progress && (
        <>
          <h3>Progress</h3>
          <pre className="dialogue-text">{dialogue.progress}</pre>
        </>
      )}
      {dialogue.log_tail && (
        <>
          <h3>Log</h3>
          <pre className="dialogue-text">{dialogue.log_tail}</pre>
        </>
      )}
    </div>
  );
}

function DialoguesView({ plugin }: { plugin: PluginContext }) {
  const [items, setItems] = useState<DialogueSummary[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DialogueDetail | null>(null);

  const load = useCallback(async () => {
    const found = await list(plugin.client).catch(() => null);
    if (found) setItems(found);
    if (openId) setDetail(await plugin.client.get<DialogueDetail>(path(openId)).catch(() => null));
  }, [plugin.client, openId]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const close = useCallback(() => {
    setOpenId(null);
    setDetail(null);
    void list(plugin.client).then(setItems, () => undefined);
  }, [plugin.client]);

  if (detail) {
    return (
      <section className="view" aria-label="Dialogue">
        <div className="view-tools">
          <button type="button" onClick={close}>Back to dialogues</button>
        </div>
        <h2>{detail.id}</h2>
        <p className="view-row-meta">
          {detail.status}, {detail.agent_a} and {detail.agent_b}
        </p>
        <Detail dialogue={detail} plugin={plugin} onChanged={close} />
      </section>
    );
  }

  return (
    <section className="view" aria-label="Dialogues">
      <h2>Dialogues</h2>
      {items === null ? (
        <p className="view-empty">Loading…</p>
      ) : items.length === 0 ? (
        <p className="view-empty">No dialogues on this service. Start one with agentop dialogue.</p>
      ) : (
        <ul className="view-list">
          {items.map((item) => (
            <li key={item.id} className="view-row">
              <div className="view-row-main">
                <div className="view-row-title">
                  <button type="button" onClick={() => setOpenId(item.id)}>{item.id}</button>
                </div>
                <div className="view-row-meta">
                  {item.status}, {item.agent_a} and {item.agent_b}
                  {item.start_time && `, ${new Date(item.start_time).toLocaleString()}`}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The sessions in a dialogue, fetched once per client for each set of sessions,
 * however many rows ask: a dialogue can only start or end when that set changes.
 */
const sessionsInDialogues = new WeakMap<PluginClient, { key: string; names: Promise<Set<string>> }>();

function dialogueSessions(client: PluginClient, key: string): Promise<Set<string>> {
  const known = sessionsInDialogues.get(client);
  if (known?.key === key) return known.names;
  const names = list(client).then(
    (found) => new Set(found.flatMap((d) => [d.session_a, d.session_b]).filter((s): s is string => s != null)),
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
  const running = (await list(plugin.client).catch(() => [])).filter((d) => RUNNING.has(d.status));
  if (!running.length) return null;
  const count = running.length === 1 ? "A dialogue is" : `${running.length} dialogues are`;
  return `${count} running. Turning Dialogues off stops ${running.length === 1 ? "it" : "them"}; what was said so far is kept.`;
}

export const plugin: UiPlugin = {
  leftView: { label: "Dialogues", Component: DialoguesView },
  sessionBadge: DialogueBadge,
  beforeDisable,
};

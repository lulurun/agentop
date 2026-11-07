import { useCallback, useEffect, useState } from "react";
import type { DialogueDetail, DialogueSummary, ServiceClient } from "../api/client";
import { useApp } from "../store";

const POLL_MS = 5_000;

function isRunning(dialogue: DialogueSummary): boolean {
  return dialogue.session_a_alive || dialogue.session_b_alive;
}

function Detail({ dialogue, onChanged }: { dialogue: DialogueDetail; onChanged(): void }) {
  const act = useApp((s) => s.act);
  const [removing, setRemoving] = useState(false);

  return (
    <div className="dialogue-detail">
      <div className="view-tools">
        {isRunning(dialogue) && (
          <button type="button" onClick={() => void act((c) => c.stopDialogue(dialogue.id)).then(onChanged)}>
            Stop dialogue
          </button>
        )}
        {removing ? (
          <>
            <button type="button" onClick={() => setRemoving(false)}>Keep</button>
            <button type="button" className="primary" onClick={() => void act((c) => c.removeDialogue(dialogue.id)).then(onChanged)}>
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

export function DialoguesView({ client }: { client: ServiceClient }) {
  const act = useApp((s) => s.act);
  const [items, setItems] = useState<DialogueSummary[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DialogueDetail | null>(null);

  const load = useCallback(async () => {
    const list = await client.dialogues().catch(() => null);
    if (list) setItems(list);
    if (openId) setDetail(await client.dialogue(openId).catch(() => null));
  }, [client, openId]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const close = useCallback(() => {
    setOpenId(null);
    setDetail(null);
    void act((c) => c.dialogues()).then((list) => list && setItems(list));
  }, [act]);

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
        <Detail dialogue={detail} onChanged={close} />
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

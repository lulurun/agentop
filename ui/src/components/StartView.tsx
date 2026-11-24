import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { StartRecent, StartSettings, StartTool } from "../api/client";
import { useStart, type Pending } from "../start";
import { activeCount, useActiveClient, useApp } from "../store";
import { GearIcon } from "./icons";
import { ToolBadge } from "./Sidebar";

/** A touch screen types a new line on Enter, as the input bar does. */
const touchScreen = () => {
  try {
    return window.matchMedia("(pointer: coarse)").matches;
  } catch {
    return false;
  }
};

function basename(path: string): string {
  return path.replace(/\/+$/, "").split("/").pop() || path;
}

function SendIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M2.5 8h10M8.5 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

function quotaText(tool: StartTool | undefined): string {
  if (!tool?.quota) return "quota unknown";
  return tool.quota.map((w) => `${w.name} ${Math.round(w.used_percent)}%`).join(" · ");
}

/** Sessions' start buffer: a session from its first prompt (docs/0_04_start.md). */
export function StartView() {
  const client = useActiveClient();
  const { draft, pending, setDraft, send } = useStart();
  const [touch] = useState(touchScreen);
  const [tools, setTools] = useState<StartTool[]>([]);
  const [recent, setRecent] = useState<StartRecent[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const atLimit = useApp((s) => activeCount(s.sessions) >= s.history.maxActive);
  const launched = pending.filter((p) => p.phase === "launched").length;

  useEffect(() => {
    client.startModels().then(setTools, () => setTools([]));
  }, [client, launched]);
  useEffect(() => {
    client.recentStarts().then(setRecent, () => setRecent([]));
  }, [client, launched]);
  useEffect(() => {
    if (!touch) box.current?.focus({ preventScroll: true });
  }, [touch]);

  function submit() {
    const prompt = draft.trim();
    if (!prompt) return;
    setDraft("");
    send(client, prompt);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.ctrlKey || event.metaKey || (!touch && !event.shiftKey && !event.altKey)) {
      event.preventDefault();
      submit();
    }
  }

  const shown = new Set(pending.map((p) => p.sessionId).filter(Boolean));
  const earlier = recent.filter((r) => !shown.has(r.session));

  return (
    <div className="start-view">
      <div className="start-inner">
        <h2 className="start-title">Start a session</h2>
        <p className="start-lede">Say what the session should do. agentop picks the agent, model and folder, then hands your prompt over, less any words that only asked for the session.</p>
        <div className="composer start-composer">
          <textarea
            ref={box}
            rows={4}
            value={draft}
            placeholder="What should the new session do?"
            enterKeyHint={touch ? "enter" : "send"}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
          />
          <div className="composer-tools">
            <button type="button" className="start-gear" aria-pressed={settingsOpen} onClick={() => setSettingsOpen(!settingsOpen)} title="How agentop chooses">
              <GearIcon />
            </button>
            <span className="start-spacer" />
            {!touch && <span className="composer-hint">Shift+Enter for a new line</span>}
            <button type="button" className="input-send" data-empty={!draft.trim() || undefined} disabled={!draft.trim()} onClick={submit} title="Start a session with this prompt">
              <SendIcon />
            </button>
          </div>
        </div>
        {atLimit && <p className="start-note">All sessions are in use: starting one closes the Sessions session idle longest.</p>}
        {settingsOpen && <StartSettingsPanel tools={tools} />}
        <ol className="start-rows" aria-label="Started from here">
          {pending.map((p) => (
            <PendingRow key={p.key} pending={p} tools={tools} />
          ))}
          {earlier.map((r) => (
            <RecentRow key={r.session} row={r} tools={tools} />
          ))}
        </ol>
      </div>
    </div>
  );
}

function modelLabel(tools: StartTool[], tool: string | null | undefined, model: string | null | undefined): string {
  if (!model) return "default model";
  return tools.find((t) => t.name === tool)?.models.find((m) => m.id === model)?.label ?? model;
}

function SessionLink({ sessionId, title }: { sessionId: string; title: string }) {
  const select = useApp((s) => s.select);
  const live = useApp((s) => !!s.sessions[sessionId]);
  if (!live) return <strong>{title}</strong>;
  return (
    <button type="button" className="start-link" onClick={() => select(sessionId, "tab", "left")}>
      {title}
    </button>
  );
}

function Chips({ tool, model, cwd, tools }: { tool: string | null | undefined; model: string | null | undefined; cwd: string | null | undefined; tools: StartTool[] }) {
  return (
    <span className="start-chips">
      {tool && <ToolBadge tool={tool} />}
      <span className="start-chip">{modelLabel(tools, tool, model)}</span>
      {cwd && <span className="start-chip" title={cwd}>{basename(cwd)}</span>}
    </span>
  );
}

function LiveStatus({ sessionId, closedWhy }: { sessionId: string; closedWhy?: string | null }) {
  const status = useApp((s) => s.sessions[sessionId]?.status);
  if (status) return <span className="start-state" data-status={status}>{status}</span>;
  return <span className="start-state" data-status="ended">{closedWhy ? `closed: ${closedWhy}` : "ended"}</span>;
}

function RecentRow({ row, tools }: { row: StartRecent; tools: StartTool[] }) {
  return (
    <li className="start-row">
      <div className="start-line">
        <SessionLink sessionId={row.session} title={row.title || row.session} />
        <LiveStatus sessionId={row.session} closedWhy={row.closed_why} />
      </div>
      <Chips tool={row.tool} model={row.model} cwd={row.cwd} tools={tools} />
    </li>
  );
}

function PendingRow({ pending: p, tools }: { pending: Pending; tools: StartTool[] }) {
  const client = useActiveClient();
  const { edit, launch, dismiss } = useStart();
  const now = useNow(p.phase === "counting");
  const title = p.choice?.title || p.decision?.title || p.prompt.slice(0, 60);

  if (p.phase === "probing") {
    return (
      <li className="start-row" data-phase="probing">
        <div className="start-line">
          <span className="start-pulse" aria-hidden="true" />
          <span>Prompt received · working out where to run it…</span>
        </div>
        <p className="start-prompt">{p.prompt}</p>
      </li>
    );
  }
  if (p.phase === "failed" || !p.choice) {
    return (
      <li className="start-row" data-phase="failed">
        <div className="start-line">
          <span>Couldn't start: {p.error}</span>
          <button type="button" className="start-link" onClick={() => dismiss(p.key)}>Dismiss</button>
        </div>
        <p className="start-prompt">{p.prompt}</p>
      </li>
    );
  }
  const { choice, decision } = p;
  if (p.phase === "launched" && p.sessionId) {
    return (
      <li className="start-row" data-phase="launched">
        <div className="start-line">
          <span>Launched</span>
          <SessionLink sessionId={p.sessionId} title={title} />
          <LiveStatus sessionId={p.sessionId} />
        </div>
        <Chips tool={choice.tool} model={choice.model} cwd={choice.cwd} tools={tools} />
      </li>
    );
  }
  const seconds = p.deadline ? Math.max(0, Math.ceil((p.deadline - now) / 1000)) : 0;
  const editing = p.phase === "editing" || p.phase === "asking";
  return (
    <li className="start-row" data-phase={p.phase}>
      <div className="start-line">
        {p.phase === "launching" ? (
          <span>Launching <strong>{title}</strong>…</span>
        ) : p.phase === "counting" ? (
          <span>Launching <strong>{title}</strong> in {seconds}s</span>
        ) : p.phase === "asking" ? (
          <span>Which folder should <strong>{title}</strong> work in?</span>
        ) : (
          <span>Ready to launch <strong>{title}</strong></span>
        )}
      </div>
      {!editing && <Chips tool={choice.tool} model={choice.model} cwd={choice.cwd} tools={tools} />}
      {!editing && choice.prompt !== p.prompt && <FirstPrompt prompt={choice.prompt} />}
      {decision && (decision.why || decision.reason) && (
        <p className="start-why">
          {[decision.why, decision.reason].filter(Boolean).join(" — ")}
          {decision.error && <> · the probe failed ({decision.error}), so this is a guess</>}
        </p>
      )}
      {editing && <ChoiceEditor pending={p} tools={tools} />}
      {p.error && <p className="start-error" role="alert">{p.error}</p>}
      {p.phase !== "launching" && (
        <div className="start-actions">
          <button type="button" className="primary" disabled={!choice.tool || !choice.cwd} onClick={() => launch(client, p.key)}>
            {p.phase === "counting" ? "Launch now" : "Launch"}
          </button>
          {p.phase === "counting" && <button type="button" onClick={() => edit(p.key)}>Change</button>}
          <button type="button" onClick={() => dismiss(p.key)}>Cancel</button>
        </div>
      )}
    </li>
  );
}

function FirstPrompt({ prompt }: { prompt: string }) {
  if (!prompt) return <p className="start-prompt start-first">No first prompt: the session opens and waits for you.</p>;
  return (
    <p className="start-prompt start-first">
      <span className="start-first-label">First prompt</span> {prompt}
    </p>
  );
}

function ChoiceEditor({ pending: p, tools }: { pending: Pending; tools: StartTool[] }) {
  const change = useStart((s) => s.change);
  const choice = p.choice!;
  const installed = tools.filter((t) => t.installed);
  const models = installed.find((t) => t.name === choice.tool)?.models ?? [];
  const candidates = p.decision?.candidates ?? [];
  const listId = `start-dirs-${p.key}`;
  return (
    <div className="start-editor">
      {p.phase === "asking" && candidates.length > 0 && (
        <div className="start-candidates">
          {candidates.map((dir) => (
            <button key={dir} type="button" aria-pressed={choice.cwd === dir} title={dir} onClick={() => change(p.key, "cwd", dir)}>
              {basename(dir)}
            </button>
          ))}
        </div>
      )}
      <label className="field">
        Folder
        <input value={choice.cwd} list={listId} spellCheck={false} placeholder="/path/to/folder" onChange={(e) => change(p.key, "cwd", e.target.value)} />
        <datalist id={listId}>
          {candidates.map((dir) => (
            <option key={dir} value={dir} />
          ))}
        </datalist>
      </label>
      <div className="start-editor-row">
        <label className="field">
          Agent
          <select value={choice.tool} onChange={(e) => change(p.key, "tool", e.target.value)}>
            {!choice.tool && <option value="">Choose…</option>}
            {installed.map((t) => (
              <option key={t.name} value={t.name}>{t.name}</option>
            ))}
          </select>
        </label>
        <label className="field">
          Model
          <select value={choice.model ?? ""} onChange={(e) => change(p.key, "model", e.target.value || null)}>
            <option value="">Its default</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>{m.label} ({m.tier})</option>
            ))}
          </select>
        </label>
        <label className="field">
          Name
          <input value={choice.name} maxLength={32} spellCheck={false} onChange={(e) => change(p.key, "name", e.target.value)} />
        </label>
      </div>
      <label className="field">
        First prompt
        <textarea rows={3} value={choice.prompt} placeholder="None: the session opens and waits" onChange={(e) => change(p.key, "prompt", e.target.value)} />
      </label>
    </div>
  );
}

function StartSettingsPanel({ tools }: { tools: StartTool[] }) {
  const client = useActiveClient();
  const [settings, setSettings] = useState<StartSettings | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    client.startSettings().then(setSettings, (e: Error) => setError(e.message));
  }, [client]);
  const probeTools = useMemo(() => tools.filter((t) => t.installed && t.can_probe), [tools]);

  async function save(patch: Partial<StartSettings>) {
    setError("");
    try {
      setSettings(await client.saveStartSettings(patch));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  if (!settings) return error ? <p className="start-error">{error}</p> : null;
  const probe = tools.find((t) => t.name === settings.probe_tool);
  const move = (at: number, by: number) => {
    const order = settings.tool_order.slice();
    const [tool] = order.splice(at, 1);
    order.splice(at + by, 0, tool);
    void save({ tool_order: order });
  };
  return (
    <section className="start-settings" aria-label="How agentop chooses">
      <div className="start-editor-row">
        <label className="field">
          Probe agent
          <select value={settings.probe_tool} onChange={(e) => void save({ probe_tool: e.target.value, probe_model: null })}>
            {probeTools.map((t) => (
              <option key={t.name} value={t.name}>{t.name}</option>
            ))}
          </select>
        </label>
        <label className="field">
          Probe model
          <select value={settings.probe_model ?? ""} onChange={(e) => void save({ probe_model: e.target.value || null })}>
            <option value="">{probe?.models.find((m) => m.id === probe.ask_model)?.label ?? probe?.ask_model ?? "Its default"}</option>
            {probe?.models.filter((m) => m.id !== probe.ask_model).map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
        </label>
        <label className="field">
          Close after idle (hours, 0 = never)
          <input type="number" min={0} step={1} defaultValue={settings.idle_hours} onBlur={(e) => void save({ idle_hours: Number(e.target.value) || 0 })} />
        </label>
      </div>
      <div className="start-order">
        <span className="start-order-title">Order, when quota doesn't decide</span>
        <ol>
          {settings.tool_order.map((name, at) => {
            const tool = tools.find((t) => t.name === name);
            return (
              <li key={name} data-installed={tool?.installed || undefined}>
                <ToolBadge tool={name} />
                <span className="start-quota">{tool?.installed ? quotaText(tool) : "not installed"}</span>
                <button type="button" disabled={at === 0} onClick={() => move(at, -1)} aria-label={`Move ${name} up`}>↑</button>
                <button type="button" disabled={at === settings.tool_order.length - 1} onClick={() => move(at, 1)} aria-label={`Move ${name} down`}>↓</button>
              </li>
            );
          })}
        </ol>
      </div>
      {error && <p className="start-error" role="alert">{error}</p>}
    </section>
  );
}

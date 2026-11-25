import { useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent, type PointerEvent } from "react";
import type { Session } from "../../api/client";
import type { PluginContext, TerminalInputControl, UiPlugin } from "../types";
import { afterPrefix, KEYS, loadDraft, modeStore, saveDraft } from "./state";

const MAX_HEIGHT = 160;
const ERROR_MS = 5000;
const NOTE_MS = 4000;

/** Reception's answer on a prompt: `pass` means no model was asked. */
interface Verdict {
  id: string | null;
  verdict: "pass" | "keep" | "clear";
  reason: string;
  by: string | null;
}

type Phase =
  | { name: "idle" }
  | { name: "checking"; text: string }
  | { name: "ask"; text: string; check: Verdict }
  | { name: "clearing" };

/** Buttons keep the focus where it is, so a phone's keyboard stays up between a key and the next line. */
const keepFocus = (event: PointerEvent) => event.preventDefault();

/** The keys that start a new group in the toolbar: Esc | arrows and Enter | Tab | numbers. */
const GROUP_STARTS = new Set(["up", "tab", "1"]);

/** A touch screen types a new line on Enter, as phone apps do; a keyboard sends on Enter, as Slack does. */
const touchScreen = () => {
  try {
    return window.matchMedia("(pointer: coarse)").matches;
  } catch {
    return false;
  }
};

function SendIcon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path d="M3.4 16.6 17.5 10 3.4 3.4l.1 5.1L13 10l-9.5 1.5z" fill="currentColor" />
    </svg>
  );
}

function EnterIcon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path d="M15.5 4.5v5.5a2 2 0 0 1-2 2H5m3.5-3.5L5 12l3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ReceptionIcon() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
      <path d="M4 14.5a6 6 0 0 1 12 0M2.5 14.5h15M10 8.5V7m-1.5 0h3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ClearIcon() {
  return (
    <svg viewBox="0 0 20 20" width="15" height="15" aria-hidden="true">
      <path d="M10 3.5v2M5.4 5.4l1.4 1.4M3.5 10h2m8.7-3.2 1.4-1.4M14.5 10h2M7 13.5l6-6M9 15.5h6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function KeyboardIcon() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
      <rect x="2.5" y="5" width="15" height="10" rx="2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M5.5 8h1m2.5 0h1m2.5 0h1m-8 4h7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

/**
 * A box below the terminal to type prompts in, with buttons for the keys a
 * phone lacks. While it shows, the terminal above is only watched.
 */
function InputBar({ session, plugin, terminal }: { session: Session; plugin: PluginContext; terminal: TerminalInputControl }) {
  const mode = useSyncExternalStore(modeStore.subscribe, modeStore.snapshot);
  // Only a running session agentop started takes prompts. Any other is typed into as before.
  const usable = session.managed && !session.readonly && session.status === "running";
  const active = usable && mode === "bar";
  const [text, setText] = useState(() => loadDraft(session.id));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // Reception: null until the service says whether it is on for this session.
  const [reception, setReception] = useState<boolean | null>(null);
  const [phase, setPhase] = useState<Phase>({ name: "idle" });
  // Bumped when a check is skipped or dropped, so its late answer is ignored.
  const checkRun = useRef(0);
  const primary = useRef<HTMLButtonElement>(null);
  // ^B pressed: the next text or key goes to tmux after its prefix, through the terminal.
  const [prefix, setPrefix] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const [touch] = useState(touchScreen);

  useEffect(() => {
    terminal.takeInput(active, () => box.current?.focus({ preventScroll: true }));
  }, [active, terminal]);

  useEffect(() => {
    if (!error) return;
    const timer = window.setTimeout(() => setError(null), ERROR_MS);
    return () => window.clearTimeout(timer);
  }, [error]);

  useEffect(() => {
    if (!note) return;
    const timer = window.setTimeout(() => setNote(null), NOTE_MS);
    return () => window.clearTimeout(timer);
  }, [note]);

  const path = `/sessions/${encodeURIComponent(session.id)}`;
  useEffect(() => {
    if (!usable) return;
    let live = true;
    plugin.client
      .get<{ enabled: boolean }>(`${path}/reception`)
      .then((answer) => live && setReception(answer.enabled))
      .catch(() => live && setReception(false));
    return () => {
      live = false;
    };
  }, [usable, path, plugin.client]);

  useEffect(() => {
    if (phase.name === "ask" && !touch) primary.current?.focus({ preventScroll: true });
  }, [phase, touch]);

  useEffect(() => {
    const element = box.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT)}px`;
  }, [text, active]);

  if (!usable) return null;
  if (!active) {
    return (
      <div className="input-bar" data-mode="terminal">
        <button type="button" className="input-toggle" onClick={() => modeStore.set("bar")} title="Type prompts in a box below the terminal (Ctrl+Shift+M)">
          <KeyboardIcon />
          Input bar
        </button>
      </div>
    );
  }

  const run = async (request: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await request();
      setError(null);
      return true;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const edit = (value: string) => {
    setText(value);
    saveDraft(session.id, value);
    // Changing the prompt answers the question differently: back to editing.
    if (phase.name === "ask") setPhase({ name: "idle" });
  };
  const clear = (sent: string) =>
    setText((now) => {
      // Keep anything typed while it was sending.
      const rest = now === sent ? "" : now;
      saveDraft(session.id, rest);
      return rest;
    });
  const typeAfterPrefix = (data: string) => {
    setPrefix(false);
    if (terminal.type(afterPrefix(data))) return true;
    setError("The terminal isn't connected");
    return false;
  };
  const deliver = async (sent: string, options: { clear?: boolean; check?: string | null } = {}) => {
    if (options.clear) setPhase({ name: "clearing" });
    const body = { text: sent, clear: options.clear ?? false, check: options.check ?? null };
    const ok = await run(() => plugin.client.post(`${path}/prompt`, body));
    setPhase({ name: "idle" });
    if (!ok) return;
    clear(sent);
    if (options.clear) setNote("Context cleared, then sent");
  };
  const send = async () => {
    if (busy || phase.name !== "idle") return;
    const sent = text;
    if (prefix) {
      if (!sent) setPrefix(false);
      else if (typeAfterPrefix(sent)) clear(sent);
      return;
    }
    if (!reception || !sent.trim()) {
      await deliver(sent);
      return;
    }
    const mine = ++checkRun.current;
    setPhase({ name: "checking", text: sent });
    let verdict: Verdict | null = null;
    try {
      verdict = await plugin.client.post<Verdict>(`${path}/check`, { text: sent });
    } catch {
      // A failed check never holds a prompt back.
    }
    if (checkRun.current !== mine) return;
    if (verdict?.verdict === "clear") {
      setPhase({ name: "ask", text: sent, check: verdict });
      return;
    }
    await deliver(sent, { check: verdict?.id });
  };
  const sendNow = () => {
    if (phase.name !== "checking") return;
    checkRun.current++;
    void deliver(phase.text);
  };
  const answer = (clearFirst: boolean) => {
    if (phase.name !== "ask") return;
    void deliver(phase.text, { clear: clearFirst, check: phase.check.id });
  };
  const keepEditing = () => {
    checkRun.current++;
    setPhase({ name: "idle" });
    box.current?.focus({ preventScroll: true });
  };
  const toggleReception = async () => {
    const next = !reception;
    setReception(next);
    try {
      const saved = await plugin.client.patch<{ enabled: boolean }>(`${path}/reception`, { enabled: next });
      setReception(saved.enabled);
      setNote(saved.enabled ? "Reception on: prompts that look like new work offer a clear first" : "Reception off");
    } catch (failure) {
      setReception(!next);
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };
  const press = (key: string, bytes: string) => {
    if (prefix) typeAfterPrefix(bytes);
    else void run(() => plugin.client.post(`${path}/keys`, { keys: [key] }));
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (held) {
      if (event.key === "Enter") event.preventDefault();
      return;
    }
    if (event.key !== "Enter" || event.nativeEvent.isComposing || event.keyCode === 229) return;
    // Ctrl or Cmd with Enter always sends; plain Enter sends on a keyboard, and Shift+Enter is a new line.
    if (event.ctrlKey || event.metaKey || (!touch && !event.shiftKey && !event.altKey)) {
      event.preventDefault();
      void send();
    }
  };
  const empty = !text.trim();
  const sendLabel = prefix ? "Send to tmux" : empty ? "Press Enter" : "Send";
  const held = phase.name !== "idle";

  return (
    <div className="input-bar" data-mode="bar">
      <form
        className="composer"
        data-prefix={prefix || undefined}
        data-reception={held ? phase.name : undefined}
        onKeyDown={(event) => {
          // Esc from the box or a reception button: back to editing the prompt.
          if (event.key === "Escape" && (phase.name === "checking" || phase.name === "ask")) {
            event.preventDefault();
            keepEditing();
          }
        }}
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        {error && (
          <p className="input-error" role="alert">
            {error}
          </p>
        )}
        {!error && note && !held && (
          <p className="input-note" role="status">
            {note}
          </p>
        )}
        {(phase.name === "checking" || phase.name === "clearing") && (
          <div className="reception" data-phase={phase.name} role="status">
            <span className="reception-pulse" aria-hidden="true" />
            <span className="reception-status">
              {phase.name === "checking" ? "Checking it against the current work…" : "Clearing the context, then sending…"}
            </span>
            {phase.name === "checking" && (
              <button type="button" className="reception-link" onPointerDown={keepFocus} onClick={sendNow}>
                Send now
              </button>
            )}
          </div>
        )}
        {phase.name === "ask" && (
          <div className="reception" data-phase="ask" role="alert">
            <div className="reception-message">
              <strong>This looks like new work</strong>
              {phase.check.reason && <span>{phase.check.reason}</span>}
            </div>
            <div className="reception-actions">
              <button ref={primary} type="button" className="reception-primary" onClick={() => answer(true)}>
                <ClearIcon />
                Clear context &amp; send
              </button>
              <button type="button" className="reception-secondary" onClick={() => answer(false)}>
                Send as is
              </button>
              <button type="button" className="reception-link" onClick={keepEditing} title="Back to the prompt (Esc)">
                Keep editing
              </button>
            </div>
          </div>
        )}
        <textarea
          ref={box}
          rows={1}
          value={text}
          aria-label={`Prompt for ${session.display_name}`}
          placeholder={prefix ? "Key for tmux, such as c for a new window" : `Message ${session.display_name}`}
          readOnly={phase.name === "checking" || phase.name === "clearing"}
          enterKeyHint={touch ? "enter" : "send"}
          onChange={(event) => edit(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="composer-tools">
          <div className="input-key-list" role="group" aria-label="Keys">
            <button
              type="button"
              className="input-prefix"
              aria-label="tmux prefix, Control B"
              title="tmux prefix (Ctrl-B): the next text or key goes to tmux, such as c for a new window"
              aria-pressed={prefix}
              onPointerDown={keepFocus}
              onClick={() => setPrefix(!prefix)}
            >
              ^B
            </button>
            {KEYS.map(([key, label, name, bytes]) => (
              <button
                key={key}
                type="button"
                data-group-start={GROUP_STARTS.has(key) || undefined}
                aria-label={name}
                title={name}
                disabled={busy}
                onPointerDown={keepFocus}
                onClick={() => press(key, bytes)}
              >
                {label}
              </button>
            ))}
          </div>
          {!touch && <span className="composer-hint">{prefix ? "Enter types it after ^B" : "Shift+Enter for a new line"}</span>}
          {reception !== null && (
            <button
              type="button"
              className="input-reception"
              aria-pressed={reception}
              aria-label="Reception"
              title={
                reception
                  ? "Reception is on: a prompt that looks like new work offers to clear the context first. Click to turn it off"
                  : "Reception is off. Turn it on to check each prompt against the current work and offer to clear the context first"
              }
              onPointerDown={keepFocus}
              onClick={() => void toggleReception()}
            >
              <ReceptionIcon />
            </button>
          )}
          <button
            type="button"
            className="input-toggle"
            aria-label="Type in the terminal"
            title="Type in the terminal instead (Ctrl+Shift+M)"
            onClick={() => modeStore.set("terminal")}
          >
            <KeyboardIcon />
          </button>
          <button
            type="submit"
            className="input-send"
            data-empty={(empty && !prefix) || undefined}
            aria-label={sendLabel}
            title={touch ? sendLabel : `${sendLabel} (Enter)`}
            disabled={busy || held}
            onPointerDown={keepFocus}
          >
            {empty && !prefix ? <EnterIcon /> : <SendIcon />}
          </button>
        </div>
      </form>
    </div>
  );
}

export const plugin: UiPlugin = {
  terminalInput: InputBar,
};

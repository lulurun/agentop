import { useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent, type PointerEvent } from "react";
import type { Session } from "../../api/client";
import type { PluginContext, TerminalInputControl, UiPlugin } from "../types";
import { afterPrefix, KEYS, loadDraft, modeStore, saveDraft } from "./state";

const MAX_HEIGHT = 160;
const ERROR_MS = 5000;

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
    const element = box.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT)}px`;
  }, [text, active]);

  if (!usable) return null;
  if (!active) {
    return (
      <div className="input-bar" data-mode="terminal">
        <button type="button" className="input-toggle" onClick={() => modeStore.set("bar")} title="Type prompts in a box below the terminal">
          <KeyboardIcon />
          Input bar
        </button>
      </div>
    );
  }

  const path = `/sessions/${encodeURIComponent(session.id)}`;
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
  const send = async () => {
    if (busy) return;
    const sent = text;
    if (prefix) {
      if (!sent) setPrefix(false);
      else if (typeAfterPrefix(sent)) clear(sent);
      return;
    }
    if (await run(() => plugin.client.post(`${path}/prompt`, { text: sent }))) clear(sent);
  };
  const press = (key: string, bytes: string) => {
    if (prefix) typeAfterPrefix(bytes);
    else void run(() => plugin.client.post(`${path}/keys`, { keys: [key] }));
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing || event.keyCode === 229) return;
    // Ctrl or Cmd with Enter always sends; plain Enter sends on a keyboard, and Shift+Enter is a new line.
    if (event.ctrlKey || event.metaKey || (!touch && !event.shiftKey && !event.altKey)) {
      event.preventDefault();
      void send();
    }
  };
  const empty = !text.trim();
  const sendLabel = prefix ? "Send to tmux" : empty ? "Press Enter" : "Send";

  return (
    <div className="input-bar" data-mode="bar">
      <form
        className="composer"
        data-prefix={prefix || undefined}
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
        <textarea
          ref={box}
          rows={1}
          value={text}
          aria-label={`Prompt for ${session.display_name}`}
          placeholder={prefix ? "Key for tmux, such as c for a new window" : `Message ${session.display_name}`}
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
          <button
            type="button"
            className="input-toggle"
            aria-label="Type in the terminal"
            title="Type in the terminal instead"
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
            disabled={busy}
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

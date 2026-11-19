import { useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent, type PointerEvent } from "react";
import type { Session } from "../../api/client";
import type { PluginContext, TerminalInputControl, UiPlugin } from "../types";
import { afterPrefix, KEYS, loadDraft, modeStore, saveDraft } from "./state";

const MAX_HEIGHT = 160;
const ERROR_MS = 5000;

/** Buttons keep the focus where it is, so a phone's keyboard stays up between a key and the next line. */
const keepFocus = (event: PointerEvent) => event.preventDefault();

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

  useEffect(() => {
    terminal.takeInput(active);
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
          ⌨ Input bar
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
    // Enter is a new line, as on a phone; Ctrl or Cmd with Enter sends.
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      void send();
    }
  };

  return (
    <div className="input-bar" data-mode="bar">
      <div className="input-keys">
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
            <button key={key} type="button" aria-label={name} title={name} disabled={busy} onPointerDown={keepFocus} onClick={() => press(key, bytes)}>
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="input-toggle"
          aria-label="Type in the terminal"
          title="Type in the terminal instead"
          onClick={() => modeStore.set("terminal")}
        >
          ⌨
        </button>
      </div>
      {error && (
        <p className="input-error" role="alert">
          {error}
        </p>
      )}
      <form
        className="input-row"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <textarea
          ref={box}
          rows={1}
          value={text}
          aria-label={`Prompt for ${session.display_name}`}
          placeholder={prefix ? "Key for tmux, such as c" : "Prompt, or empty to press Enter"}
          enterKeyHint="enter"
          onChange={(event) => edit(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" className="input-send" aria-label="Send" title="Send (Ctrl+Enter)" disabled={busy} onPointerDown={keepFocus}>
          ↑
        </button>
      </form>
    </div>
  );
}

export const plugin: UiPlugin = {
  terminalInput: InputBar,
};

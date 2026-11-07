import { ClipboardAddon } from "@xterm/addon-clipboard";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { WebglAddon } from "@xterm/addon-webgl";
import { Terminal, type ITheme } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";
import { ApiError, type ServiceClient } from "../api/client";
import { isAppShortcut } from "../shortcuts";
import { platform } from "../platform";
import { loadSettings } from "../settings";

type Status = "connecting" | "live" | "reconnecting" | "detached" | "ended";

const RETRY_MS = { first: 500, max: 10_000 };
const WS_NO_SESSION = 4404;

function themeFromTokens(): ITheme {
  const style = getComputedStyle(document.documentElement);
  const token = (name: string) => style.getPropertyValue(name).trim();
  return {
    background: token("--term-bg"),
    foreground: token("--term-fg"),
    cursor: token("--term-fg"),
    selectionBackground: token("--term-selection"),
  };
}

function loadRenderer(term: Terminal) {
  const setting = loadSettings().renderer;
  if (setting === "dom") return;
  try {
    const webgl = new WebglAddon();
    webgl.onContextLoss(() => webgl.dispose()); // falls back to the DOM renderer
    term.loadAddon(webgl);
  } catch {
    // No WebGL here. The DOM renderer takes over.
  }
}

interface Props {
  client: ServiceClient;
  sessionId: string;
  focusRequest: number;
}

/** The only code that touches xterm, so the terminal library can be replaced in one place. */
export function TerminalView({ client, sessionId, focusRequest }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const search = useRef<SearchAddon | null>(null);
  const reattach = useRef<() => void>(() => {});
  const [status, setStatus] = useState<Status>("connecting");
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let retryDelay = RETRY_MS.first;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const terminal = new Terminal({
      fontFamily: "'JetBrains Mono', ui-monospace, monospace",
      fontSize: loadSettings().fontSize,
      lineHeight: 1.2,
      cursorBlink: true,
      allowProposedApi: true, // unicode11 needs it
      scrollback: 2000, // history lives in tmux
      theme: themeFromTokens(),
    });
    term.current = terminal;
    const fit = new FitAddon();
    const searchAddon = new SearchAddon();
    search.current = searchAddon;
    terminal.loadAddon(fit);
    terminal.loadAddon(searchAddon);
    terminal.loadAddon(new Unicode11Addon());
    terminal.unicode.activeVersion = "11";
    terminal.loadAddon(new ClipboardAddon());
    terminal.loadAddon(new WebLinksAddon((_event, uri) => window.open(uri, "_blank", "noopener")));
    terminal.open(host.current!);
    loadRenderer(terminal);
    fit.fit();
    terminal.focus();

    terminal.attachCustomKeyEventHandler((event) => {
      if (isAppShortcut(event)) return false;
      const copy = event.type === "keydown" && event.ctrlKey && event.shiftKey && event.code === "KeyC";
      if (copy && terminal.hasSelection()) {
        event.preventDefault();
        void platform.copyText(terminal.getSelection());
        return false;
      }
      return true;
    });

    const send = (data: string | Uint8Array<ArrayBuffer>) => {
      if (ws?.readyState === WebSocket.OPEN) ws.send(typeof data === "string" ? new TextEncoder().encode(data) : data);
    };
    const inputs = [
      terminal.onData(send),
      terminal.onBinary((data) => send(Uint8Array.from(data, (c) => c.charCodeAt(0)))),
      terminal.onResize(({ cols, rows }) => {
        if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "resize", cols, rows }));
      }),
    ];

    function scheduleRetry() {
      if (disposed) return;
      setStatus("reconnecting");
      retryTimer = setTimeout(() => void attach(), retryDelay);
      retryDelay = Math.min(retryDelay * 2, RETRY_MS.max);
    }

    async function attach() {
      if (disposed) return;
      let url: string;
      try {
        url = await client.terminalUrl(sessionId, terminal.cols, terminal.rows);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return;
        scheduleRetry();
        return;
      }
      if (disposed) return;

      const socket = new WebSocket(url);
      ws = socket;
      socket.binaryType = "arraybuffer";
      let closing: Status | null = null;

      socket.onopen = () => {
        retryDelay = RETRY_MS.first;
        terminal.reset(); // tmux repaints the whole screen on attach
        setStatus("live");
      };
      socket.onmessage = (message) => {
        if (typeof message.data === "string") {
          const control = JSON.parse(message.data);
          if (control.type === "closed") closing = control.reason === "detached" ? "detached" : "ended";
        } else {
          terminal.write(new Uint8Array(message.data));
        }
      };
      socket.onclose = (event) => {
        if (disposed) return;
        if (closing) setStatus(closing);
        else if (event.code === WS_NO_SESSION) setStatus("ended");
        else scheduleRetry(); // includes WS_INVALID_TICKET: the next try gets a new ticket
      };
    }

    reattach.current = () => {
      setStatus("connecting");
      void attach();
    };

    let fitFrame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(fitFrame);
      fitFrame = requestAnimationFrame(() => {
        if (!disposed && host.current?.offsetWidth) fit.fit();
      });
    });
    observer.observe(host.current!);

    void document.fonts.ready.then(() => {
      if (disposed) return;
      fit.fit();
      void attach();
    });

    return () => {
      disposed = true;
      clearTimeout(retryTimer);
      cancelAnimationFrame(fitFrame);
      observer.disconnect();
      inputs.forEach((input) => input.dispose());
      ws?.close();
      terminal.dispose();
      term.current = null;
    };
  }, [client, sessionId]);

  useEffect(() => {
    if (focusRequest) term.current?.focus();
  }, [focusRequest]);

  useEffect(() => {
    const open = () => setSearching(true);
    window.addEventListener("agentop:terminal-search", open);
    return () => window.removeEventListener("agentop:terminal-search", open);
  }, []);

  return (
    <div className="terminal" data-status={status}>
      <div className="terminal-host" ref={host} data-testid="terminal" />
      {searching && (
        <form
          className="terminal-search"
          onSubmit={(event) => {
            event.preventDefault();
            search.current?.findNext(query);
          }}
        >
          <input
            autoFocus
            aria-label="Search terminal"
            placeholder="Search this screen"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                search.current?.clearDecorations();
                setSearching(false);
                term.current?.focus();
              }
            }}
          />
        </form>
      )}
      {status === "reconnecting" && <div className="terminal-note">Reconnecting…</div>}
      {status === "detached" && (
        <div className="terminal-note">
          Detached from tmux. The session is still running.
          <button type="button" onClick={() => reattach.current()}>
            Attach again
          </button>
        </div>
      )}
      {status === "ended" && <div className="terminal-note">This tmux session has ended.</div>}
    </div>
  );
}

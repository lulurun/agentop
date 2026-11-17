import { ClipboardAddon } from "@xterm/addon-clipboard";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import { Unicode11Addon } from "@xterm/addon-unicode11";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { WebglAddon } from "@xterm/addon-webgl";
import { Terminal, type ITheme } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { ApiError, type ServiceClient } from "../api/client";
import { isAppShortcut } from "../shortcuts";
import { platform } from "../platform";
import { loadSettings } from "../settings";
import { terminalClipboard } from "./clipboard";
import { isMouseReport } from "./mouse";
import { touchScroll } from "./touchScroll";

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

let cellRatio: number | null = null;

/** A cell's width per pixel of font size, measured once. */
function cellWidthRatio(): number {
  if (cellRatio === null) {
    try {
      const context = document.createElement("canvas").getContext("2d");
      if (context) {
        context.font = "100px 'JetBrains Mono', ui-monospace, monospace";
        cellRatio = context.measureText("M").width / 100 || 0.6;
      }
    } catch {
      // No canvas: the usual ratio of a monospace font.
    }
    cellRatio ??= 0.6;
  }
  return cellRatio;
}

/** The font size that shows a window of `cols` × `rows` whole in `host`, no larger than `max`. */
export function fittingFontSize(width: number, height: number, cols: number, rows: number, max: number, ratio = cellWidthRatio()): number {
  const size = Math.min(width / (cols * ratio), height / (rows * 1.2), max);
  return Math.max(6, Math.floor(size * 2) / 2);
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

/** Whether tapping the terminal raises a phone's keyboard: not while it isn't typed into. */
function keyboardFor(terminal: Terminal, typing: boolean) {
  const textarea = terminal.textarea;
  if (!textarea) return;
  textarea.readOnly = !typing;
  if (typing) textarea.removeAttribute("inputmode");
  else textarea.setAttribute("inputmode", "none");
}

interface Props {
  client: ServiceClient;
  sessionId: string;
  focusRequest: number;
  /** Shown but not typed into: input is dropped and the overlay covers the screen. */
  locked?: boolean;
  overlay?: ReactNode;
  /** Whether the search shortcut opens this terminal's search, and the terminal takes the keyboard when it opens. Off for a terminal in a group without focus. */
  searchable?: boolean;
  /** Watched only: nothing typed is sent, it never takes the keyboard, and tmux keeps the window's size. Fixed for the terminal's life. */
  readOnly?: boolean;
  /**
   * False while something else takes the input, such as an input bar below:
   * keys are dropped and a tap doesn't raise a phone's keyboard, but the terminal
   * still takes the tab's size and scrolls tmux. It can change at any time.
   */
  typing?: boolean;
  /**
   * Set to a function that types into the terminal as its keyboard would, through
   * tmux's client, so tmux's own keys (its prefix) work. It types even while
   * `typing` is off, and returns false when the terminal isn't connected.
   */
  keyboard?: MutableRefObject<((data: string) => boolean) | null>;
}

/** The only code that touches xterm, so the terminal library can be replaced in one place. */
export function TerminalView({ client, sessionId, focusRequest, locked = false, overlay, searchable = true, readOnly = false, typing = true, keyboard }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const lockedRef = useRef(locked || readOnly);
  const searchableRef = useRef(searchable);
  searchableRef.current = searchable;
  const typingRef = useRef(typing);
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
      cursorBlink: !readOnly,
      allowProposedApi: true, // unicode11 needs it
      scrollback: 2000, // history lives in tmux
      theme: themeFromTokens(),
      // tmux has the mouse, so a plain drag copies in tmux. Shift-drag, or Option-drag on
      // a Mac, selects in the page instead, for Ctrl+Shift+C.
      macOptionClickForcesSelection: true,
    });
    term.current = terminal;
    const fit = new FitAddon();
    // A read-only terminal shows the window at the size tmux keeps it (sent in a "size" frame),
    // with its font scaled to fit, instead of fitting its columns to the space.
    let windowSize: { cols: number; rows: number } | null = null;
    const fitWindow = () => {
      const box = host.current;
      if (!windowSize || !box?.offsetWidth) return;
      const fontSize = fittingFontSize(box.clientWidth, box.clientHeight, windowSize.cols, windowSize.rows, loadSettings().fontSize);
      if (terminal.options.fontSize !== fontSize) terminal.options.fontSize = fontSize;
      if (terminal.cols !== windowSize.cols || terminal.rows !== windowSize.rows) terminal.resize(windowSize.cols, windowSize.rows);
    };
    const searchAddon = new SearchAddon();
    search.current = searchAddon;
    terminal.loadAddon(fit);
    terminal.loadAddon(searchAddon);
    terminal.loadAddon(new Unicode11Addon());
    terminal.unicode.activeVersion = "11";
    terminal.loadAddon(new ClipboardAddon(undefined, terminalClipboard(platform.copyText)));
    terminal.loadAddon(new WebLinksAddon((_event, uri) => window.open(uri, "_blank", "noopener")));
    terminal.open(host.current!);
    loadRenderer(terminal);
    fit.fit();
    terminal.options.disableStdin = lockedRef.current;
    keyboardFor(terminal, typingRef.current);
    if (!lockedRef.current && typingRef.current && searchableRef.current) terminal.focus();

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
      if (lockedRef.current) return;
      if (!typingRef.current && !isMouseReport(typeof data === "string" ? data : String.fromCharCode(...data))) return;
      if (ws?.readyState === WebSocket.OPEN) ws.send(typeof data === "string" ? new TextEncoder().encode(data) : data);
    };
    if (keyboard) {
      keyboard.current = (data) => {
        if (lockedRef.current || readOnly || ws?.readyState !== WebSocket.OPEN) return false;
        ws.send(new TextEncoder().encode(data));
        return true;
      };
    }
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
        url = await client.terminalUrl(sessionId, terminal.cols, terminal.rows, readOnly);
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
          if (control.type === "size" && readOnly) {
            windowSize = { cols: control.cols, rows: control.rows };
            fitWindow();
          }
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
        if (disposed || !host.current?.offsetWidth) return;
        if (windowSize) fitWindow();
        else fit.fit();
      });
    });
    observer.observe(host.current!);
    const stopTouchScroll = touchScroll(host.current!);

    void document.fonts.ready.then(() => {
      if (disposed) return;
      fit.fit();
      void attach();
    });

    return () => {
      disposed = true;
      if (keyboard) keyboard.current = null;
      clearTimeout(retryTimer);
      cancelAnimationFrame(fitFrame);
      observer.disconnect();
      stopTouchScroll();
      inputs.forEach((input) => input.dispose());
      ws?.close();
      terminal.dispose();
      term.current = null;
    };
  }, [client, sessionId, readOnly]);

  useEffect(() => {
    if (focusRequest && !lockedRef.current) term.current?.focus();
  }, [focusRequest]);

  useEffect(() => {
    lockedRef.current = locked || readOnly;
    const terminal = term.current;
    if (!terminal) return;
    terminal.options.disableStdin = locked || readOnly;
    if (locked) {
      terminal.blur();
      setSearching(false);
    }
  }, [locked, readOnly]);

  useEffect(() => {
    typingRef.current = typing;
    const terminal = term.current;
    if (!terminal) return;
    keyboardFor(terminal, typing);
    if (!typing) terminal.blur();
  }, [typing]);

  useEffect(() => {
    if (!searchable) return;
    const open = () => setSearching(true);
    window.addEventListener("agentop:terminal-search", open);
    return () => window.removeEventListener("agentop:terminal-search", open);
  }, [searchable]);

  return (
    <div className="terminal" data-status={status} data-locked={locked || undefined} data-readonly={readOnly || undefined} data-typing={typing ? undefined : "off"}>
      <div className="terminal-host" ref={host} data-testid="terminal" inert={locked} />
      {locked && overlay}
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
      {status === "reconnecting" && !locked && <div className="terminal-note">Reconnecting…</div>}
      {status === "detached" && !locked && (
        <div className="terminal-note">
          Detached from tmux. The session is still running.
          <button type="button" onClick={() => reattach.current()}>
            Attach again
          </button>
        </div>
      )}
      {status === "ended" && !locked && <div className="terminal-note">This tmux session has ended.</div>}
    </div>
  );
}

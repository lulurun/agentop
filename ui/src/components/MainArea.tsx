import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Session } from "../api/client";
import { connectionName, failureMessage, tokenFromInput, type ConnectFailure } from "../connections";
import { platform } from "../platform";
import { LoginMethods, useLoginMethods } from "../plugins/Login";
import { PluginPanel, TerminalInputs } from "../plugins/Slot";
import { activeConnection, currentFeature, ownerOf, selectedId, useActiveClient, useApp, type AppState } from "../store";
import { TerminalView } from "../terminal/TerminalView";
import { pluginOf } from "../features";
import { Workbench, type SessionPaneProps } from "./Workbench";

type Selection = Pick<AppState, "sessions" | "selected" | "activeId" | "features" | "plugins" | "pluginsLoaded" | "scopes">;

export function selectedSession(state: Selection): Session | undefined {
  const id = selectedId(state);
  return id ? state.sessions[id] : undefined;
}

const STEPS = [
  ["creating", "Create tmux session"],
  ["launching", "Launch the agent"],
  ["waiting", "Wait for it to start"],
  ["confirming", "Finish startup"],
] as const;

function Starting({ session }: { session: Session }) {
  const current = STEPS.findIndex(([step]) => step === session.step);
  return (
    <div className="placard">
      <h2>Starting {session.display_name}</h2>
      <ol className="steps" aria-live="polite">
        {STEPS.map(([step, label], index) => (
          <li key={step} data-state={index < current ? "done" : index === current ? "active" : "todo"}>
            {label}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** With another way to sign in on the screen, the service token is no longer the only thing to ask for. */
function signInMessage(failure: ConnectFailure, label: string, otherWays: boolean): { title: string; detail: string } {
  if (otherWays && failure.kind === "no_token") return { title: `Sign in to ${label}`, detail: "" };
  if (otherWays && failure.kind === "expired") {
    return { title: `Signed out of ${label}`, detail: "This app's access ran out after a while unused, or was revoked. Sign in again." };
  }
  return failureMessage(failure, label);
}

function ConnectionProblem() {
  const link = useApp((s) => s.link);
  const connection = useApp(activeConnection);
  const retry = useApp((s) => s.retry);
  const setToken = useApp((s) => s.setToken);
  const setSwitcherOpen = useApp((s) => s.setSwitcherOpen);
  const removeConnection = useApp((s) => s.removeConnection);
  const [token, setTokenText] = useState("");
  const methods = useLoginMethods(connection.url);
  // "", "starting", or why starting failed.
  const [starting, setStarting] = useState("");

  if (link.state === "connecting") {
    return (
      <div className="placard">
        <h2>Connecting to {connectionName(connection)}</h2>
      </div>
    );
  }
  if (link.state !== "failed") return null;
  const down = link.failure.kind === "unreachable" || link.failure.kind === "stopped";
  // A page can't start a service: a stopped one has nothing to answer it. The desktop app
  // runs this computer's service, so it can start it again.
  const canStart = down && connection.isHome && !!platform.startService;
  const start = async () => {
    setStarting("starting");
    try {
      await platform.startService!();
      setStarting("");
      retry();
    } catch (error) {
      setStarting(error instanceof Error ? error.message : String(error));
    }
  };
  const { title, detail } = canStart
    ? {
        title: "The service on this computer stopped",
        detail:
          starting && starting !== "starting"
            ? `It didn't start: ${starting}`
            : "Start it again. Sessions kept running in tmux while it was stopped.",
      }
    : signInMessage(link.failure, connectionName(connection), methods.length > 0);
  const needsToken = ["no_token", "expired", "token_rejected", "rate_limited"].includes(link.failure.kind);
  return (
    <div className="placard">
      <h2 className="placard-title">
        {down && <span className="link-dot link-failed" aria-hidden="true" />}
        {title}
      </h2>
      {detail && <p>{detail}</p>}
      {needsToken && methods.length > 0 && (
        <>
          <LoginMethods url={connection.url} methods={methods} />
          <p className="login-or">Or paste the service token:</p>
        </>
      )}
      {needsToken && (
        <form
          className="token-form"
          onSubmit={(event) => {
            event.preventDefault();
            const value = tokenFromInput(token);
            if (value) setToken(value);
          }}
        >
          <input
            type="text" spellCheck={false} autoCapitalize="off"
            aria-label="Token"
            placeholder="Paste the service token"
            autoComplete="off"
            value={token}
            onChange={(event) => setTokenText(event.target.value)}
          />
          <button type="submit" className="primary">
            Connect
          </button>
        </form>
      )}
      <div className="placard-actions">
        {canStart && (
          <button type="button" className="primary" disabled={starting === "starting"} onClick={() => void start()}>
            {starting === "starting" ? "Starting…" : "Start"}
          </button>
        )}
        {!needsToken && !canStart && (
          <button type="button" onClick={() => retry()}>
            Try again
          </button>
        )}
        <button type="button" onClick={() => setSwitcherOpen(true)}>
          Switch service
        </button>
        {!connection.isHome && (
          <button type="button" title="Remove it from this app. Its sessions keep running." onClick={() => removeConnection(connection.id)}>
            Forget
          </button>
        )}
      </div>
    </div>
  );
}

function useReplace(session: Session) {
  const act = useApp((s) => s.act);
  return async function replaceWith(resume: boolean) {
    const owner = ownerOf(useApp.getState());
    const created = await act((client) =>
      client.create({
        tool: session.tool!,
        cwd: session.cwd,
        name: session.display_name.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 32) || null,
        project: session.project ?? null,
        resume: resume ? { session_id: session.session_id! } : null,
      }),
    );
    // Another service by now: its client would be the wrong one to close this session with.
    if (created && useApp.getState().activeId === owner.connection) {
      useApp.getState().openTabIn(owner, { kind: "session", sessionId: created.id }, "left");
      await act((client) => client.close(session.id));
    }
  };
}

/** Lies over the terminal of a session whose agent has quit: the screen shows through, but can't be typed into. */
function ExitedCurtain({ session }: { session: Session }) {
  const act = useApp((s) => s.act);
  const replaceWith = useReplace(session);
  // The curtain, not Close, takes focus: a key meant for something else, such as the Enter that started the session, must not close it.
  const curtain = useRef<HTMLDivElement>(null);
  useEffect(() => curtain.current?.focus(), []);

  return (
    <div ref={curtain} tabIndex={-1} className="curtain" role="region" aria-label="Session exited">
      <div className="curtain-card" role="status">
        <p className="curtain-title">{session.display_name} has exited</p>
        <p className="curtain-text">The agent is no longer running. Close the session to remove its terminal.</p>
        <div className="curtain-actions">
          {session.tool && session.session_id && (
            <button type="button" onClick={() => void replaceWith(true)}>
              Resume
            </button>
          )}
          <button type="button" className="primary" onClick={() => void act((client) => client.close(session.id))}>
            Close session
          </button>
        </div>
      </div>
    </div>
  );
}

function SessionBanner({ session }: { session: Session }) {
  const act = useApp((s) => s.act);
  const replaceWith = useReplace(session);

  if (session.status === "failed") {
    return (
      <div className="banner banner-failed">
        <span>{session.error ?? "The session failed to start."}</span>
        {session.tool && (
          <button type="button" onClick={() => void replaceWith(false)}>
            Try again
          </button>
        )}
        <button type="button" onClick={() => void act((client) => client.close(session.id))}>
          Dismiss
        </button>
      </div>
    );
  }
  return null;
}

/** A session in a tab: its terminal, or why there isn't one. */
function SessionPane({ session, focused, visible, client, onClose }: SessionPaneProps) {
  const activeId = useApp((s) => s.activeId);
  const settings = useApp((s) => s.settings);
  const loaded = useApp((s) => Object.keys(s.sessions).length > 0);
  // The plugins below the terminal that are taking its input, such as the Input bar.
  const [takers, setTakers] = useState<string[]>([]);
  const keyboard = useRef<((data: string) => boolean) | null>(null);
  const focusers = useRef(new Map<string, () => void>());
  const onTakeInput = useCallback((id: string, on: boolean, focus?: () => void) => {
    if (on && focus) focusers.current.set(id, focus);
    else focusers.current.delete(id);
    setTakers((ids) => (on === ids.includes(id) ? ids : on ? [...ids, id] : ids.filter((other) => other !== id)));
  }, []);
  const focusTaker = useCallback(() => focusers.current.values().next().value?.(), []);
  // Shown again after being kept out of sight: its terminal takes the keyboard back, as a new one would.
  const [focusRequest, setFocusRequest] = useState(0);
  const wasVisible = useRef(visible);
  // Before the screen is drawn, so keys typed right after the switch reach it.
  useLayoutEffect(() => {
    if (visible && !wasVisible.current && focused && takers.length === 0) setFocusRequest((n) => n + 1);
    wasVisible.current = visible;
  }, [visible, focused, takers.length]);

  if (!session) {
    if (!loaded) return null;
    return (
      <div className="placard">
        <h2>This session is gone</h2>
        <p>It was closed, or it ended while the app was away.</p>
        <div className="placard-actions">
          <button type="button" onClick={onClose}>
            Close tab
          </button>
        </div>
      </div>
    );
  }
  const showTerminal = session.attachable && session.status !== "starting";
  return (
    <>
      <SessionBanner session={session} />
      {showTerminal ? (
        <TerminalView
          key={`${activeId}/${session.id}/${settings.theme}/${settings.renderer}/${settings.fontSize}`}
          client={client}
          sessionId={session.id}
          focusRequest={focusRequest}
          searchable={focused}
          locked={session.status === "exited"}
          typing={takers.length === 0}
          onWatchedClick={focusTaker}
          keyboard={keyboard}
          overlay={<ExitedCurtain session={session} />}
        />
      ) : session.status === "starting" ? (
        <Starting session={session} />
      ) : session.status !== "failed" ? (
        <div className="placard">
          <h2>{session.display_name} isn't in tmux</h2>
          <p>This agent was started outside tmux, so its terminal can't be opened here. Its details are on the right.</p>
        </div>
      ) : null}
      {showTerminal && <TerminalInputs session={session} onTakeInput={onTakeInput} keyboard={keyboard} />}
    </>
  );
}

export const MainArea = memo(function MainArea() {
  // Only what this draws from the selected session, so its runtime ticking doesn't draw the main area again.
  const status = useApp((s) => selectedSession(s)?.status);
  const attachable = useApp((s) => !!selectedSession(s)?.attachable);
  const linkState = useApp((s) => s.link.state);
  const client = useActiveClient();
  const linked = linkState === "connected" || linkState === "reconnecting";
  const feature = useApp(currentFeature);
  const socketBlocked = useApp((s) => s.link.state === "reconnecting" && s.link.socketBlocked);
  const showTerminal = linked && attachable && status !== "starting";

  const renderSession = useCallback(
    (props: Omit<SessionPaneProps, "client">) => <SessionPane key={props.tabKey} {...props} client={client} />,
    [client],
  );

  useEffect(() => {
    // Leaving the page would cut off the terminal.
    if (!showTerminal) return;
    const guard = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [showTerminal]);

  return (
    <main className="main" data-status={linked ? (status ?? "none") : "none"}>
      {socketBlocked ? (
        <div className="placard" role="alert">
          <h2>This service can't open a live connection</h2>
          <p>
            The service answered, but its WebSocket did not. If it's behind a proxy, the proxy needs to forward WebSocket
            upgrades. In nginx, that means setting the Upgrade and Connection headers. The app keeps trying and continues on
            its own once this is fixed.
          </p>
        </div>
      ) : linked ? (
        <>
          {/* Sessions stays on the page under the other features, so its terminals keep their connection. */}
          <Workbench
            feature="sessions"
            hidden={feature !== "sessions"}
            renderSession={renderSession}
            background={null}
          />
          {feature !== "sessions" && (
            <Workbench
              feature={feature}
              renderSession={renderSession}
              background={pluginOf(feature) ? <PluginPanel pluginId={pluginOf(feature)!} side="background" /> : null}
            />
          )}
        </>
      ) : (
        <ConnectionProblem />
      )}
    </main>
  );
});

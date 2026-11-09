import { useEffect, useMemo, useRef, useState } from "react";
import type { Session } from "../api/client";
import { connectionName, failureMessage, tokenFromInput } from "../connections";
import { platform } from "../platform";
import { pluginsFor } from "../plugins/registry";
import { PluginSlot } from "../plugins/Slot";
import { activeClient, activeConnection, useApp } from "../store";
import { TerminalView } from "../terminal/TerminalView";
import { DialoguesView } from "./DialoguesView";
import { HistoryView } from "./HistoryView";

type Selection = { sessions: Record<string, Session>; selected: Record<string, string>; activeId: string };

export function selectedSession(state: Selection): Session | undefined {
  return state.sessions[state.selected[state.activeId]];
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

function ConnectionProblem() {
  const link = useApp((s) => s.link);
  const connection = useApp(activeConnection);
  const retry = useApp((s) => s.retry);
  const setToken = useApp((s) => s.setToken);
  const setSwitcherOpen = useApp((s) => s.setSwitcherOpen);
  const [token, setTokenText] = useState("");

  if (link.state === "connecting") {
    return (
      <div className="placard">
        <h2>Connecting to {connectionName(connection)}</h2>
      </div>
    );
  }
  if (link.state !== "failed") return null;
  // The app can't start a service: a stopped one has nothing to answer it. It says how instead.
  const down = link.failure.kind === "unreachable" || link.failure.kind === "stopped";
  const noService = down && platform.kind === "desktop" && connection.isHome && !connection.instanceId;
  const { title, detail } = noService
    ? {
        title: "No service on this computer",
        detail: "Install agentop here and run agentop serve, or add a service running on another machine.",
      }
    : failureMessage(link.failure, connectionName(connection));
  const needsToken = link.failure.kind === "no_token" || link.failure.kind === "token_rejected";
  return (
    <div className="placard">
      <h2 className="placard-title">
        {down && <span className="link-dot link-failed" aria-hidden="true" />}
        {title}
      </h2>
      <p>{detail}</p>
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
            placeholder="Paste the token"
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
        {link.failure.kind === "identity_changed" ? (
          <button type="button" onClick={() => retry(true)}>
            Trust this service
          </button>
        ) : (
          !needsToken && (
            <button type="button" onClick={() => retry()}>
              Try again
            </button>
          )
        )}
        <button type="button" onClick={() => setSwitcherOpen(true)}>
          Switch service
        </button>
      </div>
    </div>
  );
}

function useReplace(session: Session) {
  const act = useApp((s) => s.act);
  const select = useApp((s) => s.select);
  return async function replaceWith(resume: boolean) {
    const created = await act((client) =>
      client.create({
        tool: session.tool!,
        cwd: session.cwd,
        name: session.display_name.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 32) || null,
        resume: resume ? { session_id: session.session_id! } : null,
      }),
    );
    if (created) {
      select(created.id);
      await act((client) => client.close(session.id));
    }
  };
}

/** Lies over the terminal of a session whose agent has quit: the screen shows through, but can't be typed into. */
function ExitedCurtain({ session }: { session: Session }) {
  const act = useApp((s) => s.act);
  const replaceWith = useReplace(session);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => closeButton.current?.focus(), []);

  return (
    <div className="curtain" role="region" aria-label="Session exited">
      <div className="curtain-card" role="status">
        <p className="curtain-title">{session.display_name} has exited</p>
        <p className="curtain-text">The agent is no longer running. Close the session to remove its terminal.</p>
        <div className="curtain-actions">
          {session.tool && session.session_id && (
            <button type="button" onClick={() => void replaceWith(true)}>
              Resume
            </button>
          )}
          <button ref={closeButton} type="button" className="primary" onClick={() => void act((client) => client.close(session.id))}>
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

export function MainArea() {
  const session = useApp(selectedSession);
  const linkState = useApp((s) => s.link.state);
  const connections = useApp((s) => s.connections);
  const activeId = useApp((s) => s.activeId);
  const setDialog = useApp((s) => s.setDialog);
  const client = useMemo(() => activeClient({ connections, activeId }), [connections, activeId]);
  const linked = linkState === "connected" || linkState === "reconnecting";
  const view = useApp((s) => s.view);
  const plugins = useApp((s) => s.plugins);
  // A view whose plugin was disabled, or isn't on this service, falls back to the sessions.
  const pluginView = view.startsWith("plugin:")
    ? pluginsFor("left-view", plugins).find(({ info }) => `plugin:${info.id}` === view)?.info.id
    : undefined;
  const settings = useApp((s) => s.settings);
  const socketBlocked = useApp((s) => s.link.state === "reconnecting" && s.link.socketBlocked);
  const showTerminal = linked && session?.attachable && session.status !== "starting";

  useEffect(() => {
    if (!showTerminal) return;
    const guard = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [showTerminal]);

  return (
    <main className="main" data-status={linked ? (session?.status ?? "none") : "none"}>
      {socketBlocked ? (
        <div className="placard" role="alert">
          <h2>This service can't open a live connection</h2>
          <p>
            The service answered, but its WebSocket did not. If it's behind a proxy, the proxy needs to forward WebSocket
            upgrades. In nginx, that means setting the Upgrade and Connection headers. The app keeps trying and continues on
            its own once this is fixed.
          </p>
        </div>
      ) : linked && view === "history" ? (
        <HistoryView client={client} />
      ) : linked && view === "dialogues" ? (
        <DialoguesView client={client} />
      ) : linked && pluginView ? (
        <div className="plugin-view">
          <PluginSlot slot="left-view" only={pluginView} />
        </div>
      ) : linked ? (
        <>
          {session && <SessionBanner session={session} />}
          {showTerminal ? (
            <TerminalView
              key={`${activeId}/${session.id}/${settings.theme}/${settings.renderer}/${settings.fontSize}`}
              client={client}
              sessionId={session.id}
              focusRequest={0}
              locked={session.status === "exited"}
              overlay={<ExitedCurtain session={session} />}
            />
          ) : session?.status === "starting" ? (
            <Starting session={session} />
          ) : session && session.status !== "failed" ? (
            <div className="placard">
              <h2>{session.display_name} isn't in tmux</h2>
              <p>This agent was started outside tmux, so its terminal can't be opened here. Its details are on the right.</p>
            </div>
          ) : !session ? (
            <div className="placard">
              <h2>Start a session, or pick one from the list</h2>
              <div className="placard-actions">
                <button type="button" className="primary" onClick={() => setDialog("new-session")}>
                  New session
                </button>
              </div>
            </div>
          ) : null}
        </>
      ) : (
        <ConnectionProblem />
      )}
    </main>
  );
}

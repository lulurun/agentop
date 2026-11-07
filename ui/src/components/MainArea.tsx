import { useEffect, useMemo, useState } from "react";
import type { Session } from "../api/client";
import { failureMessage, tokenFromInput } from "../connections";
import { activeClient, activeConnection, useApp } from "../store";
import { TerminalView } from "../terminal/TerminalView";

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
        <h2>Connecting to {connection.label}</h2>
      </div>
    );
  }
  if (link.state !== "failed") return null;
  const { title, detail } = failureMessage(link.failure, connection.label);
  const needsToken = link.failure.kind === "no_token" || link.failure.kind === "token_rejected";
  return (
    <div className="placard">
      <h2>{title}</h2>
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

function SessionBanner({ session }: { session: Session }) {
  const act = useApp((s) => s.act);
  const select = useApp((s) => s.select);

  async function replaceWith(resume: boolean) {
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
  }

  if (session.status === "exited") {
    return (
      <div className="banner">
        <span>The agent has exited. The shell is still open.</span>
        {session.tool && session.session_id && (
          <button type="button" onClick={() => void replaceWith(true)}>
            Resume
          </button>
        )}
        <button type="button" onClick={() => void act((client) => client.close(session.id))}>
          Close session
        </button>
      </div>
    );
  }
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
      ) : linked ? (
        <>
          {session && <SessionBanner session={session} />}
          {showTerminal ? (
            <TerminalView key={`${activeId}/${session.id}`} client={client} sessionId={session.id} focusRequest={0} />
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

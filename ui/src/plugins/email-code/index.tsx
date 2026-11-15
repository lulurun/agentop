import { useEffect, useState } from "react";
import type { LoginContext, UiPlugin } from "../types";

/**
 * Sign in with a code sent by email (the email-code plugin). The request ID is
 * kept in sessionStorage, because a phone may reload the page while you are
 * away reading the mail.
 */
interface Pending {
  request: string;
  expiresAt: number;
}

const storageKey = (url: string) => `agentop.email-code:${url}`;

function loadPending(url: string): Pending | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey(url)) ?? "null") as Pending | null;
    return saved && typeof saved.request === "string" && saved.expiresAt > Date.now() ? saved : null;
  } catch {
    return null;
  }
}

function savePending(url: string, pending: Pending | null) {
  try {
    if (pending) sessionStorage.setItem(storageKey(url), JSON.stringify(pending));
    else sessionStorage.removeItem(storageKey(url));
  } catch {
    // Without storage, a reload just means asking for another code.
  }
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function EmailCodeLogin({ login }: { login: LoginContext }) {
  const [pending, setPending] = useState<Pending | null>(() => loadPending(login.url));
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => savePending(login.url, pending), [login.url, pending]);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const body = await login.client.post<{ request: string; expires_in: number }>("/send", { name: login.clientName });
      setPending({ request: body.request, expiresAt: Date.now() + body.expires_in * 1000 });
      setCode("");
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const token = await login.client.post<Parameters<LoginContext["signIn"]>[0]>("/verify", {
        request: pending.request,
        code,
        name: login.clientName,
      });
      setPending(null);
      login.signIn(token);
    } catch (e) {
      setError(messageOf(e));
      setBusy(false);
    }
  }

  if (!pending) {
    return (
      <div className="email-code">
        <button type="button" className="primary" disabled={busy} onClick={() => void send()}>
          {busy ? "Sending…" : "Send me a sign-in code"}
        </button>
        {error && <p className="form-error" role="alert">{error}</p>}
      </div>
    );
  }
  return (
    <div className="email-code">
      <p>Code sent. It works for 10 minutes.</p>
      <form
        className="token-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (code.trim()) void verify();
        }}
      >
        <input
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          aria-label="Code"
          placeholder="6-digit code"
          maxLength={12}
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        <button type="submit" className="primary" disabled={busy || !code.trim()}>
          Sign in
        </button>
      </form>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button type="button" className="link-button" disabled={busy} onClick={() => void send()}>
        Send another code
      </button>
    </div>
  );
}

export const plugin: UiPlugin = { login: EmailCodeLogin };

/**
 * Sessions' start buffer (docs/0_04_start.md): the prompts on their way to a session, kept outside
 * the buffer's component so a countdown carries on while another tab shows.
 */
import { create } from "zustand";
import type { ServiceClient, StartDecision } from "./api/client";
import { useApp } from "./store";

export const COUNTDOWN_MS = 3000;
const DRAFT_KEY = "agentop.start.draft";

export type Field = "tool" | "model" | "cwd" | "name" | "prompt";

export interface Choice {
  tool: string;
  model: string | null;
  cwd: string;
  name: string;
  title: string;
  /** The session's first prompt: the probe's task, "" for none. */
  prompt: string;
}

export interface Pending {
  key: string;
  prompt: string;
  /** probing → counting (or asking, when the folder isn't known) → launching → launched; editing stops the count. */
  phase: "probing" | "counting" | "asking" | "editing" | "launching" | "launched" | "failed";
  decision?: StartDecision;
  choice?: Choice;
  changed: Field[];
  deadline?: number;
  error?: string;
  sessionId?: string;
}

interface StartState {
  draft: string;
  pending: Pending[];
  setDraft(text: string): void;
  send(client: ServiceClient, prompt: string): void;
  change(key: string, field: Field, value: string | null): void;
  edit(key: string): void;
  launch(client: ServiceClient, key: string): void;
  dismiss(key: string): void;
}

function loadDraft(): string {
  try {
    return localStorage.getItem(DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

const timers = new Map<string, ReturnType<typeof setTimeout>>();

function stopTimer(key: string) {
  clearTimeout(timers.get(key));
  timers.delete(key);
}

export const useStart = create<StartState>()((set, get) => {
  const update = (key: string, change: (p: Pending) => Pending) =>
    set((s) => ({ pending: s.pending.map((p) => (p.key === key ? change(p) : p)) }));
  const find = (key: string) => get().pending.find((p) => p.key === key);

  return {
    draft: loadDraft(),
    pending: [],

    setDraft(text) {
      set({ draft: text });
      try {
        if (text) localStorage.setItem(DRAFT_KEY, text);
        else localStorage.removeItem(DRAFT_KEY);
      } catch {
        // Only a convenience.
      }
    },

    send(client, prompt) {
      const key = `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
      set((s) => ({ pending: [{ key, prompt, phase: "probing", changed: [] }, ...s.pending] }));
      client.probeStart(prompt).then(
        (decision) => {
          const choice: Choice = {
            tool: decision.tool ?? "",
            model: decision.model ?? null,
            cwd: decision.cwd ?? "",
            name: decision.name,
            title: decision.title,
            prompt: decision.task,
          };
          const known = !!decision.cwd && !!decision.tool;
          update(key, (p) => ({ ...p, decision, choice, phase: known ? "counting" : "asking", deadline: known ? Date.now() + COUNTDOWN_MS : undefined }));
          if (known) timers.set(key, setTimeout(() => get().launch(client, key), COUNTDOWN_MS));
        },
        (error: unknown) => update(key, (p) => ({ ...p, phase: "failed", error: error instanceof Error ? error.message : String(error) })),
      );
    },

    change(key, field, value) {
      stopTimer(key);
      update(key, (p) => {
        if (!p.choice) return p;
        const choice = { ...p.choice, [field]: value ?? (field === "model" ? null : "") };
        // A model belongs to its tool.
        if (field === "tool") choice.model = null;
        const changed = p.changed.includes(field) ? p.changed : [...p.changed, field];
        return { ...p, choice, changed, phase: p.phase === "asking" ? "asking" : "editing", deadline: undefined };
      });
    },

    edit(key) {
      stopTimer(key);
      update(key, (p) => ({ ...p, phase: p.phase === "counting" ? "editing" : p.phase, deadline: undefined }));
    },

    launch(client, key) {
      stopTimer(key);
      const p = find(key);
      if (!p?.choice || p.phase === "launching" || p.phase === "launched") return;
      const { tool, model, cwd, name, title, prompt } = p.choice;
      if (!tool || !cwd) {
        update(key, (q) => ({ ...q, phase: "asking", deadline: undefined }));
        return;
      }
      update(key, (q) => ({ ...q, phase: "launching", deadline: undefined, error: undefined }));
      client
        .launchStart({ prompt, tool, model, cwd, name, title, probe: p.decision?.id ?? null, changed: p.changed })
        .then(
          (session) => {
            update(key, (q) => ({ ...q, phase: "launched", sessionId: session.id }));
            useApp.setState((s) => ({ sessions: { ...s.sessions, [session.id]: session } }));
            // Someone typing the next prompt keeps the buffer; the row's link opens the session.
            if (!get().draft.trim()) useApp.getState().select(session.id, "tab", "left");
          },
          (error: unknown) =>
            update(key, (q) => ({ ...q, phase: "editing", error: error instanceof Error ? error.message : String(error) })),
        );
    },

    dismiss(key) {
      stopTimer(key);
      set((s) => ({ pending: s.pending.filter((p) => p.key !== key) }));
    },
  };
});

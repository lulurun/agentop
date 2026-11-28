import type { Session } from "../../api/client";

export interface Round {
  round: number;
  sent_at?: number | null;
  reported_at?: number | null;
  summary?: string | null;
  blocked?: boolean;
  artifact_present?: boolean | null;
  feedback?: string | null;
}

export interface TaskInput {
  goal: string;
  accept: string[];
  artifact: string;
  brief: string | null;
}

export interface TaskRow {
  id: string;
  generation: number;
  action: string | null;
  unlinked: boolean;
  session: string | null;
  tool: string | null;
  model: string | null;
  status: string | null;
  state: string;
  blocked: boolean;
  opened_at: number | null;
  opened_approx: boolean;
  closed_at: number | null;
  closed_approx: boolean;
  input: TaskInput | null;
  rounds: Round[];
  checked: string | null;
  note: string | null;
  sources: string[];
  raw: string | null;
}

export interface NoteRow {
  id: string;
  action: string;
  by: string;
  recorded: string;
  recorded_at: number | null;
  body: string;
}

/** A result file of an action: its task's artifact, or a results/ file with the action's exact P{N}_M{m}A{a}_ prefix. */
export interface ResultFile {
  name: string;
  /** Relative to the project folder. */
  path: string;
  task: string | null;
  kind: "artifact" | "supporting";
  size: number | null;
  problem: "missing" | "outside" | "folder" | "unsupported" | "empty" | null;
}

export interface ActionResults {
  ref: string;
  title: string | null;
  status: string | null;
  results: ResultFile[];
  /** Results past the ones listed. */
  more?: number;
}

export interface Progress {
  project: string;
  generations: number[];
  current: number;
  generation: number;
  fallback: boolean;
  direction: string | null;
  direction_placeholder: boolean;
  plan: string | null;
  active: TaskRow[];
  completed: TaskRow[];
  notes: NoteRow[];
  issues: string[];
  missing: { track: boolean; direction: boolean; plan: boolean; executions: boolean };
  legacy: boolean;
  actions?: ActionResults[];
  results_truncated?: boolean;
}

const PROBLEMS: Record<NonNullable<ResultFile["problem"]>, string> = {
  missing: "not written",
  outside: "leads outside the project, not shown",
  folder: "folder",
  unsupported: "not a regular file",
  empty: "empty",
};

/** Only a file inside the project that is there can be opened; an empty one opens too, and says so. */
export function canOpen(file: ResultFile): boolean {
  return file.problem === null || file.problem === "empty";
}

/** What the list says beside a result's name: whose it is, its size, and why it can't be opened. */
export function resultMeta(file: ResultFile): string {
  const parts = [file.kind === "artifact" ? `artifact of ${file.task}` : "supporting"];
  if (file.problem) parts.push(PROBLEMS[file.problem]);
  else if (file.size != null) parts.push(formatBytes(file.size));
  return parts.join(" · ");
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export function progressPath(project: string, generation: number | null): string {
  const path = `/projects/${encodeURIComponent(project)}/progress`;
  return generation ? `${path}?generation=${generation}` : path;
}

/** The selected session is the chosen project's own Coordinator, and no History tab is focused. */
export function isProjectCoordinator(
  session: Session | undefined,
  chosen: string | undefined,
  projects: { name: string }[] | null,
  pastId: string | undefined,
): session is Session {
  return Boolean(
    session && chosen && !pastId && session.managed && session.display_name === "Coordinator"
      && session.project === chosen && projects?.some((p) => p.name === chosen),
  );
}

/** Which direction the viewer shows: the current one while following it, else the one picked. */
export interface Choice {
  follow: boolean;
  generation: number | null;
}

/** The choice after a response: following moves to a new current direction; a picked one that is gone falls back to following. */
export function chosenAfter(choice: Choice, response: Progress): Choice {
  if (choice.follow || response.fallback) return { follow: true, generation: response.current || null };
  return choice;
}

export function pick(generation: number, current: number): Choice {
  return { follow: generation === current, generation };
}

/** Requests numbered so only the newest one's answer is used. */
export function latestOnly() {
  let seq = 0;
  return {
    begin() {
      const mine = ++seq;
      return () => mine === seq;
    },
    cancel() {
      seq++;
    },
  };
}

export function when(at: number | null | undefined, approximate = false): string {
  if (at == null) return "Unknown time";
  const text = new Date(at * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: approximate ? "short" : "medium" });
  return approximate ? `about ${text}` : text;
}

export const STATE_LABELS: Record<string, string> = {
  "awaiting report": "Awaiting report",
  "awaiting review": "Awaiting review",
  accepted: "Accepted",
  failed: "Failed",
  unknown: "Unknown",
};

export function stateLabel(row: TaskRow): string {
  const label = STATE_LABELS[row.state] ?? row.state;
  return row.blocked ? `${label} (blocked)` : label;
}

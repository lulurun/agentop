/**
 * The Dialogues feature's shared state: the list of dialogues on the service,
 * polled while any of the feature's pieces is on screen, and the dialogue
 * picked last. Also the tabs it opens, and how picking a dialogue lays them out.
 */
import { useEffect } from "react";
import { create } from "zustand";
import { layoutOf, useApp } from "../../store";
import { areasOf, focusArea, focusedTab, open, type Layout, type TabContent } from "../../tabs";
import type { PluginClient } from "../types";

export const PLUGIN_ID = "dialogue";
export const FEATURE = `plugin:${PLUGIN_ID}` as const;
const POLL_MS = 3_000;

export type Side = "a" | "b";

export interface DialogueSummary {
  id: string;
  title: string;
  scenario: string;
  status: string;
  /** Not finished yet: starting or running. */
  running: boolean;
  agent_a: string | null;
  agent_b: string | null;
  name_a: string;
  name_b: string;
  session_a: string | null;
  session_b: string | null;
  session_a_alive: boolean;
  session_b_alive: boolean;
  start_time: string | null;
  end_time: string | null;
  max_turns: number | null;
  error: string | null;
}

export interface DialogueStats {
  turns: number;
  turns_a: number;
  turns_b: number;
  words_a: number;
  words_b: number;
  retries: number;
  duration_seconds: number | null;
  completed_by: string | null;
}

export interface DialogueDetail extends DialogueSummary {
  cwd: string;
  brief: string;
  progress: string;
  log_tail: string;
  stats: DialogueStats;
}

export interface Turn {
  turn: number;
  side: Side;
  name: string;
  status: string;
  body: string;
  at: string | null;
}

export interface DialogueFile {
  path: string;
  size: number;
  modified: number;
}

export interface FileText {
  path: string;
  size: number;
  truncated: boolean;
  binary: boolean;
  text: string;
}

export interface Scenario {
  id: string;
  name: string;
  summary: string;
  shelf: boolean;
  name_a: string;
  name_b: string;
  max_turns: number;
  description: string;
  fields: string[];
}

export const dialoguePath = (id: string) => `/dialogues/${encodeURIComponent(id)}`;

// ------------------------------------------------------------------ the list

interface DialogueState {
  items: DialogueSummary[] | null;
  /** The dialogue picked last. The right panel shows the focused tab's dialogue, or this one. */
  selected: string | null;
}

export const useDialogues = create<DialogueState>(() => ({ items: null, selected: null }));

let watchers = 0;
let timer: ReturnType<typeof setInterval> | undefined;
let polled: PluginClient | null = null;

export async function refreshDialogues(client: PluginClient): Promise<void> {
  const items = await client.get<DialogueSummary[]>("/dialogues").catch(() => null);
  // A reply for a service the app has since left is dropped.
  if (items && client === polled) useDialogues.setState({ items });
}

/** The dialogues on the service, kept fresh while a component using this is mounted. */
export function useDialogueList(client: PluginClient): DialogueSummary[] | null {
  useEffect(() => {
    if (polled !== client) {
      polled = client;
      useDialogues.setState({ items: null, selected: null });
    }
    watchers += 1;
    void refreshDialogues(client);
    timer ??= setInterval(() => polled && document.visibilityState !== "hidden" && void refreshDialogues(polled), POLL_MS);
    return () => {
      watchers -= 1;
      if (watchers === 0) {
        clearInterval(timer);
        timer = undefined;
      }
    };
  }, [client]);
  return useDialogues((s) => s.items);
}

export function useDialogue(client: PluginClient, id: string): DialogueSummary | null | undefined {
  const items = useDialogueList(client);
  if (items === null) return undefined; // not loaded yet
  return items.find((d) => d.id === id) ?? null;
}

// ------------------------------------------------------------------ tabs

/** A tab's key is the dialogue's ID, a colon, then the agent's side or a file's path. IDs have no colon. */
export function keyOf(id: string, rest: string): string {
  return `${id}:${rest}`;
}

export function splitKey(key: string): [id: string, rest: string] {
  const at = key.indexOf(":");
  return at < 0 ? [key, ""] : [key.slice(0, at), key.slice(at + 1)];
}

export const agentTab = (id: string, side: Side): TabContent => ({ kind: "plugin", plugin: PLUGIN_ID, view: "agent", key: keyOf(id, side) });
export const fileTab = (id: string, path: string): TabContent => ({ kind: "plugin", plugin: PLUGIN_ID, view: "file", key: keyOf(id, path) });
export const wizardTab: TabContent = { kind: "plugin", plugin: PLUGIN_ID, view: "new" };

function isView(content: TabContent, view: string): boolean {
  return content.kind === "plugin" && content.plugin === PLUGIN_ID && content.view === view;
}

/** The dialogue a tab of this feature belongs to, if any. */
export function dialogueOf(content: TabContent | undefined): string | null {
  if (!content || content.kind !== "plugin" || content.plugin !== PLUGIN_ID || !content.key) return null;
  return splitKey(content.key)[0];
}

/** A dialogue's two agents, side by side: A in the left area and B in the right one. */
export function showAgents(layout: Layout, id: string): Layout {
  return focusArea(open(open(layout, agentTab(id, "a"), "left"), agentTab(id, "b"), "right"), "left");
}

/** Each dialogue is a project with its own tabs: show this one's as they were left, or its two agents if it has none or is picked again. */
export async function selectDialogue(id: string): Promise<void> {
  useDialogues.setState({ selected: id });
  const again = currentProject() === id;
  await useApp.getState().setProject(FEATURE, id);
  if (currentProject() !== id) return;
  if (!again && areasOf(layoutOf(useApp.getState(), FEATURE)).length > 0) return;
  const app = useApp.getState();
  app.openTab(agentTab(id, "a"), "left");
  app.openTab(agentTab(id, "b"), "right");
  app.focusArea(FEATURE, "left");
}

const currentProject = () => {
  const s = useApp.getState();
  return s.scopes[s.activeId]?.[FEATURE];
};

export async function openFile(id: string, path: string): Promise<void> {
  useDialogues.setState({ selected: id });
  if (currentProject() !== id) await useApp.getState().setProject(FEATURE, id);
  if (currentProject() === id) useApp.getState().openTab(fileTab(id, path), "right", "tab");
}

/** The wizard belongs to no dialogue, so it opens with none shown. */
export function openWizard(): void {
  useApp.getState().setFeature(FEATURE);
  void useApp.getState().setProject(FEATURE, null);
  useApp.getState().openTab(wizardTab, "left");
}

export function closeWizard(): void {
  useApp.getState().closeTabs(FEATURE, (c) => isView(c, "new"));
}

/** A removed dialogue's tabs go with its folder; the feature shows none. */
export function forgetDialogue(id: string): void {
  if (useDialogues.getState().selected === id) useDialogues.setState({ selected: null });
  if (currentProject() === id) void useApp.getState().setProject(FEATURE, null);
}

/** The dialogue the right panel is about: the focused tab's, or else the one shown. */
export function useCurrentDialogue(): string | null {
  const focused = useApp((s) => dialogueOf(focusedTab(layoutOf(s, FEATURE))?.content));
  const shown = useApp((s) => s.scopes[s.activeId]?.[FEATURE] ?? null);
  return focused ?? shown;
}

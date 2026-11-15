/**
 * The Memory workspace's shared state: the search in the left panel and its
 * results, the message each conversation tab was opened at, and the tabs.
 */
import { useEffect } from "react";
import { create } from "zustand";
import { useApp } from "../../store";
import { focusedTab, newTabId, sameContent, type Layout, type TabContent } from "../../tabs";
import type { PluginClient } from "../types";

export const PLUGIN_ID = "memory";
export const WORKSPACE = `plugin:${PLUGIN_ID}` as const;

export interface SessionHit {
  tool: string;
  session: string;
  project: string | null;
  first_at: number | null;
  last_at: number | null;
  messages: number;
  title: string;
  summary: string;
  keywords: string[];
  matched: string[];
  snippet: string;
}

export interface MessageHit {
  id: number;
  tool: string;
  session: string;
  project: string | null;
  at: number | null;
  role: "user" | "assistant";
  snippet: string;
  /** For a reply, the prompt it answered. */
  prompt: string | null;
}

export interface Results {
  sessions: SessionHit[];
  messages: MessageHit[];
}

export interface Summary {
  title: string;
  summary: string;
  keywords: string[];
  made_at: number;
  made_by: string;
  messages: number;
}

export interface SessionInfo {
  tool: string;
  session: string;
  project: string | null;
  first_at: number | null;
  last_at: number | null;
  messages: number;
  summary: Summary | null;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  at: number | null;
  text: string;
}

export interface Miss {
  at: number;
  kind: "nothing" | "wrong";
  looked_for: string;
  tried: string[];
}

/** How far back a search looks, in days; 0 is any time. */
export const SINCE_DAYS = [0, 7, 30, 90, 365] as const;

interface MemoryState {
  client: PluginClient | null;
  query: string;
  tool: string;
  sinceDays: number;
  /** The words of the search the results are for, to mark in what's shown. */
  terms: string[];
  results: Results | null;
  searching: boolean;
  error: string | null;
  /** The message a conversation tab shows first, by the tab's key. */
  targets: Record<string, number | undefined>;
  /** Session titles known from results, for tab labels. */
  titles: Record<string, string>;
}

export const useMemory = create<MemoryState>(() => ({
  client: null,
  query: "",
  tool: "",
  sinceDays: 0,
  terms: [],
  results: null,
  searching: false,
  error: null,
  targets: {},
  titles: {},
}));

/** Words, and "phrases in quotes", as the service splits a query. */
export function splitTerms(query: string): string[] {
  const terms: string[] = [];
  for (const match of query.matchAll(/"([^"]*)"|(\S+)/g)) {
    const term = (match[1] ?? match[2] ?? "").trim();
    if (term) terms.push(term);
  }
  return terms;
}

/** Start over when the app moves to another service: its results belong to the one before. */
export function useClientState(client: PluginClient): void {
  useEffect(() => {
    if (useMemory.getState().client !== client) {
      useMemory.setState({ client, results: null, terms: [], error: null, targets: {}, titles: {} });
    }
  }, [client]);
}

export async function runSearch(client: PluginClient): Promise<void> {
  const { query, tool, sinceDays } = useMemory.getState();
  const terms = splitTerms(query);
  if (!terms.length) {
    useMemory.setState({ results: null, terms: [], error: null });
    return;
  }
  const params = new URLSearchParams({ q: query });
  if (tool) params.set("tool", tool);
  if (sinceDays) params.set("since", String(Date.now() / 1000 - sinceDays * 86400));
  useMemory.setState({ searching: true, error: null });
  try {
    const results = await client.get<Results>(`/recall?${params}`);
    if (useMemory.getState().client !== client) return; // the app has moved to another service
    const titles = { ...useMemory.getState().titles };
    for (const s of results.sessions) titles[keyOf(s.tool, s.session)] = s.title;
    useMemory.setState({ results, terms, titles, searching: false });
  } catch (failure) {
    useMemory.setState({ searching: false, error: failure instanceof Error ? failure.message : String(failure) });
  }
}

// ------------------------------------------------------------------ tabs

/** A conversation tab's key is the tool, a colon, then the session. Tool names have no colon. */
export function keyOf(tool: string, session: string): string {
  return `${tool}:${session}`;
}

export function splitKey(key: string): [tool: string, session: string] {
  const at = key.indexOf(":");
  return at < 0 ? ["", key] : [key.slice(0, at), key.slice(at + 1)];
}

export const conversationTab = (tool: string, session: string): TabContent => ({
  kind: "plugin",
  plugin: PLUGIN_ID,
  view: "conversation",
  key: keyOf(tool, session),
});

function isConversation(content: TabContent | undefined): boolean {
  return content?.kind === "plugin" && content.plugin === PLUGIN_ID && content.view === "conversation";
}

/** The conversation tab a layout's focus is on, as [tool, session]. */
export function conversationOf(content: TabContent | undefined): [tool: string, session: string] | null {
  if (!isConversation(content) || content?.kind !== "plugin" || !content.key) return null;
  return splitKey(content.key);
}

/**
 * Show a conversation. One already open in either group is shown where it is;
 * otherwise it takes the place of the focused group's conversation, as a
 * session replaces the session shown, or gets a tab of its own.
 */
export function showConversation(layout: Layout, content: TabContent, newTab = false): Layout {
  for (const [index, group] of layout.groups.entries()) {
    const open = group.tabs.find((t) => sameContent(t.content, content));
    if (open) {
      const groups = layout.groups.slice();
      groups[index] = { ...group, active: open.id };
      return { groups, focused: index };
    }
  }
  if (layout.groups.length === 0) {
    const id = newTabId();
    return { groups: [{ tabs: [{ id, content }], active: id }], focused: 0 };
  }
  const index = Math.min(layout.focused, layout.groups.length - 1);
  const group = layout.groups[index];
  const current = group.tabs.find((t) => t.id === group.active);
  const groups = layout.groups.slice();
  if (!newTab && current && isConversation(current.content)) {
    groups[index] = { ...group, tabs: group.tabs.map((t) => (t.id === current.id ? { ...t, content } : t)) };
  } else {
    const id = newTabId();
    const at = group.tabs.findIndex((t) => t.id === group.active);
    const tabs = group.tabs.slice();
    tabs.splice(at < 0 ? tabs.length : at + 1, 0, { id, content });
    groups[index] = { tabs, active: id };
  }
  return { groups, focused: index };
}

/** Open a session's conversation, at a message when given one. */
export function openConversation(tool: string, session: string, at?: number, newTab = false): void {
  const key = keyOf(tool, session);
  useMemory.setState((s) => ({ targets: { ...s.targets, [key]: at } }));
  useApp.getState().editLayout(WORKSPACE, (layout) => showConversation(layout, conversationTab(tool, session), newTab));
}

/** The session the right panel is about: the focused tab's. */
export function useCurrentConversation(): [tool: string, session: string] | null {
  const key = useApp((s) => {
    const layout = s.layouts[s.activeId]?.[WORKSPACE];
    const found = layout ? conversationOf(focusedTab(layout)?.content) : null;
    return found ? keyOf(...found) : null;
  });
  return key ? splitKey(key) : null;
}

/**
 * The Memory feature's shared state: the search tab's query, results and recent
 * sessions, the message each conversation tab was opened at, and the tabs.
 */
import { useEffect } from "react";
import { create } from "zustand";
import { layoutOf, useApp } from "../../store";
import { activeTab, focusedTab, type Layout, type TabContent } from "../../tabs";
import type { PluginClient } from "../types";

export const PLUGIN_ID = "memory";
export const FEATURE = `plugin:${PLUGIN_ID}` as const;

/** A session in the recent list: `title` and `summary` are null until it is summarized. */
export interface RecentSession {
  tool: string;
  session: string;
  project: string | null;
  first_at: number | null;
  last_at: number | null;
  messages: number;
  title: string | null;
  summary: string | null;
  /** Its first prompt. */
  prompt: string | null;
}

/** A session found by its summary, or by its messages only, when `matched` and `snippet` are empty. */
export interface SessionHit extends RecentSession {
  keywords: string[];
  matched: string[];
  snippet: string;
  /** How many of its messages match. */
  mentions: number;
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

/** Which results show, as a web search's All, Images, Maps. */
export type Scope = "all" | "sessions" | "mentions";

const RECENT_PAGE = 50;

interface MemoryState {
  client: PluginClient | null;
  query: string;
  tool: string;
  sinceDays: number;
  /** The words of the search the results are for, to mark in what's shown. */
  terms: string[];
  results: Results | null;
  scope: Scope;
  searching: boolean;
  error: string | null;
  recent: RecentSession[] | null;
  /** More recent sessions than shown are there. */
  recentMore: boolean;
  /** The message a conversation tab shows first, by the tab's key. */
  targets: Record<string, number | undefined>;
  /** Sessions' titles, or else first prompts, from the lists, for tab labels. */
  titles: Record<string, string>;
}

export const useMemory = create<MemoryState>(() => ({
  client: null,
  query: "",
  tool: "",
  sinceDays: 0,
  terms: [],
  results: null,
  scope: "all",
  searching: false,
  error: null,
  recent: null,
  recentMore: false,
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
      useMemory.setState({ client, results: null, terms: [], error: null, recent: null, recentMore: false, targets: {}, titles: {} });
    }
  }, [client]);
}

function filters(): URLSearchParams {
  const { tool, sinceDays } = useMemory.getState();
  const params = new URLSearchParams();
  if (tool) params.set("tool", tool);
  if (sinceDays) params.set("since", String(Date.now() / 1000 - sinceDays * 86400));
  return params;
}

function nameSessions(sessions: RecentSession[]): void {
  const titles = { ...useMemory.getState().titles };
  for (const s of sessions) if (s.title || s.prompt) titles[keyOf(s.tool, s.session)] = (s.title || s.prompt)!;
  useMemory.setState({ titles });
}

export async function runSearch(client: PluginClient): Promise<void> {
  const { query } = useMemory.getState();
  const terms = splitTerms(query);
  if (!terms.length) {
    useMemory.setState({ results: null, terms: [], error: null });
    return;
  }
  const params = filters();
  params.set("q", query);
  useMemory.setState({ searching: true, error: null });
  try {
    const results = await client.get<Results>(`/recall?${params}`);
    if (useMemory.getState().client !== client) return; // the app has moved to another service
    nameSessions(results.sessions);
    useMemory.setState({ results, terms, searching: false });
  } catch (failure) {
    useMemory.setState({ searching: false, error: failure instanceof Error ? failure.message : String(failure) });
  }
}

/** The sessions by their last message, newest first: from the start, or with `more`, the next page. */
export async function loadRecent(client: PluginClient, more = false): Promise<void> {
  const shown = more ? (useMemory.getState().recent ?? []) : [];
  const params = filters();
  params.set("limit", String(RECENT_PAGE + 1));
  params.set("offset", String(shown.length));
  try {
    const page = await client.get<RecentSession[]>(`/sessions?${params}`);
    if (useMemory.getState().client !== client) return;
    nameSessions(page);
    useMemory.setState({ recent: [...shown, ...page.slice(0, RECENT_PAGE)], recentMore: page.length > RECENT_PAGE });
  } catch (failure) {
    useMemory.setState({ error: failure instanceof Error ? failure.message : String(failure) });
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

/** Open a session's conversation in the right area, beside the search, at a message when given one. */
export function openConversation(tool: string, session: string, at?: number, newTab = false): void {
  const key = keyOf(tool, session);
  useMemory.setState((s) => ({ targets: { ...s.targets, [key]: at } }));
  useApp.getState().openTab(conversationTab(tool, session), "right", newTab ? "tab" : "replace");
}

/** The conversation the right panel is about: the focused tab's, or while the search has focus, the one beside it. */
export function currentConversation(layout: Layout): [tool: string, session: string] | null {
  return (
    conversationOf(focusedTab(layout)?.content) ??
    conversationOf(activeTab(layout.right)?.content) ??
    conversationOf(activeTab(layout.left)?.content)
  );
}

export function useCurrentConversation(): [tool: string, session: string] | null {
  const key = useApp((s) => {
    const found = currentConversation(layoutOf(s, FEATURE));
    return found ? keyOf(...found) : null;
  });
  return key ? splitKey(key) : null;
}

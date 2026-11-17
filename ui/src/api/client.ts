import type { PluginClient } from "../plugins/types";
import type { components } from "./schema";

export type Session = components["schemas"]["SessionModel"];
export type Health = components["schemas"]["Health"];
export type ServiceInfo = components["schemas"]["ServiceInfo"];
export type UpdateInfo = components["schemas"]["UpdateInfo"];
export type HistoryItem = components["schemas"]["HistoryItem"];
export type HistoryListing = components["schemas"]["HistoryListing"];
export type HistoryStats = components["schemas"]["HistoryStats"];
export type TokenUsage = components["schemas"]["TokenUsage"];
export type ToolInfo = components["schemas"]["ToolInfo"];
export type DirListing = components["schemas"]["DirListing"];
export type FileListing = components["schemas"]["FileListing"];
export type FileEntry = components["schemas"]["FileEntry"];
export type GitChanges = components["schemas"]["GitChanges"];
export type CreateSession = components["schemas"]["CreateSession"];
export type PatchSession = components["schemas"]["PatchSession"];
export type PluginInfo = components["schemas"]["PluginInfo"];
export type ClientToken = components["schemas"]["ClientToken"];
export type ClientInfo = components["schemas"]["ClientInfo"];
export type LoginMethod = components["schemas"]["LoginMethod"];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** The request never completed. A browser gives no more detail than that. */
export class NetworkError extends Error {}

export function fetchHealth(baseUrl: string): Promise<Health> {
  return new ServiceClient(baseUrl, "").get<Health>("/health");
}

/** Sign a new client in with the service token. The service token is good for nothing else. */
export function addClient(baseUrl: string, serviceToken: string, name: string): Promise<ClientToken> {
  return new ServiceClient(baseUrl, serviceToken).request<ClientToken>("POST", "/auth/clients", { name });
}

const TOKEN_ONLY: LoginMethod[] = [{ id: "token", name: "Service token" }];

/** The ways to sign in to a service. No token needed. A service from before 2026-11-16 has only the service token. */
export async function fetchLoginMethods(baseUrl: string): Promise<LoginMethod[]> {
  try {
    return await new ServiceClient(baseUrl, "").get<LoginMethod[]>("/auth/methods");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return TOKEN_ONLY;
    throw error;
  }
}

/** A login plugin's public routes, `/api/v2/plugins/{id}/…`, called without a token. */
export function publicPluginClient(baseUrl: string, id: string): PluginClient {
  return new ServiceClient(baseUrl, "").plugin(id);
}

/**
 * One service, addressed by base URL. Nothing here is global, so services stay separate.
 * The token can be a function, read on each request, so a renewed token needs no new client.
 */
export class ServiceClient {
  constructor(
    readonly baseUrl: string,
    private readonly tokenOf: string | (() => string),
  ) {}

  get token(): string {
    return typeof this.tokenOf === "function" ? this.tokenOf() : this.tokenOf;
  }

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    let response: Response;
    const token = this.token;
    try {
      response = await fetch(`${this.baseUrl}/api/v2${path}`, {
        method,
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new NetworkError(`Could not reach ${this.baseUrl}`);
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new ApiError(response.status, typeof detail?.detail === "string" ? detail.detail : response.statusText);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  get<T>(path: string): Promise<T> {
    return this.request("GET", path);
  }

  /** An admin action. A service from before /admin had them under /service, and must stay updatable. */
  private async admin<T>(method: string, action: string): Promise<T> {
    try {
      return await this.request<T>(method, `/admin/${action}`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return this.request<T>(method, `/service/${action}`);
      throw error;
    }
  }

  service = () => this.get<ServiceInfo>("/service");
  renew = () => this.request<ClientToken>("POST", "/auth/renew");
  clients = () => this.get<ClientInfo[]>("/auth/clients");
  revokeClient = (id: string) => this.request<void>("DELETE", `/auth/clients/${encodeURIComponent(id)}`);
  updateInfo = () => this.admin<UpdateInfo>("GET", "update");
  restartService = () => this.admin("POST", "restart");
  stopService = () => this.admin("POST", "stop");
  startUpdate = () => this.admin("POST", "update");
  tools = () => this.get<ToolInfo[]>("/tools");
  sessions = () => this.get<Session[]>("/sessions");
  dirs = (path: string, hidden = false) =>
    this.get<DirListing>(`/fs/dirs?path=${encodeURIComponent(path)}&hidden=${hidden}`);
  files = (id: string, path = "") =>
    this.get<FileListing>(`/sessions/${encodeURIComponent(id)}/files?path=${encodeURIComponent(path)}`);
  changes = (id: string) => this.get<GitChanges>(`/sessions/${encodeURIComponent(id)}/changes`);
  history = () => this.get<HistoryListing>("/history");
  historyStats = (id: string) => this.get<HistoryStats>(`/history/${encodeURIComponent(id)}/stats`);
  resumeHistory = (id: string) => this.request<Session>("POST", `/history/${encodeURIComponent(id)}/resume`);
  deleteHistory = (id: string) => this.request("DELETE", `/history/${encodeURIComponent(id)}`);
  create = (body: CreateSession) => this.request<Session>("POST", "/sessions", body);
  stop = (id: string) => this.request("POST", `/sessions/${encodeURIComponent(id)}/stop`);
  close = (id: string) => this.request("DELETE", `/sessions/${encodeURIComponent(id)}`);
  patch = (id: string, body: PatchSession) =>
    this.request<Session>("PATCH", `/sessions/${encodeURIComponent(id)}`, body);
  plugins = () => this.get<PluginInfo[]>("/plugins");
  setPluginEnabled = (id: string, enabled: boolean) =>
    this.request<PluginInfo>("PATCH", `/plugins/${encodeURIComponent(id)}`, { enabled });

  /** A client for one plugin's own routes. */
  plugin(id: string): PluginClient {
    const base = `/plugins/${encodeURIComponent(id)}`;
    const at = (path: string) => `${base}${path.startsWith("/") ? path : `/${path}`}`;
    return {
      get: (path) => this.request("GET", at(path)),
      post: (path, body) => this.request("POST", at(path), body),
      patch: (path, body) => this.request("PATCH", at(path), body),
      delete: (path) => this.request("DELETE", at(path)),
    };
  }

  async socketUrl(path: string, purpose: "events" | "terminal", sessionId?: string, params = ""): Promise<string> {
    const { ticket } = await this.request<{ ticket: string }>("POST", "/tickets", {
      purpose,
      session_id: sessionId ?? null,
    });
    const base = this.baseUrl.replace(/^http/, "ws");
    return `${base}/api/v2${path}?ticket=${encodeURIComponent(ticket)}${params}`;
  }

  eventsUrl = () => this.socketUrl("/ws/events", "events");
  /** `readOnly` watches without typing or resizing. The service makes a plugin's own session read-only anyway. */
  terminalUrl = (id: string, cols: number, rows: number, readOnly = false) =>
    this.socketUrl(`/ws/sessions/${encodeURIComponent(id)}/terminal`, "terminal", id, `&cols=${cols}&rows=${rows}${readOnly ? "&readonly=true" : ""}`);
}

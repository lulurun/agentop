import type { Connection } from "../connections";
import { tauriPlatform } from "./tauri";
import { webPlatform } from "./web";

/** A new release of the desktop app itself, the native wrapper around the UI. */
export interface AppUpdate {
  /** "app": a new major version, downloaded from the release page. "service": this computer's agentop, updated in place. */
  kind: "app" | "service";
  version: string;
  /** The UI needs something this copy of the app doesn't have. */
  required: boolean;
  /** Updates this computer's agentop, on the app's start page. Only for "service". */
  install?: () => Promise<void>;
  releaseUrl: string;
}

/** The only code that differs between the web app and the desktop app. */
export interface Platform {
  kind: "web" | "desktop";
  loadConnections(): Promise<Connection[]>;
  saveConnections(connections: Connection[]): void;
  defaultConnection(): Promise<Connection | null>;
  loadActiveId(): string | null;
  saveActiveId(id: string): void;
  copyText(text: string): Promise<void>;
  /** Desktop only: a newer release of the app, if there is one. */
  checkAppUpdate?(): Promise<AppUpdate | null>;
  /** Desktop only: start this computer's service again. */
  startService?(): Promise<void>;
  /** Desktop only: open a page in the system browser. */
  openUrl?(url: string): Promise<void>;
}

/** Tauri sets this before any page script runs. */
const isDesktop = "__TAURI_INTERNALS__" in window;

export const platform: Platform = isDesktop ? tauriPlatform : webPlatform;

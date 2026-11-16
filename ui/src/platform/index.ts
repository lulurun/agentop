import type { Connection } from "../connections";
import { tauriPlatform } from "./tauri";
import { webPlatform } from "./web";

/** A new release of the desktop app itself, the native wrapper around the UI. */
export interface AppUpdate {
  version: string;
  /** The UI needs something this copy of the app doesn't have. */
  required: boolean;
  /** Download it, replace this copy, and restart. Missing where the app can't replace itself. */
  install?: () => Promise<void>;
  /** Where to get it by hand. */
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

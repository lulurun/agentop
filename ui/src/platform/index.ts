import type { Connection } from "../connections";
import { tauriPlatform } from "./tauri";
import { webPlatform } from "./web";

/** The only code that differs between the web app and the desktop app. */
export interface Platform {
  kind: "web" | "desktop";
  loadConnections(): Promise<Connection[]>;
  saveConnections(connections: Connection[]): void;
  defaultConnection(): Promise<Connection | null>;
  loadActiveId(): string | null;
  saveActiveId(id: string): void;
  copyText(text: string): Promise<void>;
}

/** Tauri sets this before any page script runs. */
const isDesktop = "__TAURI_INTERNALS__" in window;

export const platform: Platform = isDesktop ? tauriPlatform : webPlatform;

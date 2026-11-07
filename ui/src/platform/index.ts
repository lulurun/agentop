import type { Connection } from "../connections";

/** The only code that differs between the web app and the desktop app. */
export interface Platform {
  loadConnections(): Connection[];
  saveConnections(connections: Connection[]): void;
  defaultConnection(): Connection | null;
  loadActiveId(): string | null;
  saveActiveId(id: string): void;
  copyText(text: string): Promise<void>;
}

export { webPlatform as platform } from "./web";

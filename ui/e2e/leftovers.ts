import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

export const PREFIX = "agentop-e2e-";

// tmux kill-server leaves the socket file behind.
export const socketDir = join(process.env.TMUX_TMPDIR ?? "/tmp", `tmux-${process.getuid!()}`);

function alive(pid: number) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return (e as NodeJS.ErrnoException).code === "EPERM";
  }
}

/** PIDs of every process whose HOME is a test's root: its service, tmux server and agents. */
function processesIn(root: string): number[] {
  if (!existsSync("/proc") || !existsSync(root)) return [];
  const pids: number[] = [];
  for (const entry of readdirSync("/proc")) {
    const pid = Number(entry);
    if (!pid || pid === process.pid) continue;
    try {
      if (readFileSync(`/proc/${pid}/environ`, "latin1").split("\0").includes(`HOME=${root}`)) pids.push(pid);
    } catch {
      // Gone, or not ours.
    }
  }
  return pids;
}

/** Ends everything a test's root and socket hold, at once. */
export function wipe(root: string, socket: string, serve?: number) {
  if (serve) {
    try {
      process.kill(serve, "SIGKILL");
    } catch {
      // Already gone.
    }
  }
  try {
    execFileSync("tmux", ["-L", socket, "kill-server"], { stdio: "ignore", env: { ...process.env, TMUX: "" } });
  } catch {
    // No server.
  }
  for (const pid of processesIn(root)) {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      // Already gone.
    }
  }
  rmSync(join(socketDir, socket), { force: true });
  rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}

/** The PID of the test process that made a root, if it is still running. */
function owner(root: string): number | null {
  try {
    const pid = Number(readFileSync(join(root, "owner"), "utf8"));
    return pid && alive(pid) ? pid : null;
  } catch {
    return null;
  }
}

/** Removes what earlier runs left: every root, socket and process whose test process is gone. */
export function sweep() {
  const live = new Set<string>();
  for (const name of readdirSync(tmpdir())) {
    if (!name.startsWith(PREFIX)) continue;
    const root = join(tmpdir(), name);
    if (owner(root)) live.add(name);
    else wipe(root, name);
  }
  if (!existsSync(socketDir)) return;
  for (const name of readdirSync(socketDir)) {
    if (name.startsWith(PREFIX) && !live.has(name)) wipe(join(tmpdir(), basename(name)), name);
  }
}

export default sweep;

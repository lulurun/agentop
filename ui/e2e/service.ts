import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { PREFIX, wipe } from "./leftovers";

// Like Claude Code, it records its session ID by PID, so its transcript can be found.
const AGENT = `#!/bin/sh
mkdir -p "$HOME/.claude/sessions" && echo "{\\"sessionId\\": \\"fake-$$\\"}" > "$HOME/.claude/sessions/$$.json"
echo "fake agent ready"
echo "shift+tab to cycle"
while read line; do echo "got: $line"; [ "$line" = /exit ] && exit 0; done
`;
// Installed, but the shell reports it missing, so the start fails at once.
const BROKEN = `#!/bin/sh
echo "agy: command not found"
`;

/** Stands in for systemd: asks for a restart or stop by leaving a file for the test. */
const SYSTEMCTL = (root: string) => `#!/bin/sh
case "$*" in
  *" show "*) echo LoadState=loaded; echo ActiveState=active; echo MainPID=$PPID ;;
  *is-active*) test -f ${root}/updating && echo active || echo inactive ;;
  *" restart "*) touch ${root}/restart ;;
  *" stop "*) touch ${root}/stop ;;
esac
`;
const SYSTEMD_RUN = (root: string) => `#!/bin/sh
touch ${root}/updating
`;

const PYTHON = `${process.env.HOME}/.pyenv/versions/3.13.9/bin/python`;

// Workers running at once each get their own block of ports.
let nextPort = 19800 + Number(process.env.TEST_PARALLEL_INDEX ?? 0) * 500 + Math.floor(Math.random() * 400);

const live = new Set<Service>();
// A failed or interrupted test may never reach its afterEach, so the worker cleans up as it goes.
process.once("exit", () => live.forEach((s) => s.destroy()));
for (const signal of ["SIGTERM", "SIGHUP"] as const) {
  process.once(signal, () => process.exit(128 + (signal === "SIGTERM" ? 15 : 1)));
}

/** A real service with its own state and its own private tmux server. */
export class Service {
  readonly root = mkdtempSync(join(tmpdir(), PREFIX));
  readonly socket = basename(this.root);
  process: ChildProcess | null = null;
  private readonly children: ChildProcess[] = [];

  constructor(
    readonly name: string,
    readonly port = nextPort++,
  ) {
    live.add(this);
    writeFileSync(join(this.root, "owner"), String(process.pid));
    const bin = join(this.root, "bin");
    mkdirSync(bin);
    mkdirSync(join(this.root, "project"));
    for (const [file, body] of [["claude", AGENT], ["codex", AGENT], ["agy", BROKEN], ["update.sh", "exit 0\n"]]) {
      writeFileSync(join(bin, file), body);
      chmodSync(join(bin, file), 0o755);
    }
    mkdirSync(join(this.root, "state"));
    this.tmux("-f", "/dev/null", "new-session", "-d", "-s", "keeper");
    this.tmux("set-option", "-g", "default-shell", "/bin/sh");
  }

  get url() {
    return `http://127.0.0.1:${this.port}`;
  }

  private env() {
    const bin = join(this.root, "bin");
    return {
      ...process.env,
      PATH: `${bin}:/usr/local/bin:/usr/bin:/bin`,
      HOME: this.root,
      SHELL: "/bin/sh",
      // The sessions' shells save no history as the server goes down.
      HISTFILE: "/dev/null",
      AGENTOP_HOME: join(this.root, "state"),
      AGENTOP_TMUX_SOCKET: this.socket,
      // Never the real updater, which would act on this checkout.
      AGENTOP_UPDATE_SCRIPT: join(bin, "update.sh"),
      // No asking the checkout's real remote what's newer: that takes seconds, from every service.
      GIT_ALLOW_PROTOCOL: "file",
      PYTHONPATH: resolve("../src"),
    };
  }

  /** Make the next start look like it runs under systemd. */
  managed() {
    for (const [file, body] of [["systemctl", SYSTEMCTL(this.root)], ["systemd-run", SYSTEMD_RUN(this.root)]]) {
      writeFileSync(join(this.root, "bin", file), body);
      chmodSync(join(this.root, "bin", file), 0o755);
    }
    return this;
  }

  requested(action: "restart" | "stop") {
    return existsSync(join(this.root, action));
  }

  /** What update.sh would write. Passing nothing means the update has ended. */
  reportUpdate(state: string, fields: Record<string, string | null> = {}) {
    writeFileSync(join(this.root, "state/state/update-status.json"), JSON.stringify({ state, ...fields }));
    if (state !== "running") rmSync(join(this.root, "updating"), { force: true });
  }

  tmux(...args: string[]): string {
    // This starts the private server, and its sessions take its environment:
    // the test's own home, never the real one.
    return execFileSync("tmux", ["-L", this.socket, ...args], { encoding: "utf8", env: { ...this.env(), TMUX: "" } });
  }

  async start() {
    this.process = spawn(PYTHON, ["-m", "agentop.cli", "serve", "--port", String(this.port), "--name", this.name], {
      env: this.env(),
      stdio: "ignore",
    });
    for (let i = 0; i < 100; i++) {
      try {
        if ((await fetch(`${this.url}/api/v2/health`)).ok) return this;
      } catch {
        // Not up yet.
      }
      await new Promise((r) => setTimeout(r, 100));
    }
    throw new Error("service did not start");
  }

  async stop() {
    const process = this.process;
    this.process = null;
    if (!process) return;
    const exited = new Promise((r) => process.once("exit", r));
    process.kill();
    await exited;
  }

  get token(): string {
    return JSON.parse(readFileSync(join(this.root, "state/service.json"), "utf8")).token;
  }

  get connectInfo() {
    return `${this.url}#token=${this.token}`;
  }

  get signedInUrl() {
    return `${this.url}/#token=${this.token}`;
  }

  /** Another process of this test's, ended with it. */
  track(child: ChildProcess) {
    this.children.push(child);
    return child;
  }

  async dispose() {
    await this.stop();
    this.destroy();
  }

  destroy() {
    live.delete(this);
    for (const child of this.children.splice(0)) child.kill("SIGKILL");
    wipe(this.root, this.socket, this.process?.pid);
    this.process = null;
  }
}

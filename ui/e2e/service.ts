import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

// Like Claude Code, it records its session ID by PID, so its transcript can be found.
const AGENT = `#!/bin/sh
mkdir -p "$HOME/.claude/sessions" && echo "{\\"sessionId\\": \\"fake-$$\\"}" > "$HOME/.claude/sessions/$$.json"
echo "fake agent ready"
echo "shift+tab to cycle"
while read line; do echo "got: $line"; done
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

let nextPort = 19800 + Math.floor(Math.random() * 1000);

/** A real service with its own state and its own private tmux server. */
export class Service {
  readonly root = mkdtempSync(join(tmpdir(), "agentop-e2e-"));
  readonly socket = `agentop-e2e-${Math.random().toString(36).slice(2, 10)}`;
  process: ChildProcess | null = null;

  constructor(
    readonly name: string,
    readonly port = nextPort++,
  ) {
    const bin = join(this.root, "bin");
    mkdirSync(bin);
    mkdirSync(join(this.root, "project"));
    for (const [file, body] of [["claude", AGENT], ["codex", AGENT], ["agy", BROKEN], ["update.sh", "exit 0\n"]]) {
      writeFileSync(join(bin, file), body);
      chmodSync(join(bin, file), 0o755);
    }
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
    return `${this.url}/app/#token=${this.token}`;
  }

  async dispose() {
    await this.stop();
    try {
      this.tmux("kill-server");
    } catch {
      // Already gone.
    }
    rmSync(this.root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

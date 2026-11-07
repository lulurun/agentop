import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const AGENT = `#!/bin/sh
echo "fake agent ready"
echo "shift+tab to cycle"
while read line; do echo "got: $line"; done
`;
// Installed, but the shell reports it missing, so the start fails at once.
const BROKEN = `#!/bin/sh
echo "agy: command not found"
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
    for (const [file, body] of [["claude", AGENT], ["codex", AGENT], ["agy", BROKEN]]) {
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
      AGENTOP_HOME: join(this.root, "state"),
      AGENTOP_TMUX_SOCKET: this.socket,
      PYTHONPATH: resolve("../src"),
    };
  }

  tmux(...args: string[]): string {
    return execFileSync("tmux", ["-L", this.socket, ...args], { encoding: "utf8", env: { ...process.env, TMUX: "" } });
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
    rmSync(this.root, { recursive: true, force: true });
  }
}

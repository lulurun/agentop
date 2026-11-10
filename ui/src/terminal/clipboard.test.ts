import { ClipboardAddon } from "@xterm/addon-clipboard";
import { Terminal } from "@xterm/xterm";
import { describe, expect, it, vi } from "vitest";
import { terminalClipboard } from "./clipboard";

const b64 = (text: string) => btoa(String.fromCharCode(...new TextEncoder().encode(text)));

function terminal(copyText: (text: string) => Promise<void>) {
  const term = new Terminal({ allowProposedApi: true });
  term.loadAddon(new ClipboardAddon(undefined, terminalClipboard(copyText)));
  return term;
}

const write = (term: Terminal, data: string) => new Promise<void>((resolve) => term.write(data, resolve));

describe("terminal clipboard", () => {
  it("copies what tmux sends, with its empty selection field", async () => {
    const copyText = vi.fn(async () => undefined);
    await write(terminal(copyText), `\x1b]52;;${b64("héllo from tmux")}\x07`);
    expect(copyText).toHaveBeenCalledWith("héllo from tmux");
  });

  it("copies to the system clipboard when asked by name", async () => {
    const copyText = vi.fn(async () => undefined);
    await write(terminal(copyText), `\x1b]52;c;${b64("named")}\x1b\\`);
    expect(copyText).toHaveBeenCalledWith("named");
  });

  it("ignores the primary selection and requests to clear", async () => {
    const copyText = vi.fn(async () => undefined);
    const term = terminal(copyText);
    await write(term, `\x1b]52;p;${b64("primary")}\x07`);
    await write(term, `\x1b]52;c;\x07`);
    expect(copyText).not.toHaveBeenCalled();
  });

  it("never reports this computer's clipboard to the session", async () => {
    const term = terminal(async () => undefined);
    const sent: string[] = [];
    term.onData((data) => sent.push(data));
    await write(term, `\x1b]52;c;?\x07`);
    expect(sent.join("")).not.toMatch(/[A-Za-z0-9+/]{4,}=*\x07/);
  });

  it("keeps the terminal working when the clipboard refuses", async () => {
    const term = terminal(async () => Promise.reject(new Error("denied")));
    await write(term, `\x1b]52;;${b64("x")}\x07after`);
    expect(term.buffer.active.getLine(0)?.translateToString(true)).toBe("after");
  });
});

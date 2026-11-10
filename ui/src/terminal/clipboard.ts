import type { ClipboardSelectionType, IClipboardProvider } from "@xterm/addon-clipboard";

/**
 * Where OSC 52 from the session goes. tmux sends it when you copy in tmux, such as by
 * dragging with the mouse, and it leaves the selection field empty (`ESC ] 52 ; ; …`),
 * which xterm's own provider ignores because it only accepts `c`.
 */
export function terminalClipboard(copyText: (text: string) => Promise<void>): IClipboardProvider {
  return {
    // A program in the session never reads this computer's clipboard.
    readText: () => "",
    async writeText(selection: ClipboardSelectionType | string, text: string) {
      // `p` is X11's primary selection, which a page can't set. Empty text asks to clear,
      // which isn't worth wiping what the user copied elsewhere.
      if (selection === "p" || !text) return;
      try {
        await copyText(text);
      } catch {
        // No clipboard here. The text is still in tmux's buffer.
      }
    },
  };
}

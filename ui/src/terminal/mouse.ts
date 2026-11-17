/**
 * Whether what xterm sends is only mouse reports: SGR (`ESC[<b;x;yM`), the
 * original X10 form (`ESC[M` and three bytes) or urxvt's (`ESC[b;x;yM`). A
 * terminal that isn't typed into still sends these, so wheel and finger
 * scrolling keep reaching tmux.
 */
const MOUSE = /^(?:\x1b\[<\d+;\d+;\d+[Mm]|\x1b\[M[\s\S]{3}|\x1b\[\d+;\d+;\d+M)+$/;

export function isMouseReport(data: string): boolean {
  return MOUSE.test(data);
}

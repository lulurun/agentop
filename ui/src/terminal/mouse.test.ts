import { describe, expect, it } from "vitest";
import { isMouseReport } from "./mouse";

describe("isMouseReport", () => {
  it("passes wheel and clicks in every encoding", () => {
    expect(isMouseReport("\x1b[<64;10;5M")).toBe(true);
    expect(isMouseReport("\x1b[<65;10;5M\x1b[<65;10;5M")).toBe(true);
    expect(isMouseReport("\x1b[<0;3;4m")).toBe(true);
    expect(isMouseReport("\x1b[M`!!")).toBe(true);
    expect(isMouseReport("\x1b[96;10;5M")).toBe(true);
  });

  it("stops keys", () => {
    for (const data of ["a", "3", "\r", "\x1b", "\x1b[A", "\x03", "\x1b[200~hi\x1b[201~", "\x1b[<64;10;5Mx", ""]) {
      expect(isMouseReport(data)).toBe(false);
    }
  });
});

import { describe, expect, it } from "vitest";
import { fromFile, toFile, defaultView } from "./layoutfile";
import { fitSides, MIN_MIDDLE, MIN_SIDE, splitAt } from "./sizes";

describe("fitSides", () => {
  it("keeps the widths set when they fit", () => {
    expect(fitSides(1600, 300, 400)).toEqual({ left: 300, right: 400 });
    expect(fitSides(1600, null, 400)).toEqual({ left: 0, right: 400 });
  });

  it("narrows both sides in proportion so the middle panel keeps its room, not below their minimum", () => {
    const { left, right } = fitSides(1000, 500, 300);
    expect(left + right).toBe(1000 - MIN_MIDDLE);
    expect(left - MIN_SIDE).toBeCloseTo((right - MIN_SIDE) * (500 - MIN_SIDE) / (300 - MIN_SIDE), -1);
    expect(fitSides(600, 500, 500)).toEqual({ left: MIN_SIDE, right: MIN_SIDE });
    expect(fitSides(700, 700, null)).toEqual({ left: 700 - MIN_MIDDLE, right: 0 });
  });
});

describe("splitAt", () => {
  it("is the pointer's place in the middle panel, each area keeping 200px", () => {
    expect(splitAt(500, 100, 800)).toBe(0.5);
    expect(splitAt(120, 100, 800)).toBe(0.25);
    expect(splitAt(5000, 100, 800)).toBe(0.75);
    // Too narrow for both minimums: the middle.
    expect(splitAt(0, 0, 300)).toBe(0.5);
  });
});

describe("sizes in layout.json", () => {
  it("are written only once set, and read back as written", () => {
    const plain = toFile(defaultView());
    expect(plain["left-panel"]).toEqual({ open: true });
    expect("split" in plain["mid-panel"]).toBe(false);
    const sized = { ...defaultView(), leftWidth: 300, rightWidth: 420, split: 0.35 };
    const file = toFile(sized);
    expect(file["left-panel"]).toEqual({ open: true, width: 300 });
    expect(file["mid-panel"].split).toBe(0.35);
    expect(fromFile(JSON.parse(JSON.stringify(file)))).toEqual(sized);
    expect(fromFile(JSON.parse(JSON.stringify(plain)))).toEqual(defaultView());
  });
});

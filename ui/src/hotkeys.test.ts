import { describe, expect, it } from "vitest";
import { layoutAction } from "./hotkeys";

const key = (code: string, mods: Partial<KeyboardEventInit> = { ctrlKey: true, shiftKey: true }) => new KeyboardEvent("keydown", { code, ...mods });

describe("layout keys", () => {
  it("number the header from the left, with Settings and the right toggle on 9 and 0", () => {
    expect(layoutAction(key("Digit1"))).toEqual({ do: "toggle-left" });
    expect(layoutAction(key("Digit2"))).toEqual({ do: "connections" });
    expect(layoutAction(key("Digit3"))).toEqual({ do: "feature", index: 0 });
    expect(layoutAction(key("Digit8"))).toEqual({ do: "feature", index: 5 });
    expect(layoutAction(key("Digit9"))).toEqual({ do: "settings" });
    expect(layoutAction(key("Digit0"))).toEqual({ do: "toggle-right" });
  });

  it("focus with Q W E R, pick tabs with A to K, close with L", () => {
    expect(layoutAction(key("KeyQ"))).toEqual({ do: "focus", target: "left-panel" });
    expect(layoutAction(key("KeyE"))).toEqual({ do: "focus", target: "right-area" });
    expect(layoutAction(key("KeyA"))).toEqual({ do: "tab", index: 0 });
    expect(layoutAction(key("KeyK"))).toEqual({ do: "tab", index: 7 });
    expect(layoutAction(key("KeyL"))).toEqual({ do: "close-tab" });
  });

  it("leave every other key alone", () => {
    expect(layoutAction(key("KeyC"))).toBeNull();
    expect(layoutAction(key("Slash"))).toBeNull();
    expect(layoutAction(key("KeyA", { ctrlKey: true }))).toBeNull();
    expect(layoutAction(key("KeyA", { ctrlKey: true, shiftKey: true, altKey: true }))).toBeNull();
  });
});

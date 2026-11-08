import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PluginInfo, Session } from "../api/client";
import { applyEvent, onPluginEvent } from "../store";
import { missingFromApp, pluginsFor } from "./registry";
import { PluginBoundary } from "./Slot";
import type { UiPlugin } from "./types";

// React only allows act() where it is told it runs in a test.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function info(id: string, fields: Partial<PluginInfo> = {}): PluginInfo {
  return { id, name: id, version: "1", description: "", state: "enabled", error: null, ui: ["session-badge"], ...fields };
}

const Badge = () => null;
const modules: Record<string, UiPlugin> = { badge: { sessionBadge: Badge }, other: { rightPanel: Badge } };

describe("pluginsFor", () => {
  it("needs the service to list it as enabled", () => {
    expect(pluginsFor("session-badge", [info("badge")], modules).map((p) => p.info.id)).toEqual(["badge"]);
    for (const state of ["disabled", "failed", "incompatible"] as const) {
      expect(pluginsFor("session-badge", [info("badge", { state })], modules)).toEqual([]);
    }
  });

  it("needs the app to have the plugin's UI", () => {
    expect(pluginsFor("session-badge", [info("unknown")], modules)).toEqual([]);
  });

  it("needs the manifest to name the slot, and the UI to fill it", () => {
    expect(pluginsFor("session-badge", [info("badge", { ui: [] })], modules)).toEqual([]);
    expect(pluginsFor("session-badge", [info("other")], modules)).toEqual([]);
  });
});

describe("missingFromApp", () => {
  it("is true only for a plugin with screens this app lacks", () => {
    expect(missingFromApp(info("unknown"), modules)).toBe(true);
    expect(missingFromApp(info("unknown", { ui: [] }), modules)).toBe(false);
    expect(missingFromApp(info("badge"), modules)).toBe(false);
  });
});

describe("applyEvent", () => {
  it("leaves sessions alone for events that aren't about sessions", () => {
    const sessions = { a: { id: "a" } as Session };
    expect(applyEvent(sessions, { type: "plugins" })).toBe(sessions);
    expect(applyEvent(sessions, { type: "plugin", id: "x", data: 1 })).toBe(sessions);
    expect(applyEvent(sessions, { type: "from-the-future" } as never)).toBe(sessions);
  });
});

describe("onPluginEvent", () => {
  it("returns a function that stops listening", () => {
    const listener = vi.fn();
    const stop = onPluginEvent("x", listener);
    stop();
    expect(listener).not.toHaveBeenCalled();
  });
});

describe("PluginBoundary", () => {
  let container: HTMLDivElement;
  afterEach(() => container?.remove());

  it("hides a plugin that throws and keeps the rest", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    container = document.createElement("div");
    document.body.append(container);
    const Throws = () => {
      throw new Error("plugin bug");
    };
    const Fine = () => createElement("span", null, "still here");
    act(() => {
      createRoot(container).render(
        createElement(
          "div",
          null,
          createElement(PluginBoundary, { id: "bad", children: createElement(Throws) }),
          createElement(PluginBoundary, { id: "good", children: createElement(Fine) }),
        ),
      );
    });
    expect(container.textContent).toBe("still here");
    expect(error).toHaveBeenCalled();
    error.mockRestore();
  });
});

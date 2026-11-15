import { describe, expect, it } from "vitest";
import { moveToOther, pinnedLayout, type Layout } from "../../tabs";
import { agentTab, dialogueOf, fileTab, showAgents, splitKey } from "./state";
import { fieldLabel } from "./NewDialogue";

const home = () => pinnedLayout({ kind: "plugin", plugin: "dialogue", view: "dialogues" });
const shown = (layout: Layout) => layout.groups.map((g) => g.tabs.find((t) => t.id === g.active)?.content);

describe("showing a dialogue's agents", () => {
  it("puts A on the left and B on the right, keeping the pinned tab", () => {
    const layout = showAgents(home(), "debate_x_1");
    expect(layout.groups).toHaveLength(2);
    expect(layout.groups[0].tabs[0].pinned).toBe(true);
    expect(shown(layout)).toEqual([agentTab("debate_x_1", "a"), agentTab("debate_x_1", "b")]);
    expect(layout.focused).toBe(0);
  });

  it("replaces the previous dialogue's agents instead of adding tabs", () => {
    const first = showAgents(home(), "one_1");
    const second = showAgents(first, "two_2");
    expect(second.groups.map((g) => g.tabs.length)).toEqual([2, 1]);
    expect(shown(second)).toEqual([agentTab("two_2", "a"), agentTab("two_2", "b")]);
    // The same tabs, so their place in each group is kept.
    expect(second.groups[1].tabs[0].id).toBe(first.groups[1].tabs[0].id);
  });

  it("shows an agent already open where it is", () => {
    let layout = showAgents(home(), "one_1");
    // B is moved to the left group by the user.
    layout = moveToOther(layout, 1, layout.groups[1].tabs[0].id);
    expect(layout.groups).toHaveLength(1);
    const again = showAgents(layout, "one_1");
    expect(again.groups[0].tabs.filter((t) => t.content.kind === "plugin" && t.content.view === "agent")).toHaveLength(2);
  });

  it("keeps files open beside them", () => {
    let layout = showAgents(home(), "one_1");
    layout = { ...layout, groups: [layout.groups[0], { ...layout.groups[1], tabs: [...layout.groups[1].tabs, { id: "f", content: fileTab("one_1", "deliverables/a.md") }] }] };
    const next = showAgents(layout, "two_2");
    expect(next.groups[1].tabs.map((t) => t.content)).toEqual([agentTab("two_2", "b"), fileTab("one_1", "deliverables/a.md")]);
  });
});

describe("tab keys", () => {
  it("split at the first colon, so a file path may hold more", () => {
    expect(splitKey("debate_x_1:deliverables/a:b.md")).toEqual(["debate_x_1", "deliverables/a:b.md"]);
    expect(dialogueOf(fileTab("debate_x_1", "p.md"))).toBe("debate_x_1");
    expect(dialogueOf({ kind: "plugin", plugin: "dialogue", view: "dialogues" })).toBeNull();
    expect(dialogueOf({ kind: "session", sessionId: "x" })).toBeNull();
  });
});

describe("the wizard's field labels", () => {
  it("read as words", () => {
    expect(fieldLabel("acceptance_criteria")).toBe("Acceptance criteria");
    expect(fieldLabel("title")).toBe("Title");
  });
});

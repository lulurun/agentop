import { describe, expect, it } from "vitest";
import { defaultView, fromFile, LayoutSync, toFile, type LayoutFile, type LayoutIO, type ProjectView } from "./layoutfile";

const view: ProjectView = {
  layout: {
    left: {
      tabs: [
        { id: "start", content: { kind: "start" } },
        { id: "t1", content: { kind: "session", sessionId: "dev-a1" } },
      ],
      active: "t1",
    },
    right: { tabs: [{ id: "t2", content: { kind: "file", file: { sessionId: "dev-a1", source: "workdir", path: "x.py", view: "diff" } } }], active: "t2" },
    focused: "right",
  },
  leftOpen: false,
  rightOpen: true,
};

describe("the layout file", () => {
  it("has the panels in the owner's order and reads back as it was", () => {
    const file = toFile(view);
    expect(Object.keys(file)).toEqual(["version", "left-panel", "mid-panel", "right-panel", "focused"]);
    expect(file["left-panel"]).toEqual({ open: false });
    expect(file["mid-panel"].left).toEqual({ open: true, tabs: view.layout.left!.tabs, active: "t1" });
    expect(fromFile(JSON.parse(JSON.stringify(file)))).toEqual(view);
  });

  it("writes an area that isn't there as off, with no tabs", () => {
    const file = toFile({ ...view, layout: { ...view.layout, right: null, focused: "left" } });
    expect(file["mid-panel"].right).toEqual({ open: false, tabs: [], active: null });
  });

  it("writes a shut area as off with its tabs, and reads it back shut", () => {
    const shut: ProjectView = { ...view, layout: { ...view.layout, right: { ...view.layout.right!, hidden: true }, focused: "left" } };
    const file = toFile(shut);
    expect(file["mid-panel"].right).toEqual({ open: false, tabs: view.layout.right!.tabs, active: "t2" });
    expect(fromFile(JSON.parse(JSON.stringify(file)))).toEqual(shut);
  });

  it("opens a shut area when no other is shown", () => {
    const file = toFile({ ...view, layout: { left: null, right: { ...view.layout.right!, hidden: true }, focused: "left" } });
    const read = fromFile(JSON.parse(JSON.stringify(file))) as ProjectView;
    expect(read.layout.right).toEqual(view.layout.right);
    expect(read.layout.focused).toBe("right");
  });

  it("leaves no pinned flag in the file: the app pins its own tabs", () => {
    const pinned = { ...view, layout: { ...view.layout, left: { tabs: [{ id: "home", content: { kind: "status" as const }, pinned: true }], active: "home" } } };
    expect(toFile(pinned)["mid-panel"].left.tabs[0]).toEqual({ id: "home", content: { kind: "status" } });
  });

  it("is the defaults when missing or not a layout", () => {
    for (const bad of [null, undefined, "x", 3, [], {}, { version: "1" }, { version: 0 }]) expect(fromFile(bad)).toBeNull();
    expect(defaultView()).toEqual({ layout: { left: null, right: null, focused: "left" }, leftOpen: true, rightOpen: true });
  });

  it("is never written over when a newer app wrote it", () => {
    expect(fromFile({ ...toFile(view), version: 2 })).toBe("newer");
  });

  it("keeps what it can of a file edited by hand", () => {
    const read = fromFile({
      version: 1,
      "left-panel": {},
      "mid-panel": {
        left: {
          open: true,
          tabs: [
            { id: "a", content: { kind: "session", sessionId: "s1", extra: 1 } },
            { id: "a", content: { kind: "status" } },
            { content: { kind: "past", historyId: "h" } },
            { id: "x", content: { kind: "terminal" } },
            { id: "y", content: { kind: "file", file: { sessionId: "s", source: "elsewhere", path: "p" } } },
            null,
          ],
          active: "missing",
        },
        right: { open: false, tabs: [{ id: "r", content: { kind: "status" } }], active: "r" },
      },
      focused: "right",
    });
    expect(read).not.toBeNull();
    const { layout, leftOpen, rightOpen } = read as ProjectView;
    expect([leftOpen, rightOpen]).toEqual([true, true]);
    expect(layout.left!.tabs.map((t) => t.content)).toEqual([{ kind: "session", sessionId: "s1" }, { kind: "status" }, { kind: "past", historyId: "h" }]);
    const ids = layout.left!.tabs.map((t) => t.id);
    expect(ids[0]).toBe("a");
    expect(new Set(ids).size).toBe(3);
    expect(layout.left!.active).toBe("a");
    // A shut area keeps its tabs but shows nothing, so the focus goes to the area shown.
    expect(layout.right).toEqual({ tabs: [{ id: "r", content: { kind: "status" } }], active: "r", hidden: true });
    expect(layout.focused).toBe("left");
  });
});

/** A service whose replies come when the test says. */
function fakeService() {
  const files = new Map<string, unknown>();
  const log: string[] = [];
  const held: (() => void)[] = [];
  let holding = false;
  const wait = async () => {
    if (holding) await new Promise<void>((resolve) => held.push(resolve));
  };
  const io: LayoutIO = {
    async read(connection, project) {
      log.push(`read ${connection}/${project}`);
      const value = files.get(`${connection}/${project}`) ?? null;
      await wait();
      return value;
    },
    async write(connection, project, file) {
      log.push(`write ${connection}/${project} ${file.focused}`);
      await wait();
      files.set(`${connection}/${project}`, file);
    },
  };
  return {
    io,
    files,
    log,
    hold: () => (holding = true),
    release: () => {
      holding = false;
      held.splice(0).forEach((r) => r());
    },
  };
}

const file = (focused: "left" | "right"): LayoutFile => ({ ...toFile(defaultView()), focused });
const tick = () => new Promise((r) => setTimeout(r, 0));

describe("loading and saving", () => {
  it("writes nothing before the file was read", async () => {
    const service = fakeService();
    const sync = new LayoutSync(service.io, 0);
    sync.edited("c", "a", file("right"));
    await sync.flush("c", "a");
    expect(service.log).toEqual([]);
    sync.allowWrites("c", "a");
    await sync.flush("c", "a");
    expect(service.files.get("c/a")).toEqual(file("right"));
  });

  it("calls a load stale when the project was edited while it was on its way", async () => {
    const service = fakeService();
    const sync = new LayoutSync(service.io, 10_000);
    sync.allowWrites("c", "a");
    service.files.set("c/a", file("left"));
    service.hold();
    const loading = sync.load("c", "a");
    await tick();
    sync.edited("c", "a", file("right"));
    service.release();
    expect(await loading).toEqual({ kind: "stale" });
    await sync.flush("c", "a");
    expect(service.files.get("c/a")).toEqual(file("right"));
  });

  it("reads after a save of the same project still on its way, so it reads what was saved", async () => {
    const service = fakeService();
    const sync = new LayoutSync(service.io, 10_000);
    sync.allowWrites("c", "a");
    sync.edited("c", "a", file("right"));
    service.hold();
    const saving = sync.flush("c", "a");
    const loading = sync.load("c", "a");
    await tick();
    service.release();
    await saving;
    expect(await loading).toEqual({ kind: "loaded", file: file("right") });
    expect(service.log).toEqual(["write c/a right", "read c/a"]);
  });

  it("keeps saves of one project in order, the last one winning", async () => {
    const service = fakeService();
    const sync = new LayoutSync(service.io, 10_000);
    sync.allowWrites("c", "a");
    service.hold();
    sync.edited("c", "a", file("left"));
    const first = sync.flush("c", "a");
    sync.edited("c", "a", file("right"));
    const second = sync.flush("c", "a");
    service.release();
    await Promise.all([first, second]);
    await tick();
    service.release();
    await second;
    expect(service.files.get("c/a")).toEqual(file("right"));
    expect(service.log).toEqual(["write c/a left", "write c/a right"]);
  });

  it("keeps projects and connections apart", async () => {
    const service = fakeService();
    const sync = new LayoutSync(service.io, 10_000);
    for (const [c, p] of [["c1", "a"], ["c1", "b"], ["c2", "a"]]) sync.allowWrites(c, p);
    sync.edited("c1", "a", file("right"));
    sync.edited("c2", "a", file("left"));
    await sync.flushAll("c1");
    expect([...service.files.keys()]).toEqual(["c1/a"]);
    await sync.flushAll();
    expect(service.files.get("c2/a")).toEqual(file("left"));
    expect(service.files.has("c1/b")).toBe(false);
  });

  it("tries a failed save again on the next flush", async () => {
    const service = fakeService();
    let fail = true;
    const sync = new LayoutSync(
      {
        read: service.io.read,
        write: async (...args) => {
          if (fail) throw new Error("away");
          await service.io.write(...args);
        },
      },
      10_000,
    );
    sync.allowWrites("c", "a");
    sync.edited("c", "a", file("right"));
    await sync.flush("c", "a");
    expect(service.files.size).toBe(0);
    fail = false;
    await sync.flush("c", "a");
    expect(service.files.get("c/a")).toEqual(file("right"));
  });

  it("saves on its own after a moment", async () => {
    const service = fakeService();
    const sync = new LayoutSync(service.io, 5);
    sync.allowWrites("c", "a");
    sync.edited("c", "a", file("left"));
    sync.edited("c", "a", file("right"));
    await new Promise((r) => setTimeout(r, 30));
    expect(service.log).toEqual(["write c/a right"]);
  });

  it("an unsaved edit made before a load wins over the file, unless discarded", async () => {
    const service = fakeService();
    service.files.set("c/a", file("left"));
    const sync = new LayoutSync(service.io, 10_000);
    sync.edited("c", "a", file("right"));
    expect(await sync.load("c", "a")).toEqual({ kind: "stale" });
    sync.edited("c", "b", file("right"));
    sync.discard("c", "b");
    expect((await sync.load("c", "b")).kind).toBe("loaded");
  });
});

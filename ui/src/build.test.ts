import { describe, expect, it } from "vitest";
import { ownBuild, pageIsOutdated } from "./build";

describe("the page's build", () => {
  it("is the entry script's file name", () => {
    const doc = new DOMParser().parseFromString('<script type="module" crossorigin src="./assets/index-AbC123.js"></script>', "text/html");
    expect(ownBuild(doc)).toBe("index-AbC123.js");
    expect(ownBuild(new DOMParser().parseFromString('<script type="module" src="/src/main.tsx"></script>', "text/html"))).toBeNull();
  });

  it("is out of date only against the service that served it", () => {
    const here = "https://box.example/agentop/";
    expect(pageIsOutdated("https://box.example/agentop", "index-new.js", "index-old.js", here)).toBe(true);
    expect(pageIsOutdated("https://box.example/agentop", "index-old.js", "index-old.js", here)).toBe(false);
    expect(pageIsOutdated("https://other.example", "index-new.js", "index-old.js", here)).toBe(false);
    expect(pageIsOutdated("https://box.example", null, "index-old.js", here)).toBe(false);
    expect(pageIsOutdated("https://box.example", "index-new.js", null, here)).toBe(false);
  });
});

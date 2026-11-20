import { describe, expect, it } from "vitest";
import { ownBuild } from "./build";

describe("the page's build", () => {
  it("is the entry script's file name", () => {
    const doc = new DOMParser().parseFromString('<script type="module" crossorigin src="./assets/index-AbC123.js"></script>', "text/html");
    expect(ownBuild(doc)).toBe("index-AbC123.js");
    expect(ownBuild(new DOMParser().parseFromString('<script type="module" src="/src/main.tsx"></script>', "text/html"))).toBeNull();
  });

});

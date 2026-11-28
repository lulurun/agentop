import { describe, expect, it } from "vitest";
import { fileQuery, isImage, isMarkdown, languageFor, linkTarget, resolve } from "./paths";

describe("fileQuery", () => {
  it("asks for a project's file by its project and any other by its session", () => {
    expect(fileQuery({ sessionId: "alpha", source: "project", path: "results/P1_M1A1_a.md" }).toString())
      .toBe("project=alpha&source=project&path=results%2FP1_M1A1_a.md");
    expect(fileQuery({ sessionId: "s1", source: "workdir", path: "a b" }).toString()).toBe("session=s1&source=workdir&path=a+b");
  });
});

describe("isMarkdown", () => {
  it("knows Markdown by its extension", () => {
    expect(isMarkdown("README.md")).toBe(true);
    expect(isMarkdown("docs/Guide.MARKDOWN")).toBe(true);
    expect(isMarkdown("notes.txt")).toBe(false);
    expect(isMarkdown("md")).toBe(false);
  });
});

describe("isImage", () => {
  it("knows an image by its extension", () => {
    expect(isImage("shot.png")).toBe(true);
    expect(isImage("docs/Photo.JPEG")).toBe(true);
    expect(isImage("logo.svg")).toBe(true);
    expect(isImage("notes.txt")).toBe(false);
    expect(isImage("png")).toBe(false);
  });
});

describe("resolve", () => {
  it("resolves beside the file", () => {
    expect(resolve("docs/a.md", "b.md")).toBe("docs/b.md");
    expect(resolve("docs/a.md", "./img/x.png")).toBe("docs/img/x.png");
    expect(resolve("docs/a.md", "../README.md")).toBe("README.md");
    expect(resolve("a.md", "/docs/b.md")).toBe("docs/b.md");
  });

  it("goes nowhere above the root", () => {
    expect(resolve("a.md", "../b.md")).toBeNull();
    expect(resolve("docs/a.md", "../../b.md")).toBeNull();
    expect(resolve("docs/a.md", ".")).toBe("docs");
    expect(resolve("a.md", ".")).toBeNull();
  });
});

describe("linkTarget", () => {
  it("tells anchors, other sites, and files apart", () => {
    expect(linkTarget("docs/a.md", "#set-up")).toEqual({ kind: "anchor", id: "set-up" });
    expect(linkTarget("docs/a.md", "https://example.com/x")).toEqual({ kind: "external", href: "https://example.com/x" });
    expect(linkTarget("docs/a.md", "mailto:me@example.com").kind).toBe("external");
    expect(linkTarget("docs/a.md", "b.md#usage")).toEqual({ kind: "file", path: "docs/b.md", anchor: "usage" });
    expect(linkTarget("docs/a.md", "My%20Notes.md?plain=1")).toEqual({ kind: "file", path: "docs/My Notes.md", anchor: null });
  });

  it("is nothing when empty or above the root", () => {
    expect(linkTarget("a.md", "").kind).toBe("none");
    expect(linkTarget("a.md", "../up.md").kind).toBe("none");
  });
});

describe("languageFor", () => {
  it("picks a language by extension, ignoring case", () => {
    expect(languageFor("src/app.py")).toBe("python");
    expect(languageFor("ui/src/tabs.TS")).toBe("typescript");
    expect(languageFor("pyproject.toml")).toBe("ini");
    expect(languageFor("deploy/agentop-api.service")).toBe("ini");
  });

  it("knows some files by name", () => {
    expect(languageFor("Dockerfile")).toBe("dockerfile");
    expect(languageFor("docker/Dockerfile.dev")).toBe("dockerfile");
    expect(languageFor("Makefile")).toBe("makefile");
    expect(languageFor("home/.zshrc")).toBe("bash");
  });

  it("leaves anything else plain", () => {
    expect(languageFor("notes.txt")).toBeNull();
    expect(languageFor("README")).toBeNull();
    expect(languageFor(".env")).toBeNull();
    expect(languageFor("guide.md")).toBeNull();
  });
});

describe("the registered grammars", () => {
  it("cover every language a file can map to", async () => {
    const { lowlight } = await import("./highlight");
    const { LANGUAGE_NAMES } = await import("./paths");
    for (const name of LANGUAGE_NAMES) expect(lowlight.registered(name), name).toBe(true);
  });
});

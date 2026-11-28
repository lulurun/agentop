/** Where a link in a Markdown file goes, and how the service is asked for a file. Pure, so it is tested on its own. */
import type { FileRef } from "../../tabs";

/** The query /file and /image take: a project's file by its project, any other by its session. */
export function fileQuery(file: Pick<FileRef, "sessionId" | "source" | "path">): URLSearchParams {
  const owner = file.source === "project" ? "project" : "session";
  return new URLSearchParams({ [owner]: file.sessionId, source: file.source, path: file.path });
}

const MARKDOWN = /\.(md|markdown|mdown|mkd|mdx)$/i;
// The kinds the service's /image serves (IMAGE_TYPES in the viewer plugin).
const IMAGE = /\.(png|jpe?g|gif|webp|avif|bmp|ico|svg)$/i;
const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

export function isMarkdown(path: string): boolean {
  return MARKDOWN.test(path);
}

export function isImage(path: string): boolean {
  return IMAGE.test(path);
}

export type LinkTarget =
  | { kind: "anchor"; id: string }
  | { kind: "external"; href: string }
  | { kind: "file"; path: string; anchor: string | null }
  | { kind: "none" };

/**
 * A link in the Markdown file at `from`. A relative link is a file beside it,
 * from the same source; one that climbs above the source's root goes nowhere.
 * A link starting with "/" starts at the root, as on GitHub.
 */
export function linkTarget(from: string, href: string): LinkTarget {
  const link = href.trim();
  if (!link) return { kind: "none" };
  if (link.startsWith("#")) return { kind: "anchor", id: decode(link.slice(1)) };
  if (SCHEME.test(link) || link.startsWith("//")) return { kind: "external", href: link };
  const hash = link.indexOf("#");
  const anchor = hash >= 0 ? decode(link.slice(hash + 1)) || null : null;
  const bare = (hash >= 0 ? link.slice(0, hash) : link).split("?")[0];
  const path = resolve(from, decode(bare));
  return path === null ? { kind: "none" } : { kind: "file", path, anchor };
}

/** `target` resolved against the folder holding `from`, or null when it leaves the root. */
export function resolve(from: string, target: string): string | null {
  const parts = target.startsWith("/") ? [] : from.split("/").slice(0, -1);
  for (const part of target.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (parts.length === 0) return null;
      parts.pop();
    } else {
      parts.push(part);
    }
  }
  return parts.length ? parts.join("/") : null;
}

function decode(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

// The languages the viewer colours (registered in highlight.ts), by file extension.
// Anything else is shown as plain text.
const LANGUAGE_BY_EXTENSION: Record<string, string> = {
  py: "python", pyi: "python",
  js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
  ts: "typescript", mts: "typescript", cts: "typescript", tsx: "typescript",
  json: "json", jsonl: "json", jsonc: "json",
  toml: "ini", ini: "ini", cfg: "ini", conf: "ini", service: "ini", timer: "ini", desktop: "ini",
  yaml: "yaml", yml: "yaml",
  sh: "bash", bash: "bash", zsh: "bash",
  go: "go",
  rs: "rust",
  html: "xml", htm: "xml", xml: "xml", svg: "xml", plist: "xml",
  css: "css",
  sql: "sql",
  diff: "diff", patch: "diff",
  mk: "makefile",
  c: "c", h: "c",
  cc: "cpp", cpp: "cpp", cxx: "cpp", hpp: "cpp", hh: "cpp",
  java: "java",
};

const LANGUAGE_BY_NAME: Record<string, string> = {
  dockerfile: "dockerfile", containerfile: "dockerfile",
  makefile: "makefile", gnumakefile: "makefile",
  ".bashrc": "bash", ".zshrc": "bash", ".profile": "bash", ".bash_profile": "bash",
  ".gitconfig": "ini", ".editorconfig": "ini",
};

/** Every language a file can map to, so a test can check each has a grammar. */
export const LANGUAGE_NAMES = [...new Set([...Object.values(LANGUAGE_BY_EXTENSION), ...Object.values(LANGUAGE_BY_NAME)])];

/** The language to colour a file as, or null to show it plain. */
export function languageFor(path: string): string | null {
  const name = (path.split("/").pop() ?? "").toLowerCase();
  if (LANGUAGE_BY_NAME[name]) return LANGUAGE_BY_NAME[name];
  if (name.startsWith("dockerfile.")) return "dockerfile";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? (LANGUAGE_BY_EXTENSION[name.slice(dot + 1)] ?? null) : null;
}

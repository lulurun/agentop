/** Where a link in a Markdown file goes. Pure, so it is tested on its own. */

const MARKDOWN = /\.(md|markdown|mdown|mkd|mdx)$/i;
const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

export function isMarkdown(path: string): boolean {
  return MARKDOWN.test(path);
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

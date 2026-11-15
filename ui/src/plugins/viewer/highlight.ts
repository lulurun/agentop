/**
 * The languages the viewer colours: highlight.js grammars, each registered by
 * hand so only these are in the app. Shared by the text view (Code.tsx) and
 * code blocks in Markdown. Loaded only when a file is opened.
 *
 * To add a language: import its grammar here, and map its file extensions in
 * paths.ts (`languageFor`).
 */

import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import makefile from "highlight.js/lib/languages/makefile";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import shell from "highlight.js/lib/languages/shell";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import type { Element, Root } from "hast";
import { createLowlight } from "lowlight";

export const LANGUAGES = {
  bash, c, cpp, css, diff, dockerfile, go, ini, java, javascript, json,
  makefile, markdown, python, rust, shell, sql, typescript, xml, yaml,
};

export const lowlight = createLowlight(LANGUAGES);

/** Bigger than this is shown plain: colouring it would hold up the page. */
export const MAX_HIGHLIGHT_CHARS = 512 * 1024;

/**
 * A rehype step that colours fenced code blocks (`pre > code.language-x`)
 * in a language above. It runs after sanitizing and only adds spans with
 * hljs class names, so nothing it makes can carry markup from the file.
 */
export function rehypeHighlight() {
  return (tree: Root) => visit(tree);
}

function visit(node: Root | Element): void {
  for (const child of node.children) {
    if (child.type !== "element") continue;
    if (child.tagName === "pre") {
      const code = child.children.find((c): c is Element => c.type === "element" && c.tagName === "code");
      if (code) colour(code);
    } else {
      visit(child);
    }
  }
}

function colour(code: Element): void {
  const classes = Array.isArray(code.properties?.className) ? code.properties.className.map(String) : [];
  const language = classes.find((c) => c.startsWith("language-"))?.slice("language-".length).toLowerCase();
  if (!language || !lowlight.registered(language)) return;
  const text = code.children.map((c) => (c.type === "text" ? c.value : "")).join("");
  if (text.length > MAX_HIGHLIGHT_CHARS) return;
  try {
    code.children = lowlight.highlight(language, text).children as Element["children"];
    code.properties = { ...code.properties, className: [...classes, "hljs"] };
  } catch {
    // Left plain.
  }
}

/** A text file's contents, coloured by its language. Loaded only when one is opened. */

import type { Element, ElementContent, Root } from "hast";
import { useMemo, type ReactNode } from "react";
import { lowlight, MAX_HIGHLIGHT_CHARS } from "./highlight";

function render(nodes: (ElementContent | Root["children"][number])[]): ReactNode[] {
  return nodes.map((node, i) => {
    if (node.type === "text") return node.value;
    if (node.type !== "element") return null;
    const element = node as Element;
    const className = element.properties?.className;
    return (
      <span key={i} className={Array.isArray(className) ? className.join(" ") : undefined}>
        {render(element.children)}
      </span>
    );
  });
}

export default function Code({ text, language }: { text: string; language: string }) {
  const children = useMemo(() => {
    if (text.length > MAX_HIGHLIGHT_CHARS || !lowlight.registered(language)) return text;
    try {
      return render(lowlight.highlight(language, text).children);
    } catch {
      return text;
    }
  }, [text, language]);
  return <>{children}</>;
}

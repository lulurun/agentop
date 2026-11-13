/**
 * A Markdown file, drawn. Loaded only when one is opened, so the libraries
 * stay out of the app's first download.
 *
 * HTML in the file is drawn as GitHub draws it: kept, then cleaned with
 * GitHub's own list of what's allowed, so no script, style, frame or event
 * handler gets into the page. react-markdown also drops javascript: links.
 */

import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";
import type { FileRef } from "../../tabs";
import type { PluginContext } from "../types";
import { linkTarget } from "./paths";

// Ids get a prefix, so one in the file can't clash with the app's own elements:
// headings get PREFIX, and ids written in the file's HTML get the sanitizer's.
const PREFIX = "md-";
const HTML_PREFIX = defaultSchema.clobberPrefix ?? "user-content-";

interface Image {
  type: string;
  data: string;
}

export interface MarkdownProps {
  text: string;
  file: FileRef;
  plugin: PluginContext;
  label: string;
  /** Open another file from the same source, beside this one when `side`. */
  onOpen(path: string, side: boolean): void;
}

export default function Markdown({ text, file, plugin, label, onOpen }: MarkdownProps) {
  const scroller = useRef<HTMLDivElement>(null);

  function jump(id: string) {
    const ids = [PREFIX + id, HTML_PREFIX + id].map((each) => `[id="${CSS.escape(each)}"], [name="${CSS.escape(each)}"]`);
    const target = scroller.current?.querySelector(ids.join(", "));
    target?.scrollIntoView({ block: "start" });
  }

  const components: Components = {
    a({ href, children }) {
      const target = linkTarget(file.path, href ?? "");
      if (target.kind === "external") {
        return (
          <a href={target.href} target="_blank" rel="noreferrer noopener">
            {children}
          </a>
        );
      }
      if (target.kind === "none") return <span className="markdown-deadlink">{children}</span>;
      const follow = (event: MouseEvent) => {
        event.preventDefault();
        if (target.kind === "anchor") jump(target.id);
        else onOpen(target.path, event.ctrlKey || event.metaKey);
      };
      return (
        <a href={href} onClick={follow} title={target.kind === "file" ? target.path : undefined}>
          {children}
        </a>
      );
    },
    img({ src, alt }) {
      const source = typeof src === "string" ? src : "";
      const target = linkTarget(file.path, source);
      if (target.kind === "external") {
        return <img src={target.href} alt={alt ?? ""} loading="lazy" referrerPolicy="no-referrer" />;
      }
      if (target.kind === "file") return <LocalImage file={{ ...file, path: target.path }} alt={alt ?? ""} plugin={plugin} />;
      return <MissingImage alt={alt ?? ""} why="no such image" />;
    },
    // Wide tables scroll on their own instead of widening the page.
    table({ children }) {
      return (
        <div className="markdown-table">
          <table>{children}</table>
        </div>
      );
    },
  };

  return (
    <div className="markdown-scroll" ref={scroller} tabIndex={0} aria-label={label}>
      <article className="markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw, rehypeSanitize, [rehypeSlug, { prefix: PREFIX }]]} components={components}>
          {text}
        </ReactMarkdown>
      </article>
    </div>
  );
}

/** An image beside the file. The page can't put its token on an <img>, so it comes as data. */
function LocalImage({ file, alt, plugin }: { file: FileRef; alt: string; plugin: PluginContext }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    const query = new URLSearchParams({ session: file.sessionId, source: file.source, path: file.path });
    plugin.client.get<Image>(`/image?${query}`).then(
      (image) => current && setUrl(`data:${image.type};base64,${image.data}`),
      (error: unknown) => current && setFailed(error instanceof Error ? error.message : String(error)),
    );
    return () => {
      current = false;
    };
  }, [file.sessionId, file.source, file.path, plugin.client]);
  if (failed) return <MissingImage alt={alt} why={failed} />;
  if (!url) return <span className="markdown-image-wait">{alt}</span>;
  return <img src={url} alt={alt} title={file.path} />;
}

function MissingImage({ alt, why }: { alt: string; why: string }): ReactNode {
  return (
    <span className="markdown-image-missing" title={why}>
      {alt || "Image"} ({why})
    </span>
  );
}

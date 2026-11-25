/**
 * A track document drawn as Markdown, loaded only when one is shown. Without
 * rehype-raw its HTML is never drawn, and images show as their text, so nothing is fetched.
 */
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  a({ href, children }) {
    return (
      <a href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    );
  },
  img({ alt, src }) {
    return <span className="progress-image">{alt || src}</span>;
  },
  table({ children }) {
    return (
      <div className="markdown-table">
        <table>{children}</table>
      </div>
    );
  },
};

export default function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown progress-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

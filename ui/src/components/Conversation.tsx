import { useLayoutEffect, useRef, type ReactNode } from "react";

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  at?: number | null;
}

const URL_PATTERN = /https?:\/\/[^\s<>"'`)\]]+/g;

/** Text with its links made clickable. */
export function Linked({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const url = match[0].replace(/[.,;:!?]+$/, "");
    const start = match.index ?? 0;
    parts.push(text.slice(last, start));
    parts.push(
      <a key={start} href={url} target="_blank" rel="noreferrer noopener">
        {url}
      </a>,
    );
    last = start + url.length;
  }
  parts.push(text.slice(last));
  return <>{parts}</>;
}

/** Prompts on the right and replies on the left, oldest first, opened at the latest. */
export function ChatView({ messages, total, label }: { messages: ChatMessage[]; total: number; label: string }) {
  const scroller = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="placard">
        <h2>No messages yet</h2>
        <p>This transcript has no prompts or replies in it.</p>
      </div>
    );
  }
  const hidden = total - messages.length;
  return (
    <div className="conversation chat" ref={scroller} tabIndex={0} aria-label={label}>
      {hidden > 0 && (
        <p className="viewer-note" role="status">
          Showing the latest {messages.length} of {total} messages.
        </p>
      )}
      <ol>
        {messages.map((m, index) => (
          <li key={index} className="message" data-role={m.role}>
            <div className="message-head">
              <strong>{m.role === "user" ? "You" : "Agent"}</strong>
              {m.at != null && <time dateTime={new Date(m.at * 1000).toISOString()}>{new Date(m.at * 1000).toLocaleString()}</time>}
            </div>
            <div className="message-text">
              <Linked text={m.text} />
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

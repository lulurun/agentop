import type { ReactNode } from "react";
import { useApp } from "../store";

/**
 * A section of a left or right panel (docs/0_10_ui.md): a title that folds it shut or
 * open, remembered in this browser by `id`, which is unique across the app.
 */
export function Section({
  id,
  title,
  hint,
  extra,
  children,
}: {
  id: string;
  title: string;
  hint?: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  const folded = useApp((s) => s.folded.includes(id));
  const setFolded = useApp((s) => s.setFolded);
  const body = `section-${id.replace(/[^A-Za-z0-9_-]/g, "-")}`;
  return (
    <section className="panel-section" aria-label={title} data-folded={folded || undefined}>
      <h3 className="section-title">
        <button type="button" className="fold-toggle" aria-expanded={!folded} aria-controls={body} title={hint} onClick={() => setFolded(id, !folded)}>
          <span className="fold-chevron" aria-hidden="true" />
          <span className="fold-title">{title}</span>
          {extra}
        </button>
      </h3>
      <div id={body} className="section-body" hidden={folded}>
        {children}
      </div>
    </section>
  );
}

import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

export interface SideTab {
  id: string;
  label: string;
  /** Shown with a close button when given. */
  onClose?(): void;
  body: ReactNode;
}

/**
 * Tabs at the top of a side panel, styled as the main area's; a lone tab shows without a bar.
 * Every body stays mounted at its place, so a tab keeps its state while hidden or alone.
 */
export function SidePanelTabs({ label, tabs, active, onSelect }: { label: string; tabs: SideTab[]; active: string; onSelect(id: string): void }) {
  const base = useId();
  const list = useRef<HTMLDivElement>(null);

  function onKeyDown(event: KeyboardEvent) {
    const at = tabs.findIndex((t) => t.id === active);
    const step = { ArrowRight: 1, ArrowLeft: -1, Home: -at, End: tabs.length - 1 - at }[event.key];
    if (step === undefined) return;
    event.preventDefault();
    const next = tabs[(at + step + tabs.length) % tabs.length];
    onSelect(next.id);
    list.current?.querySelector<HTMLElement>(`[data-tab="${next.id}"]`)?.focus();
  }

  const bar = tabs.length > 1;
  return (
    <div className="side-tabs">
      {bar && (
        <div className="tab-bar side-tab-bar">
          <div ref={list} className="tab-list" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
            {tabs.map((tab) => (
              <div key={tab.id} className="tab" role="presentation" data-active={tab.id === active || undefined}>
                <button
                  type="button"
                  role="tab"
                  id={`${base}-${tab.id}`}
                  data-tab={tab.id}
                  className="tab-label"
                  aria-selected={tab.id === active}
                  aria-controls={`${base}-${tab.id}-panel`}
                  tabIndex={tab.id === active ? 0 : -1}
                  onClick={() => onSelect(tab.id)}
                  onAuxClick={(event) => {
                    if (event.button === 1) tab.onClose?.();
                  }}
                >
                  {tab.label}
                </button>
                {tab.onClose && (
                  <button type="button" className="tab-close" aria-label={`Close ${tab.label}`} title="Close" onClick={tab.onClose}>
                    ×
                  </button>
              )}
            </div>
          ))}
        </div>
      </div>
      )}
      {tabs.map((tab) => (
        <div
          key={tab.id}
          id={`${base}-${tab.id}-panel`}
          role={bar ? "tabpanel" : undefined}
          aria-labelledby={bar ? `${base}-${tab.id}` : undefined}
          hidden={bar && tab.id !== active}
        >
          {tab.body}
        </div>
      ))}
    </div>
  );
}

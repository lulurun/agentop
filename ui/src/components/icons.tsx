export type LayoutPart = "left" | "mid-left" | "mid-right" | "right";

const PART_X: Record<LayoutPart, number> = { left: 2.5, "mid-left": 4.75, "mid-right": 8.25, right: 10.5 };

/** The window with one part marked: a side panel at its edge, an area as a half of the middle. */
export function ToolbarIcon({ side, filled }: { side: LayoutPart; filled: boolean }) {
  const mid = side === "mid-left" || side === "mid-right";
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="1" y="2" width="14" height="12" rx="2" stroke="currentColor" />
      {mid && <line x1="8" y1="2" x2="8" y2="14" stroke="currentColor" strokeWidth="0.6" strokeDasharray="1 1" />}
      <rect x={PART_X[side]} y="3.5" width="3" height="9" rx="0.5" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth={filled ? 0 : 0.8} />
    </svg>
  );
}

export function StatusMark({ status }: { status: string }) {
  return <span className={`mark mark-${status}`} aria-hidden="true" />;
}

export function GearIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M6.7 1.5h2.6l.4 1.9 1.2.7 1.8-.6 1.3 2.2-1.4 1.3v1.4l1.4 1.3-1.3 2.2-1.8-.6-1.2.7-.4 1.9H6.7l-.4-1.9-1.2-.7-1.8.6L2 9.7l1.4-1.3V7L2 5.7l1.3-2.2 1.8.6 1.2-.7z"
        stroke="currentColor"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="8" r="2" stroke="currentColor" />
    </svg>
  );
}


export function ReloadIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M13 8a5 5 0 1 1-1.5-3.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M12 1.8v3h-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

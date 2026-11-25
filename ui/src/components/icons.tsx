export function ToolbarIcon({ side, filled }: { side: "left" | "right"; filled: boolean }) {
  const x = side === "left" ? 2.5 : 10.5;
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="1" y="2" width="14" height="12" rx="2" stroke="currentColor" />
      <rect x={x} y="3.5" width="3" height="9" rx="0.5" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth={filled ? 0 : 0.8} />
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


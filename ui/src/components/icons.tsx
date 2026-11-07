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

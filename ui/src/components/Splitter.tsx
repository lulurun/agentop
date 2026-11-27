import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

const STEP = 16;

/**
 * A boundary between two parts that can be dragged, or moved with the arrow keys (Shift for
 * bigger steps, Home and End to the ends). Double-click puts it back where it started.
 */
export function Splitter({
  label,
  className,
  value,
  min,
  max,
  onMove,
  onNudge,
  onReset,
}: {
  label: string;
  className: string;
  /** For assistive tech, in the units of `min` and `max`. */
  value: number;
  min: number;
  max: number;
  /** The boundary is dragged to `clientX`. */
  onMove(clientX: number): void;
  /** Move the boundary by `dx` pixels, ±Infinity for as far as it goes. */
  onNudge(dx: number): void;
  onReset(): void;
}) {
  const [dragging, setDragging] = useState(false);
  const frame = useRef(0);
  const last = useRef(0);
  /** How far from the boundary it was grabbed, so it doesn't jump to the pointer. */
  const grab = useRef(0);

  useEffect(() => {
    if (!dragging) return;
    document.body.dataset.resizing = "true";
    return () => {
      delete document.body.dataset.resizing;
      cancelAnimationFrame(frame.current);
    };
  }, [dragging]);

  const end = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    cancelAnimationFrame(frame.current);
    onMove(last.current);
    setDragging(false);
  };

  const keys: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={Math.round(min)}
      aria-valuemax={Math.round(max)}
      tabIndex={0}
      title={`${label}: drag, or use the arrow keys; double-click to reset`}
      className={`splitter ${className}`}
      data-dragging={dragging || undefined}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        event.currentTarget.focus();
        const box = event.currentTarget.getBoundingClientRect();
        grab.current = event.clientX - (box.left + box.width / 2);
        last.current = event.clientX - grab.current;
        setDragging(true);
      }}
      onPointerMove={(event) => {
        if (!dragging) return;
        last.current = event.clientX - grab.current;
        cancelAnimationFrame(frame.current);
        frame.current = requestAnimationFrame(() => onMove(last.current));
      }}
      onPointerUp={end}
      onPointerCancel={end}
      onLostPointerCapture={() => setDragging(false)}
      onDoubleClick={onReset}
      onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
        const direction = keys[event.key];
        if (direction === undefined || event.ctrlKey || event.metaKey || event.altKey) return;
        event.preventDefault();
        onNudge(direction * (event.shiftKey ? STEP * 4 : STEP));
      }}
    />
  );
}

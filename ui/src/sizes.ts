/** Side panel widths and the middle panel's split (docs/0_10_ui.md, Panel widths): their bounds, and how they fit a window. */

export interface Sizes {
  /** Pixels; undefined for the default. */
  leftWidth?: number;
  rightWidth?: number;
  /** The left area's share of the middle panel when both areas show. */
  split?: number;
}

export const DEFAULT_LEFT = 256;
export const DEFAULT_RIGHT = 312;
export const DEFAULT_SPLIT = 0.5;
/** Inside ProjectLayout's bounds in src/agentop/service/models.py, which refuses a save outside them. */
export const MIN_SIDE = 180;
export const MAX_SIDE = 800;
export const MIN_SPLIT = 0.1;
export const MAX_SPLIT = 0.9;
/** What the middle panel keeps however wide the side panels were made. */
export const MIN_MIDDLE = 320;
/** What each area keeps when the middle panel is split. */
export const MIN_AREA = 200;

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

export const clampSide = (px: number) => Math.round(clamp(px, MIN_SIDE, MAX_SIDE));
export const clampSplit = (share: number) => Math.round(clamp(share, MIN_SPLIT, MAX_SPLIT) * 1000) / 1000;

/** A width read from a file or storage: clamped if a number, else undefined. */
export function readSide(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? clampSide(value) : undefined;
}

export function readSplit(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? clampSplit(value) : undefined;
}

/**
 * The widths the open side panels get in a window this wide: as set, but narrowed (each in
 * proportion, not below MIN_SIDE) so the middle panel keeps MIN_MIDDLE. What is set stays as it
 * is, so the panels widen again with the window.
 */
export function fitSides(window: number, left: number | null, right: number | null): { left: number; right: number } {
  const l = left ?? 0;
  const r = right ?? 0;
  const room = window - MIN_MIDDLE;
  if (l + r <= room) return { left: l, right: r };
  const floor = (left === null ? 0 : MIN_SIDE) + (right === null ? 0 : MIN_SIDE);
  const spare = Math.max(0, room - floor);
  const over = l + r - floor;
  const shrink = (w: number | null) => (w === null ? 0 : Math.round(MIN_SIDE + ((w - MIN_SIDE) * spare) / over));
  return { left: shrink(left), right: shrink(right) };
}

/** The left area's share for a pointer at `x` in a middle panel from `start`, `width` wide, each area keeping MIN_AREA where it can. */
export function splitAt(x: number, start: number, width: number): number {
  if (width <= 0) return DEFAULT_SPLIT;
  const min = Math.min(0.5, MIN_AREA / width);
  return clampSplit(clamp((x - start) / width, min, 1 - min));
}

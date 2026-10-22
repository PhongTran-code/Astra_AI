export const DEFAULT_SIDEBAR_WIDTH = 260;
export const MIN_SIDEBAR_WIDTH = 200;
export const MAX_SIDEBAR_WIDTH = 480;

export const DEFAULT_RAIL_WIDTH = 332;
export const MIN_RAIL_WIDTH = 260;
export const MAX_RAIL_WIDTH = 560;

export const SIDEBAR_WIDTH_KEY = "astra:sidebar-width:v1";
export const RAIL_WIDTH_KEY = "astra:rail-width:v1";

export function clampWidth(width: number, min: number, max: number, fallback: number): number {
  if (!Number.isFinite(width)) return fallback;
  return Math.max(min, Math.min(max, Math.round(width)));
}

export function readStoredWidth(
  key: string,
  min: number,
  max: number,
  fallback: number,
): number {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    const num = Number(raw);
    return clampWidth(num, min, max, fallback);
  } catch {
    return fallback;
  }
}

export function writeStoredWidth(key: string, width: number): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, String(width));
  } catch {
    // best-effort persistence
  }
}

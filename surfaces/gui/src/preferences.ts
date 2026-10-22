const LOCAL_PREFERENCES_KEY = "astra.preferences";

export const DEFAULT_SHOW_THINKING = true;

export type DesktopNotificationPreference = "off" | "important" | "all";

export const DEFAULT_DESKTOP_NOTIFICATION_PREFERENCE: DesktopNotificationPreference = "off";

export const DESKTOP_NOTIFICATION_PREFERENCE_VALUES = [
  "off",
  "important",
  "all",
] as const;

export function isDesktopNotificationPreference(
  value: unknown,
): value is DesktopNotificationPreference {
  return value === "off" || value === "important" || value === "all";
}

function readPreferences(): Record<string, unknown> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(LOCAL_PREFERENCES_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(parsed)) result[key] = value;
    return result;
  } catch {
    return {};
  }
}

function writePreference(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    const preferences = readPreferences();
    preferences[key] = value;
    window.localStorage.setItem(LOCAL_PREFERENCES_KEY, JSON.stringify(preferences));
  } catch {
    // Preferences are best effort; the app remains usable when storage is unavailable.
  }
}

export function readShowThinking(): boolean {
  return readPreferences().showThinking !== false;
}

export function writeShowThinking(enabled: boolean): void {
  writePreference("showThinking", enabled);
}

export function readDesktopNotificationPreference(): DesktopNotificationPreference {
  const value = readPreferences().desktopNotifications;
  return isDesktopNotificationPreference(value)
    ? value
    : DEFAULT_DESKTOP_NOTIFICATION_PREFERENCE;
}

export function writeDesktopNotificationPreference(
  preference: DesktopNotificationPreference,
): void {
  writePreference("desktopNotifications", preference);
}

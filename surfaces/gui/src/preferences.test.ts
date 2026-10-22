import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  readDesktopNotificationPreference,
  readShowThinking,
  writeDesktopNotificationPreference,
  writeShowThinking,
} from "./preferences";

beforeEach(() => {
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    },
  });
});

afterEach(() => {
  window.localStorage?.clear();
});

describe("local Preferences", () => {
  it("keeps reasoning visible by default and persists the display choice", () => {
    expect(readShowThinking()).toBe(true);

    writeShowThinking(false);

    expect(readShowThinking()).toBe(false);
  });

  it("persists only valid desktop notification modes", () => {
    expect(readDesktopNotificationPreference()).toBe("off");

    writeDesktopNotificationPreference("important");
    expect(readDesktopNotificationPreference()).toBe("important");

    window.localStorage.setItem("astra.preferences", JSON.stringify({ desktopNotifications: "unknown" }));
    expect(readDesktopNotificationPreference()).toBe("off");
  });
});

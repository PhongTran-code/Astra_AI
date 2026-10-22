import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  notifyDesktopEvent,
  shouldNotifyDesktop,
} from "./desktopNotifications";
import { writeDesktopNotificationPreference } from "./preferences";

const NotificationMock = vi.fn();

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
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("desktop notification preferences", () => {
  it("allows all events or important events according to the selected mode", () => {
    expect(shouldNotifyDesktop("off", "important")).toBe(false);
    expect(shouldNotifyDesktop("important", "routine")).toBe(false);
    expect(shouldNotifyDesktop("important", "important")).toBe(true);
    expect(shouldNotifyDesktop("all", "routine")).toBe(true);
  });

  it("suppresses routine events when the mode is important-only", () => {
    NotificationMock.mockImplementation(() => ({}));
    Object.defineProperty(NotificationMock, "permission", { value: "granted", configurable: true });
    vi.stubGlobal("Notification", NotificationMock);
    writeDesktopNotificationPreference("important");

    notifyDesktopEvent({ type: "task.completed", sessionId: "session-1" });
    expect(NotificationMock).not.toHaveBeenCalled();

    notifyDesktopEvent({ type: "task.failed", sessionId: "session-1" });
    expect(NotificationMock).toHaveBeenCalledTimes(1);
  });
});

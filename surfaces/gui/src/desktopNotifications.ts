import { readDesktopNotificationPreference, type DesktopNotificationPreference } from "./preferences";

type DesktopNotificationImportance = "important" | "routine";

export type DesktopNotificationEvent =
  | { type: "task.completed"; sessionId: string }
  | { type: "task.failed"; sessionId: string; errorText?: string }
  | { type: "permission.asked"; sessionId: string; detail?: string }
  | { type: "question.asked"; sessionId: string; question?: string };

type NotificationCopy = {
  title: string;
  body: string;
  importance: DesktopNotificationImportance;
};

export function shouldNotifyDesktop(
  preference: DesktopNotificationPreference,
  importance: DesktopNotificationImportance,
): boolean {
  if (preference === "off") return false;
  if (preference === "important") return importance === "important";
  return true;
}

function isAppInView(): boolean {
  if (typeof document === "undefined") return false;
  return document.visibilityState === "visible" && document.hasFocus();
}

function copyForEvent(event: DesktopNotificationEvent): NotificationCopy {
  switch (event.type) {
    case "task.completed":
      return {
        title: "Task completed",
        body: "The session finished running.",
        importance: "routine",
      };
    case "task.failed":
      return {
        title: "Task failed",
        body: event.errorText?.trim() || "The session stopped with an error.",
        importance: "important",
      };
    case "permission.asked":
      return {
        title: "Permission needed",
        body: event.detail?.trim() || "A session is waiting for permission before it can continue.",
        importance: "important",
      };
    case "question.asked":
      return {
        title: "Question needs your answer",
        body: event.question?.trim() || "A session is waiting for your answer.",
        importance: "important",
      };
  }
}

export function requestDesktopNotificationPermission(): Promise<NotificationPermission | "unsupported"> {
  if (typeof window === "undefined" || !("Notification" in window)) {
    return Promise.resolve("unsupported");
  }
  if (window.Notification.permission === "granted") return Promise.resolve("granted");
  return window.Notification.requestPermission();
}

export function showDesktopNotification(
  title: string,
  body: string,
  onClick?: () => void,
): Notification | null {
  if (typeof window === "undefined" || !("Notification" in window)) return null;
  if (window.Notification.permission !== "granted") return null;
  try {
    const notification = new window.Notification(title, { body });
    notification.onclick = () => {
      window.focus();
      onClick?.();
    };
    return notification;
  } catch {
    return null;
  }
}

export function notifyDesktopEvent(event: DesktopNotificationEvent): void {
  const copy = copyForEvent(event);
  if (!shouldNotifyDesktop(readDesktopNotificationPreference(), copy.importance)) return;
  if (isAppInView()) return;
  showDesktopNotification(copy.title, copy.body);
}

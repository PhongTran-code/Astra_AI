import { afterEach, describe, expect, it, vi } from "vitest";
import { windowClose, windowIsMaximized, windowMinimize, windowToggleMaximize } from "./tauri";

afterEach(() => {
  delete (globalThis as any).__TAURI__;
});

describe("native window controls", () => {
  it("uses Tauri's native window API for frameless controls", async () => {
    let maximized = false;
    const nativeWindow = {
      minimize: vi.fn(async () => undefined),
      toggleMaximize: vi.fn(async () => {
        maximized = !maximized;
      }),
      isMaximized: vi.fn(async () => maximized),
      close: vi.fn(async () => undefined),
    };
    (globalThis as any).__TAURI__ = {
      window: { getCurrentWindow: () => nativeWindow },
    };

    expect(await windowIsMaximized()).toBe(false);
    expect(await windowToggleMaximize()).toBe(true);
    expect(await windowIsMaximized()).toBe(true);
    await windowMinimize();
    await windowClose();

    expect(nativeWindow.toggleMaximize).toHaveBeenCalledOnce();
    expect(nativeWindow.minimize).toHaveBeenCalledOnce();
    expect(nativeWindow.close).toHaveBeenCalledOnce();
  });
});

import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { UpdateInline } from "./SettingsView";

let invoke: ReturnType<typeof vi.fn>;

beforeEach(() => {
  invoke = vi.fn(async (cmd: string) => cmd === "updates_enabled" ? false : null);
  (globalThis as any).__TAURI__ = { core: { invoke } };
});

afterEach(() => {
  cleanup();
  delete (globalThis as any).__TAURI__;
});

it("shows development status without offering or checking updates", async () => {
  render(<UpdateInline />);
  expect(await screen.findByText("Automatic updates are under development.")).toBeTruthy();
  expect(screen.queryByTestId("settings-update-check")).toBeNull();
  expect(screen.queryByTestId("settings-update-install")).toBeNull();
  expect(invoke).toHaveBeenCalledTimes(1);
  expect(invoke).toHaveBeenCalledWith("updates_enabled", undefined);
});

it("keeps the check action when the shell enables updates", async () => {
  invoke.mockImplementation(async (cmd: string) => cmd === "updates_enabled" ? true : null);
  render(<UpdateInline />);
  fireEvent.click(await screen.findByTestId("settings-update-check"));
  expect(await screen.findByText("You're on the latest version.")).toBeTruthy();
});

it("does not label a rejected update check as the latest version", async () => {
  invoke.mockImplementation(async (cmd: string) => {
    if (cmd === "updates_enabled") return true;
    throw new Error("UPDATES_DISABLED");
  });
  render(<UpdateInline />);
  fireEvent.click(await screen.findByTestId("settings-update-check"));
  expect(await screen.findByText("Couldn't check right now.")).toBeTruthy();
  expect(screen.queryByText("You're on the latest version.")).toBeNull();
});

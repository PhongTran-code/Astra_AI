import { readFileSync, readdirSync, statSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import { expect, type Page } from "@playwright/test";

// Shared helpers for the LIVE smoke specs (real backend + real model). Kept out of the hermetic
// suite (separate dir/config); see e2e/README.md.

export const BACKEND = "http://127.0.0.1:8765";

function sidecarToken(): string {
  const state =
    process.env.COWORKER_STATE_DIR ||
    (process.platform === "win32"
      ? join(process.env.APPDATA || homedir(), "coworker")
      : join(homedir(), ".config", "coworker"));
  try {
    return readFileSync(join(state, "sidecar-8765.token"), "utf8").trim();
  } catch {
    return "";
  }
}

/** Fetch from the live sidecar with its per-launch authentication token. */
export function backendFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = sidecarToken();
  if (token) headers.set("X-Astra-Token", token);
  return fetch(`${BACKEND}${path}`, { ...init, headers });
}

/** The expanded scratch base if the backend is up and a model is ready — else null (→ skip). */
export async function scratchBaseIfReady(): Promise<string | null> {
  try {
    const res = await backendFetch("/v1/settings");
    const s = await res.json();
    if (res.ok && s.model_ready) {
      return String(s.scratch_base || "~/Astra").replace(/^~(?=\/|$)/, homedir());
    }
  } catch {
    /* backend unreachable */
  }
  return null;
}

/** Newest `name` file across the per-session scratch dirs (each live session gets its own). */
export function newestFile(scratchBase: string, name: string): string | null {
  let best: { path: string; mtime: number } | null = null;
  let dirs: string[];
  try {
    dirs = readdirSync(scratchBase);
  } catch {
    return null;
  }
  for (const d of dirs) {
    const f = join(scratchBase, d, name);
    try {
      const st = statSync(f);
      if (!best || st.mtimeMs > best.mtime) best = { path: f, mtime: st.mtimeMs };
    } catch {
      /* not in this session dir */
    }
  }
  return best?.path ?? null;
}

/** Open a fresh Cowork session via the composer's session-setup persona menu. */
export async function startCoworkSession(page: Page) {
  await page.goto("/");
  // The current shell starts a draft from the sidebar's New session row; the
  // persona picker then lives in the setup row above the composer. Keep this
  // helper aligned with the shipped Astra surface rather than the retired
  // split-button contract.
  await page.getByRole("button", { name: "New session" }).click();
  await page.getByTestId("coworker-chip").click();
  await page.locator(".setup-menu").getByText(/Produce a deliverable/).click();
}

/** Switch the composer's permission mode from the default "Ask for approval". */
export async function selectMode(page: Page, label: "Bypass approvals" | "Discuss") {
  await page.getByRole("button", { name: "Mode", exact: true }).click();
  await page.getByRole("menu").getByText(label, { exact: true }).click();
}

/** Normalize the per-session unattended flag before a live smoke assertion. */
export async function setUnattended(page: Page, enabled: boolean) {
  await page.getByRole("button", { name: "Mode", exact: true }).click();
  const toggle = page.getByRole("switch", { name: "Send approvals to the Inbox" });
  const checked = (await toggle.getAttribute("aria-checked")) === "true";
  if (checked !== enabled) await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", String(enabled));
  await page.locator(".fixed.inset-0.z-30").click();
}

/** Type a task and send it. */
export async function sendTask(page: Page, text: string) {
  await page.getByPlaceholder(/Ask the coworker/).fill(text);
  // exact — "Send" is a substring of the Inbox control's "Sending approvals…" title when unattended.
  await page.getByRole("button", { name: "Send", exact: true }).click();
}

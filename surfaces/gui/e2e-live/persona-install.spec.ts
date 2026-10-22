import { test, expect } from "@playwright/test";
import { readFileSync } from "fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { backendFetch, newestFile, scratchBaseIfReady, selectMode, sendTask } from "./helpers";

// LIVE capstone — install a persona from a local-directory bundle, enable + surface it, start a
// session as it, and have it do real work. Exercises the whole persona pipeline: manifest parse +
// snapshot on install, lifecycle (enable/surface), session creation, and execution. Excluded from
// CI — run with `npm run e2e:live`. Idempotent: re-installing overwrites the snapshot.

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.join(here, "fixtures", "persona"); // holds e2e-tester.md

test("live: install a persona from a directory, enable it, and run a task as it", async ({ page }) => {
  const scratchBase = await scratchBaseIfReady();
  test.skip(!scratchBase, "live backend not ready — start astra-server and configure a model");

  const token = `PERSONA-${Date.now()}`;
  const name = `persona-${Date.now()}.txt`;

  // Directory selection is a native OS dialog in the real browser. Install the
  // exact local fixture through the authenticated local API, then exercise the
  // enable/surface/session path through the UI below.
  const installResponse = await backendFetch("/v1/personas/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dir: FIXTURE_DIR }),
  });
  expect(installResponse.ok).toBeTruthy();
  expect((await installResponse.json()).ok).toBe(true);

  await page.goto("/");

  // Open persona management (Settings ▸ Coworkers) from the current session
  // setup picker.
  await page.getByText("New session").first().click();
  await page.getByTestId("coworker-chip").click();
  await page.locator(".setup-menu").getByText(/Manage coworkers/).click();
  await expect(page.getByText("E2E Tester").first()).toBeVisible({ timeout: 30_000 });

  // Enable it in the picker. Enabling a persona also surfaces it in the picker; there is
  // intentionally one lifecycle switch now. Idempotent across re-runs (skip if already on), and
  // click + await rather than check() — this is a controlled React switch (async re-render).
  const row = page.locator(".divide-y > div").filter({ hasText: "E2E Tester" });
  const ensureChecked = async () => {
    const box = row.getByRole("switch");
    if (!(await box.isChecked())) {
      await box.click();
      await expect(box).toBeChecked();
    }
  };
  await ensureChecked();

  // Leave Settings (so the settings rows unmount), then use the setup row's Coworker chip to
  // start a fresh session AS the new persona. Select by the unique tagline — it appears only on
  // the dropdown item, whereas the name "E2E Tester" also shows in the top bar/sidebar once a
  // session is on it.
  await page.getByRole("button", { name: "New session" }).click();
  await page.getByRole("button", { name: "Coworker", exact: true }).click();
  await page.getByText(/Throwaway persona/).click();
  await expect(page.getByText("E2E Tester").first()).toBeVisible(); // the session is this persona

  // New sessions start in "Ask for approval" regardless of the persona's declared mode (a safety
  // default for freshly-installed personas), so bypass approvals to let the write run to completion.
  await selectMode(page, "Bypass approvals");
  await sendTask(page, `Write a file named ${name} containing exactly: ${token}`);

  // The installed persona should do the work. Non-Cowork personas don't render the Artifacts rail,
  // so wait on the file itself (ground truth) rather than a UI signal.
  await expect
    .poll(
      () => {
        const f = newestFile(scratchBase!, name);
        return f ? readFileSync(f, "utf8") : "";
      },
      { timeout: 150_000, message: `${name} with the token never appeared under ${scratchBase}` },
    )
    .toContain(token);
});

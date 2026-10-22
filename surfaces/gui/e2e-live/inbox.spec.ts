import { test, expect } from "@playwright/test";
import { scratchBaseIfReady, sendTask, setUnattended, startCoworkSession } from "./helpers";

// LIVE — Inbox / Unattended. With "Send to Inbox" on, a tool call that would normally block on an
// inline approval card must instead route to the Inbox (so the agent runs unattended). We assert the
// approval shows up in the Inbox for this session. Excluded from CI — run with `npm run e2e:live`.

test("live: unattended routes an approval to the Inbox", async ({ page }) => {
  const scratchBase = await scratchBaseIfReady();
  test.skip(!scratchBase, "live backend not ready — start astra-server and configure a model");

  const token = `INBOX-${Date.now()}`;
  const name = `inbox-${Date.now()}.txt`;

  await startCoworkSession(page);

  // The unattended flag lives in the composer's Mode menu.
  await setUnattended(page, true);

  // Keep the default Ask-for-approval mode: the write would normally block inline, but unattended
  // routes it to the Inbox.
  await sendTask(page, `Write a file named ${name} containing exactly: ${token}`);

  // Open the Inbox; the approval appears there (its session chip carries this session's title, which
  // is the prompt — so it contains the unique filename).
  await page.getByTestId("account-row").click();
  await page.getByRole("button", { name: "Inbox", exact: true }).click();
  await expect(page.getByText(name).first()).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole("button", { name: "Allow once", exact: true }).first()).toBeVisible();
});

import { test, expect } from "@playwright/test";
import { readFileSync } from "fs";
import { newestFile, scratchBaseIfReady, sendTask, setUnattended, startCoworkSession } from "./helpers";

// LIVE #1 — the approval gate. In the default "Ask for approval" mode a tool call must block on an
// in-transcript approval card; approving it lets execution proceed. (fib.md skips this via Full
// access.) Excluded from CI — run with `npm run e2e:live`.

test("live: a write blocks on an approval card, then completes once approved", async ({ page }) => {
  const scratchBase = await scratchBaseIfReady();
  test.skip(!scratchBase, "live backend not ready — start astra-server and configure a model");

  // Unique filename per run so the "doesn't exist before approval" check can't see a prior run's file.
  const name = `hello-${Date.now()}.txt`;

  await startCoworkSession(page);
  await setUnattended(page, false);
  // Leave the default "Ask for approval" mode — the write should gate.
  await sendTask(page, `Create a file named ${name} containing exactly the text: hello world`);

  // File writes use the compact approval row in the current Astra surface.
  // The row is the durable gate; the file must not exist until it is resolved.
  await expect(page.getByTestId("approval-row")).toBeVisible({ timeout: 120_000 });
  expect(newestFile(scratchBase!, name), "file must not exist before approval").toBeNull();

  // A real model may issue a follow-up read/verification after the write. Each
  // such tool has its own approval, so resolve only this session's bounded
  // chain rather than assuming one model turn equals one approval.
  for (let attempt = 0; attempt < 6; attempt++) {
    const allow = page.getByRole("button", { name: /^Allow(?: once)?$/ }).last();
    await expect(allow).toBeVisible({ timeout: 120_000 });
    await allow.click();
    if (newestFile(scratchBase!, name)) break;
  }

  // Now it runs to completion and the artifact lands on disk. The filesystem
  // is the strongest signal and does not depend on rail visibility/copy.
  await expect
    .poll(
      () => {
        const file = newestFile(scratchBase!, name);
        return file ? readFileSync(file, "utf8").toLowerCase() : "";
      },
      { timeout: 120_000, message: `${name} never appeared under ${scratchBase}` },
    )
    .toContain("hello world");
});

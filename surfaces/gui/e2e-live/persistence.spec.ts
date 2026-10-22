import { test, expect } from "@playwright/test";
import { readFileSync } from "fs";
import path from "node:path";
import {
  backendFetch,
  newestFile,
  scratchBaseIfReady,
  selectMode,
  sendTask,
  startCoworkSession,
} from "./helpers";

// LIVE #6 — persistence & resume. After a completed turn, reloading the page must not lose the work:
// the session persists in the sidebar and reopens with its full transcript and its artifact. We
// reopen it explicitly (rather than relying on which session auto-restores — several sessions can
// share the same updated_at second). Excluded from CI — run with `npm run e2e:live`.

test("live: a session's transcript and artifact survive a page reload", async ({ page }) => {
  const scratchBase = await scratchBaseIfReady();
  test.skip(!scratchBase, "live backend not ready — start astra-server and configure a model");

  const token = `PERSIST-${Date.now()}`;
  // Unique filename — appears early in the session title (so it survives title truncation and is a
  // reliable click target in the sidebar), and is the artifact name.
  const name = `note-${Date.now()}.txt`;

  await startCoworkSession(page);
  await selectMode(page, "Bypass approvals");
  await sendTask(page, `Write a file named ${name} containing exactly: ${token}`);

  // Turn finishes (the artifact lands) and the token is in the transcript.
  await expect
    .poll(
      () => {
        const file = scratchBase ? newestFile(scratchBase, name) : null;
        return file ? readFileSync(file, "utf8") : "";
      },
      { timeout: 150_000, message: `${name} never appeared under ${scratchBase}` },
    )
    .toContain(token);
  await expect(page.getByText(token).first()).toBeVisible();

  // The model-generated title is intentionally semantic and may omit the filename. Resolve the
  // session from the scratch artifact we just verified, then give this test a stable UI title
  // before reloading so the sidebar lookup is deterministic even when another live test was
  // updated more recently.
  const artifact = newestFile(scratchBase!, name);
  expect(artifact).toBeTruthy();
  const sessionId = path.basename(path.dirname(artifact!));
  const renamed = await backendFetch(`/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: name }),
  });
  expect(renamed.ok).toBeTruthy();

  // Reload, then reopen this session from the sidebar (it must have persisted there).
  await page.reload();
  await page.getByText(name).first().click({ timeout: 60_000 });

  // Reopened with its transcript restored (the token) and its artifact count back on the rail.
  await expect(page.getByText(token).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: /^Artifacts \d+$/ })).toBeVisible({ timeout: 30_000 });
});

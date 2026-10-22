const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const download = require("../api/download.js");
const update = require("../api/update.js");

function response() {
  return {
    headers: {},
    setHeader(name, value) { this.headers[name] = value; },
    end(body) { this.body = body; },
  };
}

test("public download and update routes pause without Blob access", async () => {
  const oldFetch = global.fetch;
  const oldBase = process.env.ASTRA_BLOB_BASE_URL;
  let fetches = 0;
  global.fetch = async () => { fetches++; throw new Error("unexpected upstream access"); };
  process.env.ASTRA_BLOB_BASE_URL = "https://example.invalid";
  try {
    for (const handler of [download, update]) {
      for (const method of ["GET", "HEAD"]) {
        const res = response();
        await handler({ method, query: { path: "stable/Astra-windows-setup.exe" } }, res);
        assert.equal(res.statusCode, 503);
        assert.equal(res.headers["Cache-Control"], "no-store");
        assert.equal(res.headers.Location, undefined);
        assert.equal(res.body, method === "HEAD" ? undefined : "Downloads and updates are temporarily paused during development.");
      }
      const res = response();
      await handler({ method: "POST" }, res);
      assert.equal(res.statusCode, 405);
    }
    assert.equal(fetches, 0);
  } finally {
    global.fetch = oldFetch;
    if (oldBase === undefined) delete process.env.ASTRA_BLOB_BASE_URL;
    else process.env.ASTRA_BLOB_BASE_URL = oldBase;
  }
});

test("release publishing is disabled and no old download domain remains", () => {
  const workflow = fs.readFileSync(path.join(__dirname, "../.github/workflows/release.yml"), "utf8");
  assert.match(workflow, /  release:\s*\n\s*if: \$\{\{ false \}\}/);
  assert.ok(!workflow.includes("astra-worker.vercel.app"));
});

test("landing installers are disabled instead of linking to Blob downloads", () => {
  const html = fs.readFileSync(path.join(__dirname, "../landing/landing.html"), "utf8");
  assert.ok(!/href=["'][^"']*\/downloads\//.test(html));
  assert.equal((html.match(/<button[^>]*class="btn [^"]*dl-btn-[^"]*"[^>]*disabled/g) || []).length, 6);
});

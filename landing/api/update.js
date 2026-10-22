const { blobUrl } = require("./_astra_blob.js");
const { pauseDistribution } = require("./_distribution.js");

module.exports = async function handler(req, res) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.setHeader("Allow", "GET, HEAD");
    res.statusCode = 405;
    res.end("Method not allowed");
    return;
  }

  if (pauseDistribution(req, res)) return;

  try {
    const upstream = await fetch(blobUrl("latest.json"), {
      headers: { accept: "application/json" },
    });
    const body = await upstream.text();

    if (!upstream.ok) {
      res.statusCode = upstream.status;
      res.setHeader("Cache-Control", "no-store");
      res.end("Update manifest is not available");
      return;
    }

    try {
      JSON.parse(body);
    } catch {
      res.statusCode = 502;
      res.setHeader("Cache-Control", "no-store");
      res.end("Update manifest is invalid");
      return;
    }

    res.statusCode = 200;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store, max-age=0");
    res.setHeader("Access-Control-Allow-Origin", "*");
    if (req.method === "HEAD") {
      res.end();
    } else {
      res.end(body);
    }
  } catch {
    res.statusCode = 503;
    res.setHeader("Cache-Control", "no-store");
    res.end("Update service is not configured");
  }
}

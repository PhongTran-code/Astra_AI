const { blobUrl } = require("./_astra_blob.js");
const { pauseDistribution } = require("./_distribution.js");

module.exports = function handler(req, res) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.setHeader("Allow", "GET, HEAD");
    res.statusCode = 405;
    res.end("Method not allowed");
    return;
  }

  if (pauseDistribution(req, res)) return;

  const rawPath = req.query?.path;
  const path = Array.isArray(rawPath) ? rawPath.join("/") : rawPath;

  try {
    const destination = blobUrl(path);
    res.statusCode = 302;
    res.setHeader("Location", destination);
    res.setHeader("Cache-Control", "public, max-age=300");
    res.end();
  } catch {
    res.statusCode = 400;
    res.end("Invalid download path");
  }
}

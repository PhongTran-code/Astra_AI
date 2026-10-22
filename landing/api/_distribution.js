// Keep public distribution paused until the new storage and release service are ready.
const DISTRIBUTION_ENABLED = false;

function pauseDistribution(req, res) {
  if (DISTRIBUTION_ENABLED) return false;
  res.statusCode = 503;
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("Content-Type", "text/plain; charset=utf-8");
  res.end(req.method === "HEAD" ? undefined : "Downloads and updates are temporarily paused during development.");
  return true;
}

module.exports = { pauseDistribution };

const BLOB_PREFIX = "astra";

function blobBaseUrl() {
  const raw = process.env.ASTRA_BLOB_BASE_URL?.trim();
  if (!raw) {
    throw new Error("ASTRA_BLOB_BASE_URL is not configured");
  }

  const url = new URL(raw);
  if (url.protocol !== "https:" || url.search || url.hash) {
    throw new Error("ASTRA_BLOB_BASE_URL must be an https URL without query parameters");
  }
  return raw.replace(/\/+$/, "");
}

function blobUrl(path) {
  const normalized = String(path || "").replace(/^\/+/, "");
  const segments = normalized.split("/");
  if (
    !normalized ||
    normalized.length > 512 ||
    segments.some((segment) => !segment || segment === "." || segment === "..") ||
    !/^[A-Za-z0-9._/-]+$/.test(normalized)
  ) {
    throw new Error("invalid download path");
  }

  const encodedPath = segments.map((segment) => encodeURIComponent(segment)).join("/");
  return `${blobBaseUrl()}/${BLOB_PREFIX}/${encodedPath}`;
}

module.exports = { blobUrl };

// Formatting helpers. Every string from the scan is untrusted: use esc() for
// any text that ends up inside HTML (ECharts tooltips), textContent elsewhere.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(text) {
  if (text === null || text === undefined) return "";
  return String(text).replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB"];

export function formatBytes(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "unknown";
  if (n < 1024) return `${Math.max(0, Math.round(n))} B`;
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${UNITS[unit]}`;
}

export function formatAge(days) {
  if (days === null || days === undefined) return "unknown";
  if (days < 1) return "today";
  if (days < 2) return "1 day";
  if (days < 45) return `${Math.floor(days)} days`;
  if (days < 365) {
    const months = Math.round(days / 30.44);
    return months === 1 ? "1 month" : `${months} months`;
  }
  return `${(days / 365.25).toFixed(1)} years`;
}

export function formatDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// Read a CSS custom property (theme-aware colors for ECharts).
export function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const AGE_LABELS = {
  "last-opened": "Last opened",
  "last-modified": "Last modified",
  "last-commit": "Last commit",
  installed: "Installed",
  created: "Created",
  "last-container-use": "Last used by a container",
};

// "Last opened 3 months ago", "Installed 2.1 years ago".
export function describeAge(signal, days) {
  const label = AGE_LABELS[signal] ?? "Last used";
  if (days === null || days === undefined) return `${label}: unknown`;
  return days < 1 ? `${label} today` : `${label} ${formatAge(days)} ago`;
}

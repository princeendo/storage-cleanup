import { describeAge, formatAge, formatBytes, formatDate } from "./format.js";
import { renderScatter } from "./scatter.js";
import { renderTreemap } from "./treemap.js";

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

async function loadView() {
  const response = await fetch("/api/view", { credentials: "same-origin" });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `The server answered ${response.status}.`);
  }
  return response.json();
}

function showBanner(message) {
  const banner = $("banner");
  banner.textContent = message;
  banner.hidden = false;
}

function kpi({ label, value, sub, status, onClick }) {
  const card = el(onClick ? "button" : "div", "kpi");
  if (onClick) {
    card.type = "button";
    card.addEventListener("click", onClick);
  }
  card.append(el("div", "label", label), el("div", "value", value));
  if (sub) card.append(el("div", "sub", sub));
  if (status) card.append(el("div", `status ${status.kind}`, status.text));
  return card;
}

function renderKpis(view) {
  const o = view.overview;
  const lowSpace = o.percent_free < 10;
  const root = $("kpis");
  root.replaceChildren(
    kpi({ label: "Used", value: formatBytes(o.used_bytes), sub: `of ${formatBytes(o.total_bytes)}` }),
    kpi({
      label: "Free",
      value: formatBytes(o.free_bytes),
      sub: `${o.percent_free}% of the disk`,
      status: lowSpace ? { kind: "critical", text: "Low on space" } : { kind: "good", text: "Healthy" },
    }),
    kpi({
      label: "Waiting in the Trash",
      value: o.trash_bytes === null ? "Can't read" : formatBytes(o.trash_bytes),
      sub: o.trash_bytes === null ? "macOS blocks reading the Trash" : "Freed once emptied",
    }),
    kpi({
      label: "Local snapshots",
      value: String(o.local_snapshots),
      sub: o.local_snapshots ? "May hold deleted files' space" : "None",
    }),
    kpi({
      label: "Skipped paths",
      value: String(o.skipped.length),
      sub: "Couldn't or shouldn't be read",
      onClick: () => openSkipped(o.skipped),
    }),
  );
  $("scan-meta").textContent = `Scanned ${formatDate(o.created_at)} · ${o.item_count.toLocaleString()} items`;
}

function openSkipped(skipped) {
  const list = $("skipped-list");
  list.replaceChildren(
    ...skipped.map((s) => {
      const item = el("li");
      item.append(el("div", "path", s.path || `(${s.collector})`), el("div", "reason", s.reason));
      return item;
    }),
  );
  if (!skipped.length) list.append(el("li", "reason", "Nothing was skipped."));
  $("skipped").showModal();
}

const FACTS = [
  ["Size", (e) => (e.value ?? e.size) !== undefined ? formatBytes(e.value ?? e.size) : null],
  ["Category", (e) => e.categoryName ?? null],
  ["Age", (e) => (e.age_days !== undefined ? describeAge(e.age_signal, e.age_days) : null)],
  ["Your files", (e) => (e.own_bytes !== undefined ? formatBytes(e.own_bytes) : null)],
  ["Rebuildable", (e) => (e.regenerable_bytes !== undefined ? formatBytes(e.regenerable_bytes) : null)],
  ["Last commit", (e) => (e.last_commit_days !== undefined ? (e.last_commit_days === null ? "unknown" : `${formatAge(e.last_commit_days)} ago`) : null)],
  ["Uncommitted changes", (e) => (e.dirty === undefined || e.dirty === null ? null : e.dirty ? "Yes" : "No")],
  ["Unpushed commits", (e) => e.unpushed_commits ?? null],
  ["Has a remote", (e) => (e.has_remote === undefined || e.has_remote === null ? null : e.has_remote ? "Yes" : "No")],
];

function showDetail(entry) {
  $("detail-name").textContent = entry.name ?? "";
  $("detail-path").textContent = entry.path ?? "";
  const facts = $("detail-facts");
  facts.replaceChildren();
  for (const [label, read] of FACTS) {
    const value = read(entry);
    if (value === null || value === undefined) continue;
    facts.append(el("dt", null, label), el("dd", null, value));
  }
  const old = document.querySelector("#detail .protected");
  if (old) old.remove();
  if (entry.protected) {
    $("detail").append(el("div", "status critical protected", "Protected: never removed by this tool"));
  }
  const panel = $("detail");
  panel.classList.add("open");
  panel.setAttribute("aria-hidden", "false");
}

function closeDetail() {
  const panel = $("detail");
  panel.classList.remove("open");
  panel.setAttribute("aria-hidden", "true");
}

// Each section draws itself from the view; onSelect opens the detail panel.
function treemapSection(view, onSelect) {
  const buttons = [...document.querySelectorAll("#where [data-lens]")];
  let lens = "categories";
  const draw = () => renderTreemap($("treemap"), view, lens, onSelect);
  for (const button of buttons) {
    button.addEventListener("click", () => {
      lens = button.dataset.lens;
      for (const b of buttons) b.setAttribute("aria-pressed", String(b === button));
      draw();
    });
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
  draw();
}

function scatterSection(view, onSelect) {
  const draw = () => renderScatter($("scatter"), view, onSelect);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
  draw();
}

const sections = [treemapSection, scatterSection];

async function main() {
  document.querySelector("#detail .close").addEventListener("click", closeDetail);
  document.querySelector("#skipped .icon-button").addEventListener("click", () => $("skipped").close());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDetail();
  });
  let view;
  try {
    view = await loadView();
  } catch (error) {
    showBanner(`Could not load the scan: ${error.message}`);
    return;
  }
  renderKpis(view);
  for (const render of sections) {
    try {
      render(view, showDetail);
    } catch (error) {
      console.error(error);
      showBanner(`A chart failed to draw: ${error.message}`);
    }
  }
}

// Module scripts run after the document is parsed.
main();

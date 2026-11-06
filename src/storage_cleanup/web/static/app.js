import { describeAge, formatAgo, formatBytes, formatDate } from "./format.js";
import * as queue from "./queue.js";
import { renderArchiveTab } from "./archive.js";
import { renderDeleteTab } from "./delete.js";
import { renderGrowth } from "./growth.js";
import { renderProjects } from "./projects.js";
import { renderRecommendations } from "./recommendations.js";
import { renderScatter } from "./scatter.js";
import { renderTarget } from "./target.js";
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
      label: "Recommended to reclaim",
      value: formatBytes(o.reclaimable_bytes),
      sub: `${o.recommendation_count} recommendation${o.recommendation_count === 1 ? "" : "s"}`,
      onClick: () => switchTab("recommendations"),
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
  ["Last commit", (e) => (e.last_commit_days !== undefined ? formatAgo(e.last_commit_days) : null)],
  ["Action", (e) => (e.action ? (e.action === "archive" ? "Archive" : "Delete (to the Trash)") : null)],
  ["Confidence", (e) => (typeof e.confidence === "number" ? `${Math.round(e.confidence * 100)}%` : null)],
  ["Score", (e) => (typeof e.score === "number" ? `${e.score} of 100` : null)],
  ["Suggested by", (e) => (e.source ? { agent: "Agent", rule: "Built-in rule", mixed: "Agent and rules" }[e.source] ?? e.source : null)],
  ["Why", (e) => e.reason ?? null],
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
  for (const old of document.querySelectorAll("#detail .protected, #detail .detail-body, #detail .detail-items")) {
    old.remove();
  }
  if (entry.body) facts.before(el("p", "detail-body", entry.body));
  if (entry.items?.length) {
    const list = el("ul", "detail-items");
    for (const item of entry.items) {
      const li = el("li");
      li.append(el("div", "item-name", `${item.name} · ${formatBytes(item.size)}`), el("div", "path", item.path ?? ""));
      list.append(li);
    }
    $("detail").append(list);
  }
  for (const old of document.querySelectorAll("#detail .queue-button")) old.remove();
  if (entry.item_id && !entry.protected) {
    const queueButton = (target, word) => {
      const button = el("button", "show-all queue-button");
      button.type = "button";
      button.textContent = target.has(entry.item_id) ? `Remove from ${word} queue` : `Add to ${word} queue`;
      button.addEventListener("click", () => {
        if (target.has(entry.item_id)) target.remove([entry.item_id]);
        else target.add([{ item_id: entry.item_id, name: entry.name, path: entry.path, size: entry.size ?? entry.value }]);
      });
      // Adding to one queue removes the item from the other.
      const refresh = () => {
        button.textContent = target.has(entry.item_id) ? `Remove from ${word} queue` : `Add to ${word} queue`;
      };
      target.onChange(refresh);
      queue.onChange(refresh);
      queue.archive.onChange(refresh);
      return button;
    };
    // Only path items can be archived; the server refuses the rest too.
    if (entry.path) $("detail").append(queueButton(queue.archive, "Archive"));
    $("detail").append(queueButton(queue, "Delete"));
  }
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

function findingsSection(view, onSelect) {
  const body = $("findings-body");
  const findings = view.findings;
  body.replaceChildren();
  if (!findings) {
    $("findings-byline").textContent = "";
    body.append(
      el(
        "p",
        "empty",
        "No agent findings yet. Ask your coding agent to follow AGENT_PLAYBOOK.md in this repository.",
      ),
    );
    return;
  }
  $("findings-byline").textContent = `By ${findings.agent} · ${formatDate(findings.created_at)}`;
  for (const headline of findings.headlines) {
    const card = el("button", "finding-card");
    card.type = "button";
    const total = headline.items.reduce((sum, i) => sum + i.size, 0);
    const count = headline.items.length;
    card.append(
      el("div", "finding-title", headline.title),
      el("div", "finding-body", headline.body),
      el("div", "finding-meta", count ? `${count} item${count === 1 ? "" : "s"} · ${formatBytes(total)}` : ""),
    );
    card.addEventListener("click", () => onSelect({ name: headline.title, body: headline.body, items: headline.items }));
    body.append(card);
  }
}

const TABS = ["overview", "recommendations", "archive", "delete"];

function updateQueueTabs() {
  for (const [tab, label, source] of [
    ["archive", "Archive", queue.archive],
    ["delete", "Delete", queue],
  ]) {
    const button = document.querySelector(`nav.tabs [data-tab="${tab}"]`);
    const count = source.queued().length;
    button.textContent = count ? `${label} (${count})` : label;
  }
}

function switchTab(name) {
  const tab = TABS.includes(name) ? name : "overview";
  for (const id of TABS) {
    $(id).hidden = id !== tab;
    const button = document.querySelector(`nav.tabs [data-tab="${id}"]`);
    button.setAttribute("aria-selected", String(id === tab));
  }
  if (location.hash !== `#${tab}`) history.replaceState(null, "", `#${tab}`);
  // Charts drawn while hidden have no size; let them measure again.
  window.dispatchEvent(new Event("resize"));
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

function growthSection(view, onSelect) {
  const draw = () => renderGrowth($("growth-body"), view, onSelect);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
  draw();
}

function projectsSection(view, onSelect) {
  renderProjects($("projects-body"), view, onSelect);
}

function recommendationsSection(view, onSelect) {
  renderRecommendations($("recs-body"), view, onSelect);
}

function targetSection(view) {
  renderTarget($("target-body"), view);
}

function archiveSection(view) {
  renderArchiveTab($("archive-body"), view);
}

function deleteSection(view) {
  renderDeleteTab($("delete-body"), view);
}

const sections = [
  findingsSection,
  growthSection,
  treemapSection,
  scatterSection,
  projectsSection,
  targetSection,
  recommendationsSection,
  archiveSection,
  deleteSection,
];

async function main() {
  for (const button of document.querySelectorAll("nav.tabs [data-tab]")) {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  }
  queue.onChange(updateQueueTabs);
  queue.archive.onChange(updateQueueTabs);
  updateQueueTabs();
  switchTab(location.hash.slice(1));
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
  if (view.findings_note) showBanner(view.findings_note);
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

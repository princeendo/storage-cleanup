// The Recommendations tab: ranked by score, filterable, roll-ups expand.
// Every string comes from the scan or an agent, so all text uses textContent.
import { describeAge, formatBytes } from "./format.js";

const SOURCE_LABELS = { agent: "Agent", rule: "Rule", mixed: "Agent + rules" };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function select(label, options, onChange) {
  const wrap = el("label", "filter");
  wrap.append(el("span", "filter-label", label));
  const input = el("select");
  for (const [value, text] of options) {
    const option = el("option", null, text);
    option.value = value;
    input.append(option);
  }
  input.addEventListener("change", () => onChange(input.value));
  wrap.append(input);
  return wrap;
}

function scoreCell(score) {
  const td = el("td", "score-cell");
  const bar = el("div", "score-bar");
  const fill = el("span");
  fill.style.width = `${Math.max(0, Math.min(100, score))}%`;
  bar.append(fill);
  td.append(el("span", "score-value", Math.round(score)), bar);
  return td;
}

function rowFor(row, view, onSelect, depth) {
  const tr = el("tr", depth ? "child-row" : "");
  tr.tabIndex = 0;
  tr.append(scoreCell(row.score));

  const item = el("td", "rec-item");
  const head = el("div", "rec-head");
  if (row.children?.length) {
    const toggle = el("button", "expand", "▸");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", `Show the ${row.children.length} items inside`);
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? "▾" : "▸";
      for (const child of tr.childRows) child.hidden = !open;
    });
    head.append(toggle);
  }
  const name = el("span", "rec-name", row.name);
  name.title = row.path ?? "";
  head.append(name);
  item.append(head, el("div", "rec-reason", row.reason));
  if (depth) item.style.paddingLeft = `${12 + depth * 20}px`;

  const action = el("span", `chip ${row.action}`, row.action === "archive" ? "Archive" : "Delete");
  const actionCell = el("td");
  actionCell.append(action);
  tr.append(
    item,
    el("td", "num", formatBytes(row.size)),
    el("td", "age", row.age_days === null || row.age_days === undefined ? "" : describeAge(row.age_signal, row.age_days)),
    actionCell,
    el("td", "num", `${Math.round(row.confidence * 100)}%`),
    (() => {
      const td = el("td");
      td.append(el("span", `badge source-${row.source}`, SOURCE_LABELS[row.source] ?? row.source));
      return td;
    })(),
  );

  const open = () =>
    onSelect({
      ...row,
      value: row.size,
      categoryName: row.category ? view.category_names[row.category] ?? row.category : null,
    });
  tr.addEventListener("click", open);
  tr.addEventListener("keydown", (event) => {
    // Keys on the expand button belong to the button, not the row.
    if (event.target !== tr) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });

  tr.childRows = [];
  const rows = [tr];
  for (const child of row.children ?? []) {
    const childRows = rowFor(child, view, onSelect, depth + 1);
    for (const childRow of childRows) {
      childRow.hidden = true;
      tr.childRows.push(childRow);
    }
    rows.push(...childRows);
  }
  return rows;
}

export function renderRecommendations(el_, view, onSelect) {
  const all = view.recommendations;
  const summary = document.getElementById("recs-summary");
  const o = view.overview;
  summary.textContent = all.length
    ? `${o.recommendation_count} recommendations · ${formatBytes(o.reclaimable_bytes)} could be reclaimed. Choosing what to archive or delete arrives in the next step.`
    : "";
  el_.replaceChildren();
  if (!all.length) {
    el_.append(el("p", "empty", "Nothing to recommend yet."));
    return;
  }

  const filters = { action: "all", source: "all", category: "all" };
  const categories = [...new Set(all.map((r) => r.category).filter(Boolean))];
  const bar = el("div", "filters");
  bar.append(
    select("Action", [["all", "All"], ["delete", "Delete"], ["archive", "Archive"]], (v) => {
      filters.action = v;
      draw();
    }),
    select("Suggested by", [["all", "All"], ["agent", "Agent"], ["rule", "Rules"]], (v) => {
      filters.source = v;
      draw();
    }),
    select(
      "Category",
      [["all", "All"], ...categories.map((c) => [c, view.category_names[c] ?? c])],
      (v) => {
        filters.category = v;
        draw();
      },
    ),
  );

  const scroller = el("div", "table-scroll");
  const table = el("table", "recs-table");
  const head = el("thead");
  const headRow = el("tr");
  for (const [label, cls] of [
    ["Score", ""],
    ["Item and reason", ""],
    ["Size", "num"],
    ["Age", ""],
    ["Action", ""],
    ["Confidence", "num"],
    ["Source", ""],
  ]) {
    headRow.append(el("th", cls, label));
  }
  head.append(headRow);
  const body = el("tbody");
  table.append(head, body);
  scroller.append(table);
  const empty = el("p", "empty", "No recommendations match these filters.");
  el_.append(bar, scroller, empty);

  function matches(row) {
    if (filters.action !== "all" && row.action !== filters.action) return false;
    if (filters.source !== "all" && row.source !== filters.source && row.source !== "mixed") return false;
    if (filters.category !== "all" && row.category !== filters.category) return false;
    return true;
  }

  function draw() {
    const shown = all.filter(matches);
    body.replaceChildren(...shown.flatMap((row) => rowFor(row, view, onSelect, 0)));
    empty.hidden = shown.length > 0;
    scroller.hidden = shown.length === 0;
  }
  draw();
}

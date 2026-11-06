// The Recommendations tab: ranked by score, filterable, roll-ups expand.
// Every string comes from the scan or an agent, so all text uses textContent.
import { describeAge, formatBytes } from "./format.js";
import * as queue from "./queue.js";

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

// What checking a row queues: the item itself, or a roll-up's members.
function selectable(row) {
  if (row.item_id) return row.removable && (row.action !== "archive" || row.path) ? [row] : [];
  return (row.children ?? []).flatMap(selectable);
}

// Archive recommendations go to the Archive queue, the rest to Delete.
function queueFor(row) {
  return row.action === "archive" ? queue.archive : queue;
}

function addRows(rows) {
  for (const target of [queue, queue.archive]) {
    const mine = rows.filter((r) => queueFor(r) === target);
    if (mine.length) target.add(mine.map(toEntry));
  }
}

function toEntry(row) {
  return { item_id: row.item_id, name: row.name, path: row.path, size: row.size };
}

function checkCell(row) {
  const td = el("td", "check-cell");
  const targets = selectable(row);
  if (!targets.length) return td;
  const box = el("input");
  box.type = "checkbox";
  const archiving = targets.every((t) => t.action === "archive");
  box.setAttribute("aria-label", `Queue ${row.name} for ${archiving ? "archiving" : "removal"}`);
  const sync = () => {
    const count = targets.filter((t) => queueFor(t).has(t.item_id)).length;
    box.checked = count === targets.length;
    box.indeterminate = count > 0 && count < targets.length;
  };
  sync();
  box.addEventListener("click", (event) => event.stopPropagation());
  box.addEventListener("change", () => {
    if (box.checked) addRows(targets);
    else {
      queue.remove(targets.map((t) => t.item_id));
      queue.archive.remove(targets.map((t) => t.item_id));
    }
  });
  queue.onChange(sync);
  queue.archive.onChange(sync);
  td.append(box);
  return td;
}

function rowFor(row, view, onSelect, depth) {
  const tr = el("tr", depth ? "child-row" : "");
  tr.tabIndex = 0;
  tr.append(checkCell(row), scoreCell(row.score));

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
    ? `${o.recommendation_count} recommendations · ${formatBytes(o.reclaimable_bytes)} could be reclaimed. Check items to add them to the Archive or Delete queue.`
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
    ["", "check-cell"],
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
  const selectShown = el("button", "show-all", "Queue all shown");
  selectShown.type = "button";
  selectShown.addEventListener("click", () => addRows(all.filter(matches).flatMap(selectable)));
  bar.append(selectShown);
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

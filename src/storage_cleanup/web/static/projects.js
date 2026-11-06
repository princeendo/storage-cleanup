// "Dead weight in your projects": your files against rebuildable folders.
// Plain HTML; every cell is set with textContent.
import { formatAge, formatBytes } from "./format.js";

const FIRST = 25;

function cell(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function badges(row) {
  const wrap = cell("div", "badges");
  if (row.dirty) wrap.append(cell("span", "badge", "uncommitted changes"));
  if (row.unpushed_commits) {
    const n = row.unpushed_commits;
    wrap.append(cell("span", "badge", `${n} unpushed commit${n === 1 ? "" : "s"}`));
  }
  if (row.has_remote === false) wrap.append(cell("span", "badge", "no remote"));
  return wrap;
}

function bar(row, largest) {
  const track = cell("div", "split-bar");
  const own = cell("span", "own");
  const regen = cell("span", "regen");
  own.style.width = `${(row.own_bytes / largest) * 100}%`;
  regen.style.width = `${(row.regenerable_bytes / largest) * 100}%`;
  track.append(own, regen);
  track.title = `Your files ${formatBytes(row.own_bytes)} · rebuildable ${formatBytes(row.regenerable_bytes)}`;
  return track;
}

function tableRow(row, largest, onSelect) {
  const tr = cell("tr");
  tr.tabIndex = 0;
  const name = cell("td", "name");
  const title = cell("div", "project-name", row.name);
  title.title = row.path ?? "";
  name.append(title, badges(row));
  tr.append(
    name,
    (() => {
      const td = cell("td", "bar-cell");
      td.append(bar(row, largest));
      return td;
    })(),
    cell("td", "num", formatBytes(row.regenerable_bytes)),
    cell("td", "num", row.last_commit_days === null ? "unknown" : `${formatAge(row.last_commit_days)} ago`),
  );
  const open = () => onSelect({ ...row, item_id: row.id });
  tr.addEventListener("click", open);
  tr.addEventListener("keydown", (event) => {
    if (event.key === "Enter") open();
  });
  return tr;
}

export function renderProjects(el, view, onSelect) {
  const rows = view.projects;
  el.replaceChildren();
  if (!rows.length) {
    el.append(cell("div", "empty", "No git projects found under the workspace folders."));
    return;
  }
  const largest = Math.max(...rows.map((r) => r.size), 1);
  const totalRegen = rows.reduce((sum, r) => sum + r.regenerable_bytes, 0);

  const summary = cell(
    "p",
    "projects-summary",
    `${rows.length} projects · ${formatBytes(totalRegen)} could be rebuilt by their tools.`,
  );
  const legend = cell("div", "split-legend");
  legend.append(cell("span", "key own", "Your files"), cell("span", "key regen", "Rebuildable"));

  const scroller = cell("div", "table-scroll");
  const table = cell("table", "projects-table");
  const head = cell("thead");
  const headRow = cell("tr");
  for (const [label, className] of [
    ["Project", ""],
    ["Size", ""],
    ["Rebuildable", "num"],
    ["Last commit", "num"],
  ]) {
    headRow.append(cell("th", className, label));
  }
  head.append(headRow);
  const body = cell("tbody");
  const draw = (count) => body.replaceChildren(...rows.slice(0, count).map((r) => tableRow(r, largest, onSelect)));
  draw(FIRST);
  table.append(head, body);
  scroller.append(table);
  el.append(summary, legend, scroller);

  if (rows.length > FIRST) {
    const more = cell("button", "show-all", `Show all ${rows.length} projects`);
    more.type = "button";
    more.addEventListener("click", () => {
      draw(rows.length);
      more.remove();
    });
    el.append(more);
  }
}

// "What grew since last time": the change in disk use, the items that grew or
// shrank, and a sparkline of used space across scans. A change in size is not
// a status, so it uses arrows and ink, never red or green. Every string from
// the scan uses textContent (or esc() inside the ECharts tooltip).
import { esc, formatBytes, formatDate, token } from "./format.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

const signed = (change) => `${change < 0 ? "▼" : "▲"} ${formatBytes(Math.abs(change))}`;

function row(item, onSelect) {
  const button = el("button", "growth-row");
  button.type = "button";
  const name = el("div", "growth-name");
  name.append(el("span", null, item.name));
  if (item.status === "new" || item.status === "gone") {
    name.append(el("span", "growth-tag", item.status));
  }
  const main = el("div", "growth-main");
  main.append(name, el("div", "path", item.path));
  const numbers = el("div", "growth-numbers");
  numbers.append(
    el("div", "growth-change", signed(item.change)),
    el("div", "growth-span", `${formatBytes(item.before)} → ${formatBytes(item.after)}`),
  );
  button.append(main, numbers);
  button.addEventListener("click", () => {
    const entry = { name: item.name, path: item.path, value: item.status === "gone" ? item.before : item.after };
    // A gone item no longer exists in this scan, so it cannot be queued.
    if (item.status !== "gone") entry.item_id = item.item_id;
    onSelect(entry);
  });
  return button;
}

function list(title, items, onSelect) {
  const box = el("div", "growth-list");
  box.append(el("h3", null, title));
  if (!items.length) box.append(el("p", "growth-none", "Nothing."));
  for (const item of items) box.append(row(item, onSelect));
  return box;
}

let chart = null;

function disposeChart() {
  if (chart) {
    chart.dispose();
    chart = null;
  }
}

function sparkline(host, history) {
  const data = history.map((h) => [new Date(h.created_at).getTime(), h.used_bytes]);
  chart = echarts.init(host, null, { renderer: "canvas" });
  window.addEventListener("resize", () => chart?.resize());
  const line = token("--series-1");
  chart.setOption({
    animation: false,
    grid: { left: 8, right: 8, top: 8, bottom: 8 },
    xAxis: { type: "time", show: false },
    yAxis: { type: "value", show: false, scale: true },
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: token("--surface-raised"),
      borderColor: token("--hairline"),
      textStyle: { color: token("--ink") },
      axisPointer: { type: "line", lineStyle: { color: token("--axis"), width: 1 } },
      formatter: (params) => {
        const p = params[0];
        return `<strong>${esc(formatBytes(p.value[1]))}</strong> used<br>${esc(formatDate(new Date(p.value[0]).toISOString()))}`;
      },
    },
    series: [
      {
        type: "line",
        data,
        symbol: "circle",
        symbolSize: 5,
        showSymbol: data.length <= 20,
        lineStyle: { width: 2, color: line },
        itemStyle: { color: line },
        emphasis: { scale: 1.6 },
      },
    ],
  });
}

export function renderGrowth(root, view, onSelect) {
  disposeChart();
  root.replaceChildren();
  const growth = view.growth;
  if (!growth) {
    root.append(el("p", "empty", "Run another scan later to see what grew."));
    return;
  }
  const head = el("p", "growth-head");
  head.append(
    document.createTextNode(`Since ${formatDate(growth.previous_created_at)}: disk use `),
    el("strong", null, signed(growth.used_change)),
  );
  root.append(head);
  const history = view.history ?? [];
  if (history.length >= 2) {
    const host = el("div", "growth-spark");
    host.setAttribute("aria-label", "Used space across scans");
    root.append(host);
    sparkline(host, history);
  }
  const lists = el("div", "growth-lists");
  lists.append(list("Grew", growth.grew, onSelect), list("Shrank", growth.shrank, onSelect));
  root.append(lists);
}

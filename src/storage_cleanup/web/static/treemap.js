// "Where your disk went": a drill-down treemap over the partition trees the
// server already computed. Never re-total here; the server's numbers add up.
import { fillFor, inkFor } from "./colors.js";
import { esc, formatBytes, token } from "./format.js";

let chart = null;

// "page" ink is for labels drawn on the card itself (the root header strip).
function rich() {
  return {
    lightName: { color: "#ffffff", fontWeight: 600, fontSize: 13, lineHeight: 18 },
    light: { color: "rgba(255,255,255,0.85)", fontSize: 12, lineHeight: 16 },
    darkName: { color: "#0b0b0b", fontWeight: 600, fontSize: 13, lineHeight: 18 },
    dark: { color: "rgba(11,11,11,0.72)", fontSize: 12, lineHeight: 16 },
    pageName: { color: token("--ink"), fontWeight: 600, fontSize: 13, lineHeight: 18 },
    page: { color: token("--ink-2"), fontSize: 12, lineHeight: 16 },
  };
}

function toData(node, lens, group, used) {
  const top = group ?? node.group;
  const data = {
    name: node.name,
    value: node.value,
    id: node.id,
    path: node.path,
    kind: node.kind,
    protected: node.protected,
    group: top,
    share: used ? node.value / used : 0,
    itemStyle: { color: fillFor(lens, top) },
    ink: inkFor(lens, top) === "#ffffff" ? "light" : "dark",
  };
  if (node.children?.length) {
    // A top-level "smaller items" box holds whole groups; let each keep its color.
    const inherited = node.kind === "smaller" && group === null ? null : top;
    data.children = node.children.map((child) => toData(child, lens, inherited, used));
  }
  return data;
}

function tooltip(info) {
  const d = info.data;
  if (!d || d.value === undefined) return "";
  const share = `${(d.share * 100).toFixed(1)}% of used space`;
  const path = d.path ? `<div style="opacity:.75;max-width:360px;white-space:normal;word-break:break-all">${esc(d.path)}</div>` : "";
  return `<strong>${esc(d.name)}</strong><br>${esc(formatBytes(d.value))} · ${esc(share)}${path}`;
}

export function renderTreemap(el, view, lens, onSelect) {
  const tree = view[lens];
  const used = tree.value;
  if (!chart) {
    chart = echarts.init(el, null, { renderer: "canvas" });
    window.addEventListener("resize", () => chart.resize());
  }
  chart.off("click");
  chart.on("click", (params) => {
    const d = params.data;
    if (!d || d.children) return;
    onSelect({
      item_id: d.kind === "item" ? d.id : undefined,
      protected: d.protected,
      name: d.name,
      path: d.path,
      value: d.value,
      categoryName: lens === "categories" ? view.category_names[d.group] ?? d.group : d.group,
    });
  });
  const surface = token("--surface");
  chart.setOption(
    {
      tooltip: {
        formatter: tooltip,
        backgroundColor: token("--surface-raised"),
        borderColor: token("--hairline"),
        textStyle: { color: token("--ink"), fontSize: 13 },
        extraCssText: "border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.12);",
      },
      series: [
        {
          type: "treemap",
          name: tree.name,
          data: tree.children.map((child) => toData(child, lens, null, used)),
          leafDepth: 1,
          roam: false,
          nodeClick: "zoomToNode",
          top: 0,
          left: 0,
          right: 0,
          bottom: 36,
          squareRatio: 0.6,
          visibleMin: 200,
          breadcrumb: {
            show: true,
            left: 0,
            bottom: 4,
            height: 24,
            emptyItemWidth: 40,
            itemStyle: {
              color: token("--wash") || "rgba(0,0,0,.05)",
              borderColor: "transparent",
              textStyle: { color: token("--ink-2"), fontSize: 12 },
            },
            emphasis: { itemStyle: { color: token("--hairline") } },
          },
          label: {
            show: true,
            fontSize: 12,
            lineHeight: 16,
            overflow: "truncate",
            // Ink comes from rich-text styles (item-level label overrides hide
            // the text). Rich text treats { } | as markup, so strip them.
            formatter: (p) => {
              const ink = p.data?.ink ?? "page";
              const name = String(p.name).replace(/[{}|]/g, "");
              return `{${ink}Name|${name}}\n{${ink}|${formatBytes(p.value)}}`;
            },
            rich: rich(),
          },
          upperLabel: {
            show: true,
            height: 26,
            fontWeight: 600,
            fontSize: 13,
            overflow: "truncate",
            formatter: (p) => {
              const ink = p.data?.ink ?? "page";
              const name = String(p.name).replace(/[{}|]/g, "");
              return `{${ink}Name|${name}}{${ink}|  ·  ${formatBytes(p.value)}}`;
            },
            rich: rich(),
          },
          itemStyle: { borderColor: surface, borderWidth: 2, gapWidth: 2, borderRadius: 4 },
          levels: [
            { itemStyle: { borderColor: surface, borderWidth: 0, gapWidth: 4 } },
            { itemStyle: { borderColor: surface, borderWidth: 3, gapWidth: 2 }, colorSaturation: [0.85, 1] },
            { itemStyle: { borderColor: surface, borderWidth: 2, gapWidth: 1 } },
          ],
        },
      ],
    },
    true,
  );
}

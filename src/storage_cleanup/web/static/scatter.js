// "Big and forgotten": age against size, one dot per app, tool, or folder.
// Scatter charts can only keep three colors apart (dataviz all-pairs cap), so
// categories fold into three families; the exact category is in the tooltip.
import { describeAge, esc, formatBytes, token } from "./format.js";

const FAMILIES = [
  {
    name: "Apps & tools",
    slot: "--series-1",
    categories: ["applications", "homebrew", "packages", "docker"],
  },
  { name: "Your files", slot: "--series-2", categories: ["downloads", "home", "projects"] },
  {
    name: "Caches & tool data",
    slot: "--series-3",
    categories: ["dev-caches", "app-data", "leftover-app-data", "ai-tooling"],
  },
];

const TICKS = [
  [0, "today"],
  [30, "1 month"],
  [182, "6 months"],
  [365, "1 year"],
  [1095, "3 years"],
  [3650, "10 years"],
];

// Round binary sizes, so the axis reads 10 MiB, 100 MiB, 1 GiB, ...
const SIZE_TICKS = [10, 100, 1024, 10 * 1024, 100 * 1024, 1024 * 1024].map((m) => m * 1024 ** 2);

// Age axis: log(1 + days), so last week and last decade both get room.
const toX = (days) => Math.log10(1 + Math.max(0, days));

let chart = null;

function familyOf(category) {
  return FAMILIES.findIndex((f) => f.categories.includes(category));
}

export function renderScatter(el, view, onSelect) {
  const points = view.scatter;
  if (!points.length) {
    if (chart) {
      chart.dispose();
      chart = null;
    }
    el.replaceChildren();
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No items over 10 MB with a known age.";
    el.append(empty);
    return;
  }
  if (!chart) {
    chart = echarts.init(el, null, { renderer: "canvas" });
    window.addEventListener("resize", () => chart.resize());
    chart.on("click", (params) => {
      const p = params.data?.point;
      if (p) onSelect({ ...p, categoryName: view.category_names[p.category] ?? p.category });
    });
  }

  const maxDays = Math.max(...points.map((p) => p.age_days), 400);
  const maxSize = Math.max(...points.map((p) => p.size));
  const zone = view.reclaim_zone;
  const surface = token("--surface");
  const ink2 = token("--ink-2");
  const muted = token("--ink-muted");
  const hairline = token("--hairline");

  const series = FAMILIES.map((family, index) => ({
    name: family.name,
    type: "scatter",
    symbolSize: 10,
    data: points
      .filter((p) => familyOf(p.category) === index)
      .map((p) => ({
        value: [toX(p.age_days), p.size],
        point: p,
        itemStyle: p.protected
          ? { color: "transparent", borderColor: token(family.slot), borderWidth: 2 }
          : undefined,
      })),
    itemStyle: { color: token(family.slot), borderColor: surface, borderWidth: 1.5, opacity: 0.9 },
    emphasis: { scale: 1.6, itemStyle: { borderWidth: 2 } },
    ...(index === 0
      ? {
          markArea: {
            silent: true,
            itemStyle: { color: token("--wash"), borderColor: token("--axis"), borderType: "dashed", borderWidth: 1 },
            label: { show: true, position: "insideTopRight", color: ink2, fontWeight: 600, fontSize: 12 },
            data: [
              [
                { name: "Big and forgotten", coord: [toX(zone.min_age_days), zone.min_bytes] },
                { coord: [toX(maxDays * 1.05), maxSize * 2] },
              ],
            ],
          },
        }
      : {}),
  }));

  chart.setOption(
    {
      grid: { left: 72, right: 24, top: 40, bottom: 48 },
      legend: {
        top: 0,
        left: 0,
        itemWidth: 10,
        itemHeight: 10,
        icon: "circle",
        textStyle: { color: ink2, fontSize: 12 },
      },
      tooltip: {
        trigger: "item",
        backgroundColor: token("--surface-raised"),
        borderColor: hairline,
        textStyle: { color: token("--ink"), fontSize: 13 },
        extraCssText: "border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.12);",
        formatter: (params) => {
          const p = params.data?.point;
          if (!p) return "";
          const category = view.category_names[p.category] ?? p.category;
          const protectedNote = p.protected ? "<br><em>Protected: never removed</em>" : "";
          const age = describeAge(p.age_signal, p.age_days);
          return `<strong>${esc(p.name)}</strong><br>${esc(formatBytes(p.size))} · ${esc(category)}<br>${esc(age)}${protectedNote}`;
        },
      },
      xAxis: {
        type: "value",
        min: 0,
        max: toX(maxDays * 1.05),
        name: "Time since last used →",
        nameLocation: "middle",
        nameGap: 30,
        nameTextStyle: { color: muted, fontSize: 12 },
        axisLine: { lineStyle: { color: token("--axis") } },
        splitLine: { lineStyle: { color: hairline } },
        axisTick: { show: false },
        axisLabel: {
          color: muted,
          customValues: TICKS.filter(([d]) => d <= maxDays * 1.05).map(([d]) => toX(d)),
          formatter: (value) => {
            const tick = TICKS.find(([d]) => Math.abs(toX(d) - value) < 1e-9);
            return tick ? tick[1] : "";
          },
        },
      },
      yAxis: {
        type: "log",
        logBase: 10,
        min: 10 * 1024 ** 2,
        max: maxSize * 2,
        axisLine: { show: false },
        axisTick: { show: false, customValues: SIZE_TICKS },
        splitLine: { lineStyle: { color: hairline } },
        axisLabel: {
          color: muted,
          customValues: SIZE_TICKS.filter((v) => v <= maxSize * 2),
          formatter: (value) => formatBytes(value).replace(".0 ", " "),
        },
      },
      series,
    },
    true,
  );
}

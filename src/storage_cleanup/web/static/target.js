// "Path to your target": set a free-space goal, queue the best items to reach
// it, and see on one bar how much is freed now and how much waits in the Trash.
// Every string from the scan uses textContent.
import { formatBytes } from "./format.js";
import * as queue from "./queue.js";

const STORE_KEY = "storage-cleanup:target";
const GIB = 1024 ** 3;
const DEFAULT_GOAL = { mode: "percent", value: "15" };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

// Bytes still to free for the goal; zero or less means already met.
// mode "percent": reach value% free. mode "amount": free up value GiB more.
export function bytesNeeded(overview, mode, value) {
  const v = Number(value);
  if (!Number.isFinite(v)) return NaN;
  if (mode === "amount") return v * GIB;
  return (v / 100) * overview.total_bytes - overview.free_bytes;
}

function covers(ancestor, path) {
  return path === ancestor || path.startsWith(ancestor.endsWith("/") ? ancestor : `${ancestor}/`);
}

// Greedy in score order. What is queued already counts toward the goal, and a
// candidate inside (or holding) a queued path is never picked. `queuedItems`
// is a list of {item_id, path, size}; returns the candidates to add.
export function fill(candidates, needed, queuedItems = []) {
  let have = queuedItems.reduce((sum, q) => sum + (q.size ?? 0), 0);
  const taken = queuedItems.map((q) => q.path).filter(Boolean);
  const queuedIds = new Set(queuedItems.map((q) => q.item_id));
  const picked = [];
  if (!(needed > 0)) return picked;
  for (const c of candidates) {
    if (have >= needed) break;
    if (queuedIds.has(c.item_id)) continue;
    if (taken.some((p) => covers(p, c.path) || covers(c.path, p))) continue;
    picked.push(c);
    taken.push(c.path);
    have += c.size;
  }
  return picked;
}

function loadGoal() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) ?? "null");
    if (raw && (raw.mode === "percent" || raw.mode === "amount") && typeof raw.value === "string") {
      return raw;
    }
  } catch {
    // Storage can be blocked; fall back to the default.
  }
  return { ...DEFAULT_GOAL };
}

function saveGoal(goal) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(goal));
  } catch {
    // Not fatal.
  }
}

function tooltip() {
  const tip = el("div", "target-tip");
  tip.hidden = true;
  tip.setAttribute("role", "tooltip");
  return tip;
}

export function renderTarget(root, view) {
  const o = view.overview;
  const candidates = view.target_candidates ?? [];
  const byId = new Map(candidates.map((c) => [c.item_id, c]));
  const goal = loadGoal();

  root.replaceChildren();
  root.classList.add("target");

  const controls = el("div", "target-controls");
  const mode = el("div", "segmented");
  mode.setAttribute("role", "group");
  mode.setAttribute("aria-label", "Goal type");
  const modes = [
    ["percent", "Reach % free"],
    ["amount", "Free up GiB"],
  ];
  const modeButtons = modes.map(([key, label]) => {
    const b = el("button", null, label);
    b.type = "button";
    b.addEventListener("click", () => {
      goal.mode = key;
      goal.value = key === "percent" ? "15" : "10";
      input.value = goal.value;
      saveGoal(goal);
      draw();
    });
    mode.append(b);
    return [key, b];
  });
  const field = el("label", "target-field");
  const fieldLabel = el("span", "filter-label");
  const input = el("input");
  input.type = "text";
  input.inputMode = "decimal";
  input.value = goal.value;
  input.setAttribute("aria-label", "Goal value");
  input.addEventListener("input", () => {
    goal.value = input.value;
    saveGoal(goal);
    draw();
  });
  field.append(fieldLabel, input);
  const go = el("button", "primary", "Check the best items");
  go.type = "button";
  go.addEventListener("click", () => {
    const needed = bytesNeeded(o, goal.mode, goal.value);
    const picks = fill(candidates, needed, queue.queued());
    if (picks.length) {
      queue.add(picks.map((c) => ({ item_id: c.item_id, name: c.name, path: c.path, size: c.size })));
    }
  });
  controls.append(mode, field, go);

  const sentence = el("p", "target-sentence");
  sentence.setAttribute("aria-live", "polite");

  const bar = el("div", "target-bar");
  bar.setAttribute("role", "img");
  const legend = el("ul", "target-legend");
  const status = el("p", "target-status");
  const tip = tooltip();
  const wrap = el("div", "target-bar-wrap");
  wrap.append(bar, tip);
  root.append(controls, sentence, wrap, legend, status);

  function showTip(node, lines) {
    tip.replaceChildren(el("strong", null, lines[0]), el("span", null, lines[1]));
    tip.hidden = false;
    const w = wrap.getBoundingClientRect();
    const r = node.getBoundingClientRect();
    const x = Math.min(Math.max(r.left - w.left + r.width / 2, 70), Math.max(70, w.width - 70));
    tip.style.left = `${x}px`;
  }
  const hideTip = () => {
    tip.hidden = true;
  };

  function segment(cls, bytes, name, detail) {
    const share = Math.min(100, (bytes / o.total_bytes) * 100);
    const seg = el("span", `target-seg ${cls}`);
    seg.style.flexBasis = `${share}%`;
    seg.tabIndex = 0;
    const text = [`${name}: ${formatBytes(bytes)}`, detail];
    seg.setAttribute("aria-label", `${text[0]}. ${text[1]}`);
    seg.addEventListener("pointermove", () => showTip(seg, text));
    seg.addEventListener("focus", () => showTip(seg, text));
    seg.addEventListener("pointerleave", hideTip);
    seg.addEventListener("blur", hideTip);
    return seg;
  }

  function draw() {
    for (const [key, b] of modeButtons) b.setAttribute("aria-pressed", String(key === goal.mode));
    fieldLabel.textContent = goal.mode === "percent" ? "% free" : "GiB to free";
    const v = Number(goal.value);
    const valid = goal.value.trim() !== "" && Number.isFinite(v) && v >= 0 && (goal.mode === "amount" || v <= 100);
    legend.replaceChildren();
    bar.replaceChildren();
    hideTip();
    const queuedList = queue.queued();
    go.disabled = !valid;
    if (!valid) {
      sentence.textContent =
        goal.mode === "percent" ? "Enter a percentage from 0 to 100." : "Enter a number of GiB, 0 or more.";
      status.textContent = "";
      bar.removeAttribute("aria-label");
      return;
    }
    const needed = bytesNeeded(o, goal.mode, goal.value);
    const targetFree = o.free_bytes + Math.max(0, needed);
    const everything = candidates.reduce((s, c) => s + c.size, 0);
    const youHave = `You have ${formatBytes(o.free_bytes)} free (${o.percent_free}%).`;
    const goalText = goal.mode === "percent" ? `Reaching ${v}%` : `Freeing ${formatBytes(v * GIB)}`;
    if (needed <= 0) sentence.textContent = `${youHave} Already met.`;
    else if (needed > everything) {
      sentence.textContent = `${youHave} ${goalText} needs ${formatBytes(needed)} more. Even everything recommended (${formatBytes(everything)}) falls short by ${formatBytes(needed - everything)}.`;
    } else sentence.textContent = `${youHave} ${goalText} needs ${formatBytes(needed)} more.`;

    let now = 0;
    let waits = 0;
    for (const q of queuedList) {
      if (byId.get(q.item_id)?.frees_now) now += q.size ?? 0;
      else waits += q.size ?? 0;
    }
    const scale = o.total_bytes;
    const room = Math.max(0, scale - o.free_bytes);
    now = Math.min(now, room);
    waits = Math.min(waits, room - now);
    const parts = [
      ["free", o.free_bytes, "Free now", "Space free on the disk today."],
      ["now", now, "Frees now", "Queued items removed for good as soon as they run."],
      ["wait", waits, "Waits in the Trash", "Queued items that free space once you empty the Trash."],
    ];
    for (const [cls, bytes, name, detail] of parts) {
      if (bytes > 0) bar.append(segment(cls, bytes, name, detail));
      const item = el("li");
      item.append(el("span", `target-key ${cls}`), document.createTextNode(`${name} ${formatBytes(bytes)}`));
      legend.append(item);
    }
    const mark = el("span", "target-marker");
    mark.style.left = `${Math.min(100, (targetFree / scale) * 100)}%`;
    const markText = [`Target: ${formatBytes(targetFree)} free`, `${((targetFree / scale) * 100).toFixed(1)}% of the disk.`];
    if (targetFree / scale > 0.7) mark.classList.add("flip");
    mark.tabIndex = 0;
    mark.setAttribute("aria-label", markText.join(". "));
    mark.addEventListener("pointermove", () => showTip(mark, markText));
    mark.addEventListener("focus", () => showTip(mark, markText));
    mark.addEventListener("pointerleave", hideTip);
    mark.addEventListener("blur", hideTip);
    const label = el("span", "target-marker-label", `Target ${formatBytes(targetFree)}`);
    mark.append(label);
    bar.append(mark);
    const rest = el("li");
    rest.append(el("span", "target-key goal"), document.createTextNode("Target"));
    legend.append(rest);
    bar.setAttribute(
      "aria-label",
      `Disk of ${formatBytes(scale)}: ${parts.map(([, b, n]) => `${n} ${formatBytes(b)}`).join(", ")}; target ${formatBytes(targetFree)} free.`,
    );

    const gap = targetFree - (o.free_bytes + now);
    const gapAll = targetFree - (o.free_bytes + now + waits);
    let text;
    if (gap <= 0) text = "Met now";
    else if (gapAll <= 0) text = "Met once you empty the Trash";
    else text = `Not met yet: ${formatBytes(gapAll)} to go`;
    if (o.local_snapshots > 0) text += ". Snapshots may keep some space in use until macOS removes them.";
    status.textContent = text;
  }

  queue.onChange(draw);
  draw();
}

// The Archive tab: the queue, an exact review, confirmation, and results.
// Each item is copied to the homelab, verified, and only then moved to the
// Trash. The server rebuilds and re-checks the plan; this page only asks.
import { formatBytes, formatDate } from "./format.js";
import { button, el, entryList, openDialog, post, refusalList, resultsView } from "./actions-ui.js";
import { archive as queue } from "./queue.js";

const NOT_SET_UP = "Archive is not set up: copy archive.example.toml to local/archive.toml.";
const HOW = "Each item is copied to the homelab, checked with SHA-256 on both ends, and only then moved to the Trash.";
const RUNNING = "Archiving… this can take a while. The log in reports/actions shows progress.";

export function renderArchiveTab(root, view) {
  let location = view.archive?.location ?? "";
  let lastResult = null;
  let running = false;
  // Created once so screen readers announce text set later.
  const status = el("p", "plan-how");
  status.setAttribute("aria-live", "polite");

  function setBanner(message) {
    const banner = document.getElementById("banner");
    banner.textContent = message;
    banner.hidden = false;
  }

  function body(items) {
    return { action: "archive", location, item_ids: items.map((i) => i.item_id), permanent: false };
  }

  async function review() {
    const { status: code, data } = await post("/api/plan", body(queue.queued()));
    if (code !== 200) {
      setBanner(typeof data === "string" ? data : `Could not build the plan (${code}).`);
      return;
    }
    openReview(data);
  }

  function openReview(plan) {
    openDialog((dialog, close) => {
      dialog.append(el("h3", null, "Review: archive to the homelab"));
      const total = plan.entries.reduce((sum, e) => sum + (e.size ?? 0), 0);
      dialog.append(
        el(
          "p",
          "plan-totals",
          plan.entries.length
            ? `Archives ${plan.entries.length} item${plan.entries.length === 1 ? "" : "s"} (${formatBytes(total)}) to ${plan.location ?? location}.`
            : "Nothing in this plan can be archived.",
        ),
        el("p", "plan-how", HOW),
      );
      if (plan.entries.length) dialog.append(entryList(plan.entries));
      if (plan.refused.length) {
        dialog.append(el("h4", null, `Not included (${plan.refused.length})`), refusalList(plan.refused));
      }
      const actions = el("div", "dialog-actions");
      actions.append(button("Cancel", "show-all", close));
      if (plan.entries.length) {
        actions.append(
          button("Archive", "primary", () => {
            close();
            run(plan);
          }),
        );
      }
      dialog.append(actions);
    });
  }

  async function run(plan) {
    running = true;
    status.textContent = RUNNING;
    draw();
    const { status: code, data } = await post("/api/run", { ...body(queue.queued()), plan_id: plan.plan_id });
    running = false;
    status.textContent = "";
    if (code === 409 && data === "The plan changed; review it again.") {
      setBanner(data);
      draw();
      review();
      return;
    }
    if (code !== 200) {
      setBanner(typeof data === "string" ? data : `The run failed (${code}).`);
      draw();
      return;
    }
    lastResult = data;
    queue.remove(data.results.filter((r) => r.status === "done").map((r) => r.item_id));
    setBanner(`Charts show the scan from ${formatDate(view.overview.created_at)}. Run a new scan to refresh them.`);
    draw();
  }

  function draw() {
    root.replaceChildren();
    if (!view.archive?.configured) {
      root.append(el("p", "empty", NOT_SET_UP));
      return;
    }
    if (lastResult) {
      root.append(
        resultsView(lastResult, {
          kind: "archive",
          onBack: () => {
            lastResult = null;
            draw();
          },
        }),
      );
      return;
    }
    const items = queue.queued();
    if (!items.length) {
      root.append(el("p", "empty", "Nothing queued. Check items on the Recommendations tab, or use “Add to Archive queue” in any item’s details."));
      root.append(status);
      return;
    }
    const total = items.reduce((sum, i) => sum + (i.size ?? 0), 0);
    root.append(el("p", "lede", `${items.length} item${items.length === 1 ? "" : "s"} queued · ${formatBytes(total)} (as scanned)`));
    const list = el("ul", "plan-list queue-list");
    for (const item of items) {
      const li = el("li");
      const head = el("div", "plan-head");
      const remove = button("×", "icon-button", () => queue.remove([item.item_id]));
      remove.setAttribute("aria-label", `Remove ${item.name} from the queue`);
      remove.disabled = running;
      head.append(el("span", "plan-name", item.name), el("span", "plan-size", formatBytes(item.size)), remove);
      li.append(head, el("div", "path", item.path ?? ""));
      list.append(li);
    }
    const field = el("label", "confirm-label", "Archive location");
    const input = el("input", "confirm-input");
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.value = location;
    input.disabled = running;
    input.addEventListener("input", () => {
      location = input.value;
    });
    field.append(input);
    const reviewButton = button("Review…", "primary", review);
    reviewButton.disabled = running;
    const clear = button("Clear queue", "show-all", () => queue.clear());
    clear.disabled = running;
    const controls = el("div", "delete-controls");
    controls.append(reviewButton, clear);
    root.append(field, controls, status, list);
  }

  queue.onChange(() => {
    if (!running) draw();
  });
  draw();
}

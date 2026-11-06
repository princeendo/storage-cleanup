// The Delete tab: the queue, an exact review, confirmation, and results.
// The server rebuilds and re-checks the plan; this page only asks and shows.
import { formatBytes, formatDate } from "./format.js";
import * as queue from "./queue.js";

const PERMANENT_PHRASE = "delete permanently";
const EMPTY_TRASH_PHRASE = "empty trash";
const METHOD_LABELS = {
  trash: "Move to Trash",
  delete: "Delete permanently",
  "docker-image-rm": "Docker: remove image",
  "docker-builder-prune": "Docker: clear build cache",
  "brew-uninstall": "Homebrew: uninstall",
  "brew-uninstall-cask": "Homebrew: uninstall app",
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function button(text, className, onClick) {
  const b = el("button", className, text);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  return { status: response.status, data };
}

// A modal dialog that returns focus to whatever opened it.
function openDialog(build) {
  const opener = document.activeElement;
  const dialog = el("dialog", "action-dialog");
  const close = () => {
    dialog.close();
    dialog.remove();
    if (opener instanceof HTMLElement) opener.focus();
  };
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  build(dialog, close);
  document.body.append(dialog);
  dialog.showModal();
  dialog.querySelector("button, input")?.focus();
  return close;
}

function entryList(entries) {
  const list = el("ul", "plan-list");
  for (const entry of entries) {
    const li = el("li");
    const head = el("div", "plan-head");
    head.append(
      el("span", "plan-name", entry.name),
      el("span", "plan-size", formatBytes(entry.size)),
    );
    li.append(head, el("div", "path", entry.path ?? ""));
    li.append(el("div", "plan-how", `${METHOD_LABELS[entry.method] ?? entry.method} · ${entry.undo}`));
    for (const warning of entry.warnings ?? []) li.append(el("div", "status critical", warning));
    list.append(li);
  }
  return list;
}

function refusalList(refused) {
  const list = el("ul", "plan-list refused");
  for (const refusal of refused) {
    const li = el("li");
    li.append(el("div", "plan-name", refusal.name), el("div", "plan-how", refusal.reason));
    list.append(li);
  }
  return list;
}

export function renderDeleteTab(root, view) {
  let permanent = false;
  let lastResult = null;

  function setBanner(message) {
    const banner = document.getElementById("banner");
    banner.textContent = message;
    banner.hidden = false;
  }

  async function review() {
    const items = queue.queued();
    const { status, data } = await post("/api/plan", {
      item_ids: items.map((i) => i.item_id),
      permanent,
    });
    if (status !== 200) {
      setBanner(typeof data === "string" ? data : `Could not build the plan (${status}).`);
      return;
    }
    openReview(data);
  }

  function openReview(plan) {
    openDialog((dialog, close) => {
      dialog.append(el("h3", null, permanent ? "Review: delete permanently" : "Review: move to the Trash"));
      const totals = el("p", "plan-totals");
      const parts = [];
      if (plan.trash_bytes) parts.push(`Moves ${formatBytes(plan.trash_bytes)} to the Trash`);
      if (plan.freed_bytes) parts.push(`frees ${formatBytes(plan.freed_bytes)} now`);
      const sentence = parts.join(" and ");
      totals.textContent = parts.length
        ? `${sentence[0].toUpperCase()}${sentence.slice(1)}.`
        : "Nothing in this plan can be removed.";
      dialog.append(totals);
      if (plan.entries.length) dialog.append(entryList(plan.entries));
      if (plan.refused.length) {
        dialog.append(el("h4", null, `Not included (${plan.refused.length})`), refusalList(plan.refused));
      }
      const actions = el("div", "dialog-actions");
      actions.append(button("Cancel", "show-all", close));
      if (plan.entries.length) {
        const label = permanent ? "Continue" : "Move to Trash";
        const confirm = button(label, `primary${permanent ? " danger" : ""}`, () => {
          close();
          if (permanent) openPermanentConfirm(plan);
          else run(plan, null);
        });
        actions.append(confirm);
      }
      dialog.append(actions);
    });
  }

  function openPermanentConfirm(plan) {
    openDialog((dialog, close) => {
      dialog.append(el("h3", null, "This cannot be undone"));
      dialog.append(
        el("p", "plan-totals", `These ${plan.entries.length} items will be deleted permanently, not moved to the Trash.`),
        entryList(plan.entries),
      );
      const label = el("label", "confirm-label", `Type "${PERMANENT_PHRASE}" to confirm`);
      const input = el("input", "confirm-input");
      input.autocomplete = "off";
      label.append(input);
      const go = button("Delete permanently", "primary danger", () => {
        close();
        run(plan, input.value);
      });
      go.disabled = true;
      input.addEventListener("input", () => {
        go.disabled = input.value !== PERMANENT_PHRASE;
      });
      const actions = el("div", "dialog-actions");
      actions.append(button("Cancel", "show-all", close), go);
      dialog.append(label, actions);
    });
  }

  async function run(plan, confirmText) {
    const items = queue.queued();
    const { status, data } = await post("/api/run", {
      item_ids: items.map((i) => i.item_id),
      permanent,
      plan_id: plan.plan_id,
      confirm: confirmText,
    });
    if (status === 409 && data === "The plan changed; review it again.") {
      setBanner(data);
      review();
      return;
    }
    if (status !== 200) {
      setBanner(typeof data === "string" ? data : `The run failed (${status}).`);
      return;
    }
    lastResult = data;
    queue.remove(data.results.filter((r) => r.status === "done").map((r) => r.item_id));
    setBanner(`Charts show the scan from ${formatDate(view.overview.created_at)}. Run a new scan to refresh them.`);
    draw();
  }

  function emptyTrash() {
    openDialog((dialog, close) => {
      const size = view.overview.trash_bytes;
      dialog.append(el("h3", null, "Empty the Trash?"));
      dialog.append(
        el(
          "p",
          "plan-totals",
          `The Trash holds ${size === null ? "an unknown amount (macOS blocks reading it)" : formatBytes(size)}. ` +
            "Emptying it is permanent and includes everything in the Trash, not only what this tool put there.",
        ),
      );
      const label = el("label", "confirm-label", `Type "${EMPTY_TRASH_PHRASE}" to confirm`);
      const input = el("input", "confirm-input");
      input.autocomplete = "off";
      label.append(input);
      const message = el("p", "plan-how");
      const go = button("Empty Trash", "primary danger", async () => {
        go.disabled = true;
        const { status, data } = await post("/api/empty-trash", { confirm: input.value });
        message.textContent =
          status === 200 ? data.message : typeof data === "string" ? data : `Failed (${status}).`;
      });
      go.disabled = true;
      input.addEventListener("input", () => {
        go.disabled = input.value !== EMPTY_TRASH_PHRASE;
      });
      const actions = el("div", "dialog-actions");
      actions.append(button("Close", "show-all", close), go);
      dialog.append(label, message, actions);
    });
  }

  function resultsView(result) {
    const wrap = el("div", "results");
    const done = result.results.filter((r) => r.status === "done").length;
    wrap.append(
      el("h3", null, `Done: ${done} of ${result.results.length}`),
      el(
        "p",
        "plan-totals",
        [
          result.moved_to_trash_bytes
            ? `Moved to the Trash: ${formatBytes(result.moved_to_trash_bytes)} (freed when the Trash is emptied).`
            : "",
          result.freed_bytes ? `Freed now: ${formatBytes(result.freed_bytes)}.` : "",
        ]
          .filter(Boolean)
          .join(" ") || "Nothing was removed.",
      ),
      el("p", "plan-how", `Free space: ${formatBytes(result.free_before)} before, ${formatBytes(result.free_after)} after. Log: ${result.log_path}`),
    );
    const list = el("ul", "plan-list");
    for (const r of result.results) {
      const li = el("li", `result-${r.status}`);
      const head = el("div", "plan-head");
      head.append(el("span", "plan-name", r.name), el("span", `status ${r.status === "done" ? "good" : "critical"}`, r.status));
      li.append(head, el("div", "path", r.path ?? ""), el("div", "plan-how", r.detail));
      list.append(li);
    }
    wrap.append(list, button("Back to the queue", "show-all", () => {
      lastResult = null;
      draw();
    }));
    return wrap;
  }

  function draw() {
    root.replaceChildren();
    if (lastResult) {
      root.append(resultsView(lastResult));
      return;
    }
    const items = queue.queued();
    if (!items.length) {
      root.append(el("p", "empty", "Nothing queued. Check items on the Recommendations tab, or use “Add to Delete queue” in any item’s details."));
    } else {
      const total = items.reduce((sum, i) => sum + (i.size ?? 0), 0);
      root.append(el("p", "lede", `${items.length} item${items.length === 1 ? "" : "s"} queued · ${formatBytes(total)} (as scanned)`));
      const list = el("ul", "plan-list queue-list");
      for (const item of items) {
        const li = el("li");
        const head = el("div", "plan-head");
        head.append(
          el("span", "plan-name", item.name),
          el("span", "plan-size", formatBytes(item.size)),
          button("×", "icon-button", () => queue.remove([item.item_id])),
        );
        head.lastChild.setAttribute("aria-label", `Remove ${item.name} from the queue`);
        li.append(head, el("div", "path", item.path ?? ""));
        list.append(li);
      }
      const mode = el("label", "mode-switch");
      const toggle = el("input");
      toggle.type = "checkbox";
      toggle.checked = !permanent;
      toggle.addEventListener("change", () => {
        permanent = !toggle.checked;
        draw();
      });
      mode.append(toggle, el("span", null, "Move files and folders to the Trash"));
      const controls = el("div", "delete-controls");
      controls.append(mode);
      if (permanent) controls.append(el("span", "status critical", "Permanent: cannot be undone"));
      controls.append(
        button("Review…", "primary", review),
        button("Clear queue", "show-all", () => queue.clear()),
      );
      root.append(controls, list);
    }
    const trash = el("div", "trash-box");
    const size = view.overview.trash_bytes;
    trash.append(
      el("span", null, `Trash: ${size === null ? "size unknown (macOS blocks reading it)" : formatBytes(size)}`),
      button("Empty Trash…", "show-all", emptyTrash),
    );
    root.append(trash);
  }

  queue.onChange(draw);
  draw();
}

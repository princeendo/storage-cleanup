// Helpers shared by the Delete and Archive tabs.
// Every string from the scan or the server goes through textContent.
import { formatBytes } from "./format.js";

const METHOD_LABELS = {
  trash: "Move to Trash",
  delete: "Delete permanently",
  "docker-image-rm": "Docker: remove image",
  "docker-builder-prune": "Docker: clear build cache",
  "brew-uninstall": "Homebrew: uninstall",
  "brew-uninstall-cask": "Homebrew: uninstall app",
  archive: "Archive to the homelab, then move to the Trash",
};

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

export function button(text, className, onClick) {
  const b = el("button", className, text);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

export async function post(url, body) {
  let response;
  try {
    response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    return {
      status: 0,
      data: `Could not reach the page's server (${error.message}). If a run was in progress, its log in reports/actions shows what was done.`,
    };
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  return { status: response.status, data };
}

// A modal dialog that returns focus to whatever opened it.
export function openDialog(build) {
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

export function entryList(entries) {
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

export function refusalList(refused) {
  const list = el("ul", "plan-list refused");
  for (const refusal of refused) {
    const li = el("li");
    li.append(el("div", "plan-name", refusal.name), el("div", "plan-how", refusal.reason));
    list.append(li);
  }
  return list;
}

// The results of a run. kind "archive" says "Archived" and shows the homelab folder.
export function resultsView(result, { kind = "delete", onBack } = {}) {
  const archive = kind === "archive";
  const wrap = el("div", "results");
  const done = result.results.filter((r) => r.status === "done").length;
  const trashed = archive ? (result.archived_bytes ?? result.moved_to_trash_bytes) : result.moved_to_trash_bytes;
  wrap.append(
    el("h3", null, `Done: ${done} of ${result.results.length}`),
    el(
      "p",
      "plan-totals",
      [
        trashed
          ? `${archive ? "Archived" : "Moved to the Trash"}: ${formatBytes(trashed)} (freed when the Trash is emptied).`
          : "",
        result.freed_bytes ? `Freed now: ${formatBytes(result.freed_bytes)}.` : "",
      ]
        .filter(Boolean)
        .join(" ") || (archive ? "Nothing was archived." : "Nothing was removed."),
    ),
  );
  if (archive && result.archive_batch) {
    wrap.append(el("p", "plan-how", `Homelab folder: ${result.archive_batch}`));
  }
  wrap.append(
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
  wrap.append(list, button("Back to the queue", "show-all", onBack ?? (() => {})));
  return wrap;
}

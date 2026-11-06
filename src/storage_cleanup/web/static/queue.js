// The Delete queue: what the user picked, kept per browser tab.
// Storage can be unavailable (private windows); the queue then lives in memory.
const KEY = "storage-cleanup:delete-queue";
const listeners = new Set();
let entries = load();

function load() {
  try {
    const raw = sessionStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return new Map(Array.isArray(parsed) ? parsed.map((e) => [e.item_id, e]) : []);
  } catch {
    return new Map();
  }
}

function save() {
  try {
    sessionStorage.setItem(KEY, JSON.stringify([...entries.values()]));
  } catch {
    // Not fatal: the queue still works for this page view.
  }
  for (const listener of listeners) listener();
}

// entry: { item_id, name, path, size }
export function queued() {
  return [...entries.values()];
}

export function has(itemId) {
  return entries.has(itemId);
}

export function add(list) {
  for (const entry of list) {
    if (entry?.item_id) entries.set(entry.item_id, entry);
  }
  save();
}

export function remove(itemIds) {
  for (const id of itemIds) entries.delete(id);
  save();
}

export function clear() {
  entries.clear();
  save();
}

export function onChange(callback) {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

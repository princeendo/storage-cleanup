// The Delete and Archive queues: what the user picked, kept per browser tab.
// Storage can be unavailable (private windows); a queue then lives in memory.
// An item sits in at most one queue: adding it to one removes it from the other.
const DELETE_KEY = "storage-cleanup:delete-queue";
const ARCHIVE_KEY = "storage-cleanup:archive-queue";
const all = [];

// entry: { item_id, name, path, size }
export function makeQueue(storageKey) {
  const listeners = new Set();
  const entries = load();

  function load() {
    try {
      const raw = sessionStorage.getItem(storageKey);
      const parsed = raw ? JSON.parse(raw) : [];
      return new Map(Array.isArray(parsed) ? parsed.map((e) => [e.item_id, e]) : []);
    } catch {
      return new Map();
    }
  }

  function save() {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify([...entries.values()]));
    } catch {
      // Not fatal: the queue still works for this page view.
    }
    for (const listener of listeners) listener();
  }

  const queue = {
    queued: () => [...entries.values()],
    has: (itemId) => entries.has(itemId),
    add(list) {
      const added = [];
      for (const entry of list) {
        if (entry?.item_id) {
          entries.set(entry.item_id, entry);
          added.push(entry.item_id);
        }
      }
      for (const other of all) {
        if (other !== queue) other.drop(added);
      }
      save();
    },
    // Removes ids that moved to another queue; notifies only if any were here.
    drop(itemIds) {
      let changed = false;
      for (const id of itemIds) changed = entries.delete(id) || changed;
      if (changed) save();
    },
    remove(itemIds) {
      for (const id of itemIds) entries.delete(id);
      save();
    },
    clear() {
      entries.clear();
      save();
    },
    onChange(callback) {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
  };
  all.push(queue);
  return queue;
}

const deleteQueue = makeQueue(DELETE_KEY);
export const archive = makeQueue(ARCHIVE_KEY);

export const queued = deleteQueue.queued;
export const has = deleteQueue.has;
export const add = deleteQueue.add;
export const remove = deleteQueue.remove;
export const clear = deleteQueue.clear;
export const onChange = deleteQueue.onChange;

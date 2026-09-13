// H3 recovery must not consume ComfyUI's localStorage workflow-draft quota.
// No server/project writes, eviction, or fallback to large localStorage blobs.
export const RECOVERY_BYTE_LIMIT = 64 * 1024 * 1024;
const STORE = "recovery";
const TOTAL = "__bytes__";
const isRecoveryKey = key => /^(h3-branch-draft-v1:|h3-branch-pending-v1:)/.test(key);
const bytes = (key, value) => value == null ? 0 : 2 * (key.length + value.length);
const requestResult = request => new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
});

async function transaction(db, mode, action) {
    const tx = db.transaction(STORE, mode);
    const done = new Promise((resolve, reject) => {
        tx.oncomplete = resolve;
        tx.onabort = () => reject(tx.error || new Error("Recovery transaction was aborted."));
        tx.onerror = () => {}; // onabort reports transaction failure, not request success.
    });
    // Observe rejection immediately, even if a request in action fails first.
    done.catch(() => {});
    try {
        const result = await action(tx.objectStore(STORE));
        await done;
        return result;
    } catch (error) {
        try { tx.abort(); } catch { /* Already completed/aborted. */ }
        await done.catch(() => {});
        throw error;
    }
}

export class BranchRecoveryStorage {
    constructor({indexedDB, legacyStorage, name = "h3-branch-recovery-v1",
        limit = RECOVERY_BYTE_LIMIT} = {}) {
        this.limit = limit;
        this.warning = "";
        this.ready = this.open(indexedDB, name).then(async db => {
            try {
                await this.migrateLegacy(db, legacyStorage);
                return db;
            } catch (error) { db.close(); throw error; }
        });
        this.ready.catch(() => {}); // The node shows failures when reading/saving.
    }

    open(indexedDB, name) {
        return new Promise((resolve, reject) => {
            if (!indexedDB) return reject(new Error("IndexedDB is unavailable; browser recovery is disabled. Save the branch or export the workflow."));
            const request = indexedDB.open(name, 1);
            let blocked = false;
            request.onupgradeneeded = () => request.result.createObjectStore(STORE);
            request.onerror = () => reject(request.error);
            request.onblocked = () => {
                blocked = true;
                reject(new Error("Recovery storage is blocked by another tab. Close old ComfyUI tabs and reload."));
            };
            request.onsuccess = () => {
                const db = request.result;
                if (blocked) { db.close(); return; }
                db.onversionchange = () => db.close();
                resolve(db);
            };
        });
    }

    async migrateLegacy(db, storage) {
        if (!storage) return;
        // One startup pass over H3's keys only, never a scan on edits/polling.
        const entries = [];
        try {
            for (let i = 0; i < storage.length; i++) {
                const key = storage.key(i);
                if (!key || !isRecoveryKey(key)) continue;
                const value = storage.getItem(key);
                if (value != null) entries.push([key, value]);
            }
        } catch (error) {
            throw new Error(`Cannot read existing browser recovery; it has not been changed. ${error.message}`);
        }
        if (!entries.length) return;
        const copied = await transaction(db, "readwrite", async store => {
            let total = (await requestResult(store.get(TOTAL))) || 0;
            const copied = [];
            for (const [key, value] of entries) {
                const existing = await requestResult(store.get(key));
                // An older browser tab may have written again after migration.
                // Never overwrite either version or delete an unmerged draft.
                if (existing != null && existing !== value) {
                    this.warning = "An older ComfyUI tab has different recovery data. Both copies were kept; export that tab's edits before resolving its legacy recovery.";
                    continue;
                }
                if (existing == null) {
                    store.put(value, key);
                    total += bytes(key, value);
                }
                copied.push([key, value]);
            }
            // Preserve even oversized existing histories. Subsequent writes
            // may shrink them but cannot grow them beyond the normal limit.
            store.put(total, TOTAL);
            return copied;
        });
        // Read back after COMMIT, then remove only the exact unchanged source.
        // Interrupted/failed migration always leaves a recoverable copy.
        await transaction(db, "readonly", async store => {
            for (const [key, value] of copied) {
                if (await requestResult(store.get(key)) !== value) {
                    throw new Error("Recovery migration verification failed; local drafts were kept.");
                }
            }
        });
        for (const [key, value] of copied) {
            try {
                if (storage.getItem(key) === value) storage.removeItem(key);
                else this.warning = "An older tab changed recovery data during migration; its local copy was kept.";
            } catch {
                this.warning = "Recovery was copied, but its old browser-storage entries could not be removed.";
            }
        }
    }

    async getItem(key) {
        if (!isRecoveryKey(key)) throw new Error("Invalid H3 recovery key.");
        return transaction(await this.ready, "readonly", async store =>
            (await requestResult(store.get(key))) ?? null);
    }

    async updateItem(key, update) {
        if (!isRecoveryKey(key)) throw new Error("Invalid H3 recovery key.");
        return transaction(await this.ready, "readwrite", async store => {
            const previous = (await requestResult(store.get(key))) ?? null;
            const total = (await requestResult(store.get(TOTAL))) || 0;
            const next = update(previous);
            if (next === previous) return;
            const size = total - bytes(key, previous) + bytes(key, next);
            if (size > this.limit && size > total) {
                throw new Error("H3 browser recovery has reached its size limit. Existing drafts were kept. Save the branch or export the workflow before leaving it.");
            }
            if (next == null) store.delete(key);
            else store.put(next, key);
            store.put(size, TOTAL);
        });
    }

    setItem(key, value) { return this.updateItem(key, () => value); }
    removeItem(key) { return this.updateItem(key, () => null); }
}

let browserStorage;
export function browserBranchRecoveryStorage(browser = window) {
    if (!browserStorage) {
        // Accessing localStorage itself can throw in restricted contexts.
        let legacyStorage;
        try { legacyStorage = browser.localStorage; } catch { /* IndexedDB may still work. */ }
        browserStorage = new BranchRecoveryStorage({indexedDB:browser.indexedDB, legacyStorage});
    }
    return browserStorage;
}

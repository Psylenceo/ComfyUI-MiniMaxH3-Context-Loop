// Carousel tree/family placement: pure, catalog-only logic. Deliberately
// kept free of `state`/DOM so it can be unit tested directly against plain
// catalog fixtures, without a live ComfyUI widget.

// Crops/edits are stored as new catalog entries carrying parent_asset_id
// (see project_assets.py register_derived_image); this indexes that
// backward pointer into a forward parent -> children lookup for the tree.
export function lineageChildren(assets) {
    const byParent = new Map();
    for (const asset of assets) {
        const parentId = String(asset.parent_asset_id ?? "");
        if (!parentId) continue;
        if (!byParent.has(parentId)) byParent.set(parentId, []);
        byParent.get(parentId).push(asset);
    }
    return byParent;
}
export function lineageFlatten(root, byParent) {
    const ordered = [];
    const visited = new Set();
    (function walk(asset) {
        if (!asset || visited.has(asset.id)) return;
        visited.add(asset.id);
        ordered.push(asset);
        for (const child of byParent.get(String(asset.id)) ?? []) walk(child);
    })(root);
    return ordered;
}
// `byId` must be scoped to whatever set of assets is currently visible (the
// active tab/role filter), not necessarily the whole catalog: a child whose
// parent doesn't pass that filter (or was deleted) has no visible parent
// here, so it correctly counts as a root rather than vanishing.
export function isLineageRoot(asset, byId) {
    const parentId = String(asset.parent_asset_id ?? "");
    if (!parentId) return true;
    const parent = byId.get(parentId);
    if (!parent) return true;
    // Folder placement is authoritative: a crop the user moved to a
    // different folder than its parent is no longer "the parent's child"
    // for display purposes, even though the crop lineage still exists
    // (surfaced in the bottom detail row instead).
    return String(asset.folder_id ?? "") !== String(parent.folder_id ?? "");
}
// Review Gate's frame capture re-uses a tag as an "updated take" of the
// same subject (see project_assets.py _capture_family_tag): @char_bob,
// @char_bob-v1, @char_bob-v2, ... The "-vN" delimiter (rather than a bare
// trailing digit) keeps this from misfiring on tags that just happen to
// end in a number or the letter v, e.g. @vehicle-van or @char-venessa.
const FAMILY_TAG_RE = /^(.*)-v(\d+)$/;
export function familyKey(asset) {
    const match = FAMILY_TAG_RE.exec(String(asset.tag ?? ""));
    return match ? match[1] : String(asset.tag ?? "");
}
export function familyOrdinal(asset) {
    const match = FAMILY_TAG_RE.exec(String(asset.tag ?? ""));
    return match ? Number(match[2]) : 0;
}
// A tag match alone isn't enough to call two assets versions of the same
// thing — @hero (an image) and @hero-v1 (an audio reference) sharing a
// base tag is a coincidence, not a relationship. Group (and look up
// groups) by base tag *and* media kind together so a family can never
// mix images/video/audio.
export function familyGroupKey(asset) {
    return `${familyKey(asset)} ${String(asset.kind ?? "")}`;
}
export function assetFamilies(assets) {
    const byKey = new Map();
    for (const asset of assets) {
        if (asset._unresolved || !asset.tag) continue;
        const key = familyGroupKey(asset);
        if (!byKey.has(key)) byKey.set(key, []);
        byKey.get(key).push(asset);
    }
    for (const members of byKey.values()) {
        members.sort((a, b) => familyOrdinal(a) - familyOrdinal(b));
    }
    return byKey;
}
// Gathers what should render under one or more tree positions (an asset,
// or every member of a family sharing one stack): each position's plain
// (non-hoisted) crop children, plus any family stack that attaches at
// that exact position (see familyAttachPoint) — so a family nested
// entirely under some other asset renders in place there, rather than
// always being pulled up to the top of the tree.
export function collectChildItems(nodes, byParent, hoisted, stacksByAttach) {
    const items = [];
    const seenFamilies = new Set();
    for (const node of nodes) {
        const nodeFolder = String(node.folder_id ?? "");
        for (const child of byParent.get(String(node.id)) ?? []) {
            if (hoisted.has(child.id)) continue;
            // Folder placement is authoritative: a crop moved to a
            // different folder than its parent is no longer "nested
            // under" the parent for display — it surfaces as its own
            // root item in its own folder instead (see isLineageRoot).
            if (String(child.folder_id ?? "") !== nodeFolder) continue;
            items.push({type: "asset", asset: child});
        }
        for (const [key, members] of stacksByAttach.get(String(node.id)) ?? []) {
            if (seenFamilies.has(key)) continue;
            seenFamilies.add(key);
            items.push({type: "family", members});
        }
    }
    return items;
}
// Where a family's stack should render: at the root of the tree, or
// nested under whichever ancestor isn't itself part of the family (e.g.
// two sibling crops of the same source that also happen to form a
// version family stay nested under that shared source, instead of being
// pulled up to the top of the tree).
export function familyAttachPoint(members, byId) {
    const memberIds = new Set(members.map((member) => member.id));
    const base = members[0];
    const parentId = String(base.parent_asset_id ?? "");
    // A stale/out-of-scope parent (deleted, or filtered out of the
    // current tab/role view) is not a usable attach point — fall back to
    // the root rather than leaving the stack with nowhere to render.
    if (!parentId || memberIds.has(parentId) || !byId.has(parentId)) return "";
    const parent = byId.get(parentId);
    // Folder placement is authoritative (see isLineageRoot/collectChildItems):
    // a stack only nests under the parent if the members actually share the
    // parent's folder. Otherwise the members live in a different folder (or
    // are unfiled) and the stack must render as its own top-level entry
    // there, not nested inside the parent's folder where it isn't visible.
    const parentFolder = String(parent.folder_id ?? "");
    if (members.some((member) => String(member.folder_id ?? "") !== parentFolder)) return "";
    return parentId;
}
// The full placement decision for the carousel's main list: which assets
// are plain root items, which collapse into a family stack (and where that
// stack attaches), and which are grouped under a folder — all computed from
// `assets` (the already tab/role-filtered list) plus the full catalog only
// for folder totals. No DOM, no `state`; `renderCarousel` walks `slots` to
// build the actual widget, and reuses `byParent`/`hoisted`/`stacksByAttach`
// for the nested tree under each rendered node.
export function computeCarouselPlacement(assets, catalogAssets, folders, filter) {
    const byParent = lineageChildren(assets);
    const byId = new Map(assets.map((item) => [String(item.id), item]));
    const folderById = new Map(folders.map((folder) => [String(folder.id), folder]));
    const allMembers = new Map(folders.map((folder) => [String(folder.id), []]));
    for (const asset of catalogAssets) {
        const members = allMembers.get(String(asset.folder_id ?? ""));
        if (members) members.push(asset);
    }
    const visibleMembers = new Map(folders.map((folder) => [String(folder.id), []]));
    for (const asset of assets) {
        const members = visibleMembers.get(String(asset.folder_id ?? ""));
        if (members && !asset._unresolved) members.push(asset);
    }
    const families = assetFamilies(assets.filter((asset) => (
        !asset._unresolved && !folderById.has(String(asset.folder_id ?? ""))
    )));
    const hoisted = new Set();
    const stacksByAttach = new Map();
    for (const [key, members] of families) {
        if (members.length <= 1) continue;
        for (const member of members) hoisted.add(member.id);
        const attach = familyAttachPoint(members, byId);
        if (!stacksByAttach.has(attach)) stacksByAttach.set(attach, new Map());
        stacksByAttach.get(attach).set(key, members);
    }
    const rootFamilies = stacksByAttach.get("") ?? new Map();
    const slots = [];
    const renderedFamilies = new Set();
    const renderedFolders = new Set();
    for (const asset of assets) {
        if (!asset._unresolved && hoisted.has(asset.id)) {
            // Either rendered here (family attaches at the root) or it
            // belongs under some other asset and surfaces there instead
            // via collectChildItems — either way it's not a plain item.
            const key = familyGroupKey(asset);
            if (rootFamilies.has(key) && !renderedFamilies.has(key)) {
                renderedFamilies.add(key);
                slots.push({type: "family", key, members: rootFamilies.get(key)});
            }
            continue;
        }
        if (!asset._unresolved && !isLineageRoot(asset, byId)) continue;
        const folder = !asset._unresolved
            ? folderById.get(String(asset.folder_id ?? "")) : null;
        if (!folder) {
            slots.push({type: "asset", asset});
            continue;
        }
        const folderId = String(folder.id);
        if (renderedFolders.has(folderId)) continue;
        renderedFolders.add(folderId);
        const members = (visibleMembers.get(folderId) ?? [])
            .filter((member) => isLineageRoot(member, byId));
        slots.push({
            type: "folder", folder, members,
            totalCount: (allMembers.get(folderId) ?? []).length,
        });
    }
    if (filter === "all") {
        for (const folder of folders) {
            const folderId = String(folder.id);
            if (renderedFolders.has(folderId)) continue;
            renderedFolders.add(folderId);
            slots.push({type: "folder", folder, members: [], totalCount: 0});
        }
    }
    return {byParent, byId, hoisted, stacksByAttach, slots};
}

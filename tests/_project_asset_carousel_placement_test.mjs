import assert from "node:assert/strict";

import {computeCarouselPlacement} from "../web/h3_project_asset_carousel_core.mjs";

let nextId = 1;
function asset(overrides) {
    return {
        id: overrides.id ?? `a${nextId++}`,
        kind: "image",
        role: "picture",
        folder_id: "",
        parent_asset_id: "",
        tag: "",
        ...overrides,
    };
}
function folder(id, name) {
    return {id, name};
}
function slotSummary(plan) {
    // A stable, order-preserving shape for deep-equal comparisons that
    // doesn't depend on object identity surviving a JSON round-trip.
    return plan.slots.map((slot) => {
        if (slot.type === "asset") return {type: "asset", id: slot.asset.id};
        if (slot.type === "family") {
            return {type: "family", members: slot.members.map((m) => m.id)};
        }
        return {
            type: "folder", folder: slot.folder.id,
            members: slot.members.map((m) => m.id), totalCount: slot.totalCount,
        };
    });
}

// --- 1. Moving a child between folders -------------------------------------
// A crop assigned to a different folder than its source must show up as its
// own root item in its real folder, not nested under the source's folder.
{
    const source = asset({id: "source", folder_id: "references"});
    const crop = asset({
        id: "crop", parent_asset_id: "source", folder_id: "characters",
    });
    const folders = [folder("references", "References"), folder("characters", "Characters")];
    const assets = [source, crop];
    const plan = computeCarouselPlacement(assets, assets, folders, "all");
    const summary = slotSummary(plan);
    assert.deepEqual(summary, [
        {type: "folder", folder: "references", members: ["source"], totalCount: 1},
        {type: "folder", folder: "characters", members: ["crop"], totalCount: 1},
    ]);
}

// --- 2. A folder containing only derived children ---------------------------
// The bug: a folder whose only member is a crop of an *unfoldered* source
// rendered as an empty folder with a hardcoded zero count.
{
    const source = asset({id: "source2"});
    const crop = asset({id: "crop2", parent_asset_id: "source2", folder_id: "captures"});
    const folders = [folder("captures", "Captures")];
    const assets = [source, crop];
    const plan = computeCarouselPlacement(assets, assets, folders, "all");
    const summary = slotSummary(plan);
    assert.deepEqual(summary, [
        {type: "asset", id: "source2"},
        {type: "folder", folder: "captures", members: ["crop2"], totalCount: 1},
    ]);
}

// --- 3. Parent/child roles across tabs --------------------------------------
// A matching child whose parent is filtered out of the active tab must get
// its own visible entry; a parent's tab must not show a non-matching child
// nested underneath it (expanding a parent must not bypass the filter).
{
    const parent = asset({id: "picture-parent", role: "picture"});
    const child = asset({
        id: "semantic-child", role: "semantic_anchor", parent_asset_id: "picture-parent",
    });
    const catalogAssets = [parent, child];

    // Semantic tab: only the child matches. It must render on its own.
    const semanticVisible = catalogAssets.filter((item) => item.role === "semantic_anchor");
    const semanticPlan = computeCarouselPlacement(semanticVisible, catalogAssets, [], "semantic");
    assert.deepEqual(slotSummary(semanticPlan), [{type: "asset", id: "semantic-child"}]);

    // Images tab: only the parent matches. The semantic child must not
    // appear nested underneath it (it isn't part of this tab's scope at all).
    const imagesVisible = catalogAssets.filter((item) => item.role === "picture");
    const imagesPlan = computeCarouselPlacement(imagesVisible, catalogAssets, [], "image");
    assert.deepEqual(slotSummary(imagesPlan), [{type: "asset", id: "picture-parent"}]);
    assert.equal(imagesPlan.byParent.get("picture-parent"), undefined);
}

// --- 4. Missing/deleted family parents --------------------------------------
// Two family members whose shared crop-parent has been deleted must still
// render (as a root-attached stack), not vanish because their stale parent
// reference can't be resolved.
{
    const childA = asset({id: "hero", tag: "hero", parent_asset_id: "deleted-parent"});
    const childB = asset({id: "hero-v1", tag: "hero-v1", parent_asset_id: "deleted-parent"});
    const assets = [childA, childB]; // "deleted-parent" is not in the catalog at all
    const plan = computeCarouselPlacement(assets, assets, [], "all");
    assert.deepEqual(slotSummary(plan), [
        {type: "family", members: ["hero", "hero-v1"]},
    ]);
}

// --- 5. Mixed legacy/new capture tags ---------------------------------------
// Older bare-digit takes (hero1, hero2) must stay separate, ungrouped, and
// fully visible — never silently swept into a new -vN family, and never
// grouped with each other on a shared "trailing digit" guess either.
{
    const legacyBase = asset({id: "hero-base", tag: "hero"});
    const legacy1 = asset({id: "hero-1-legacy", tag: "hero1"});
    const legacy2 = asset({id: "hero-2-legacy", tag: "hero2"});
    const newTake = asset({id: "hero-new-v1", tag: "hero-v1"});
    const assets = [legacyBase, legacy1, legacy2, newTake];
    const plan = computeCarouselPlacement(assets, assets, [], "all");
    const summary = slotSummary(plan);
    const familySlot = summary.find((slot) => slot.type === "family");
    assert.deepEqual(familySlot.members, ["hero-base", "hero-new-v1"]);
    const assetSlots = summary.filter((slot) => slot.type === "asset").map((s) => s.id);
    assert.deepEqual(new Set(assetSlots), new Set(["hero-1-legacy", "hero-2-legacy"]));
    assert.equal(summary.length, 3);
}

// --- 6. Unrelated assets with version-looking names -------------------------
// A tag match alone must not group assets of different media kinds.
{
    const image = asset({id: "image-hero", tag: "hero", kind: "image"});
    const audio = asset({id: "audio-hero-v1", tag: "hero-v1", kind: "audio", role: "audio_reference"});
    const assets = [image, audio];
    const plan = computeCarouselPlacement(assets, assets, [], "all");
    const summary = slotSummary(plan);
    assert.ok(!summary.some((slot) => slot.type === "family"));
    assert.deepEqual(new Set(summary.map((s) => s.id)), new Set(["image-hero", "audio-hero-v1"]));
}

// --- 7. Save/reload check ----------------------------------------------------
// Placement is derived purely from catalog data (ids, tags, kind, folder_id,
// parent_asset_id) with no other hidden state, so a JSON round-trip of that
// data (as happens saving/reopening a workflow through catalog_json) must
// reproduce identical placement.
{
    const source = asset({id: "reload-source", folder_id: "refs"});
    const crop = asset({id: "reload-crop", parent_asset_id: "reload-source", folder_id: "chars"});
    const heroBase = asset({id: "reload-hero", tag: "hero"});
    const heroV1 = asset({id: "reload-hero-v1", tag: "hero-v1"});
    const folders = [folder("refs", "References"), folder("chars", "Characters")];
    const assets = [source, crop, heroBase, heroV1];

    const before = slotSummary(computeCarouselPlacement(assets, assets, folders, "all"));

    const reloadedAssets = JSON.parse(JSON.stringify(assets));
    const reloadedFolders = JSON.parse(JSON.stringify(folders));
    const after = slotSummary(
        computeCarouselPlacement(reloadedAssets, reloadedAssets, reloadedFolders, "all"),
    );

    assert.deepEqual(after, before);
}

// --- 8. Version stack whose members sit outside the parent's folder --------
// A source in one folder with unfiled version-family children must still
// nest a *single* child under the source (existing folder rule), but once a
// second version joins to form a stack, the stack must surface at the top
// level (outside the source's folder) rather than being hoisted-but-nested
// inside it with no visible entry anywhere.
{
    const source = asset({id: "stack-source", folder_id: "references"});
    const single = asset({
        id: "hero-only", tag: "hero", parent_asset_id: "stack-source", folder_id: "",
    });
    const folders = [folder("references", "References")];

    // Single unfiled child: nests under the source per the existing folder
    // rule (child folder_id "" !== source folder_id "references" means it's
    // actually its own root per isLineageRoot — confirm that baseline first).
    const singlePlan = computeCarouselPlacement([source, single], [source, single], folders, "all");
    assert.deepEqual(slotSummary(singlePlan), [
        {type: "folder", folder: "references", members: ["stack-source"], totalCount: 1},
        {type: "asset", id: "hero-only"},
    ]);

    // Add a second version to form a stack: the pair must render as a
    // top-level family stack (folder_id "" !== the source's "references"),
    // not silently disappear nested inside the References folder.
    const heroV1 = asset({
        id: "hero-v1", tag: "hero-v1", parent_asset_id: "stack-source", folder_id: "",
    });
    const stackAssets = [source, single, heroV1];
    const stackPlan = computeCarouselPlacement(stackAssets, stackAssets, folders, "all");
    assert.deepEqual(slotSummary(stackPlan), [
        {type: "folder", folder: "references", members: ["stack-source"], totalCount: 1},
        {type: "family", members: ["hero-only", "hero-v1"]},
    ]);

    // Save/reload round-trip must reproduce the same placement.
    const reloaded = JSON.parse(JSON.stringify(stackAssets));
    const reloadedFolders = JSON.parse(JSON.stringify(folders));
    const after = slotSummary(computeCarouselPlacement(reloaded, reloaded, reloadedFolders, "all"));
    assert.deepEqual(after, slotSummary(stackPlan));
}

console.log("H3 Asset Carousel placement: folder authority, tab scoping, stale/legacy family handling, version-stack folder scoping, and save/reload stability pass");

import {inputSource, nodeType, PROJECT_ASSET_MANAGER_TYPES} from "./h3_reference_preview_core.mjs?v=0.7.27";

export const PROJECT_ASSET_CATALOG_CHANGED_EVENT =
    "minimax-h3-project-assets-changed";

const PLAN_TYPES = new Set([
    "MiniMaxH3ChainPlan", "MiniMaxH3ChainPlanModern", "MiniMaxH3ChainPlanStudio",
]);

function graphNodes(graph, seen = new Set()) {
    if (!graph || seen.has(graph)) return [];
    seen.add(graph);
    return [...(graph._nodes ?? graph.nodes ?? [])].flatMap(node => [
        node, ...graphNodes(node.subgraph, seen),
    ]);
}

function widget(node, name) {
    return node?.widgets?.find(item => item.name === name);
}

export function projectAssetManagers(graph) {
    return graphNodes(graph).filter(node => PROJECT_ASSET_MANAGER_TYPES.has(nodeType(node)));
}

export function connectedProjectAssetPlans(manager) {
    const root = manager?.graph?.rootGraph ?? manager?.graph;
    // Resolve each actual project_assets input instead of walking every output:
    // supports Map links, reroutes, Set/Get and native subgraph rails without
    // renaming unrelated Plans which merely share a policy or preview node.
    return graphNodes(root).filter(plan => PLAN_TYPES.has(nodeType(plan))
        && inputSource(plan, "project_assets") === manager);
}

function writePlanRunName(plan, runName) {
    const run = widget(plan, "run_name");
    if (!run || !runName || run.value === runName) return false;
    run.value = runName;
    run.callback?.(runName);
    plan.graph?.setDirtyCanvas?.(true, true);
    return true;
}

export function syncProjectAssetPlanRun(manager, runName) {
    const changed = [];
    for (const plan of connectedProjectAssetPlans(manager)) {
        if (!writePlanRunName(plan, runName)) continue;
        changed.push(plan);
        // The Modern Plan has its own DOM settings form; changing a hidden
        // backing widget alone does not repaint its disabled Run name field.
        plan._h3ChainEditorConnectionRefresh?.();
        plan._h3PlanStudioRefresh?.();
    }
    return changed;
}

export function syncManagedPlanRunName(plan) {
    const manager = inputSource(plan, "project_assets");
    if (!PROJECT_ASSET_MANAGER_TYPES.has(nodeType(manager))) return false;
    const runName = serializedProjectAssetIdentity(
        widget(manager, "run_name")?.value, widget(manager, "catalog_json")?.value);
    return writePlanRunName(plan, runName);
}

export function serializedProjectAssetCatalog(value, requestedProject = "") {
    let catalog = value;
    if (typeof catalog === "string") {
        try { catalog = JSON.parse(catalog); }
        catch (_error) { return null; }
    }
    if (!catalog || typeof catalog !== "object" || Array.isArray(catalog)) {
        return null;
    }
    const project = String(catalog.project ?? "").trim();
    const requested = String(requestedProject ?? "").trim();
    if (requested && project && requested !== project) return null;
    if (!Array.isArray(catalog.assets)
            || !Array.isArray(catalog.reference_slots ?? [])) return null;
    return {
        ...catalog,
        project:project || requested,
        assets:[...catalog.assets],
        reference_slots:[...(catalog.reference_slots ?? [])],
        folders:Array.isArray(catalog.folders) ? [...catalog.folders] : [],
    };
}

export function serializedProjectAssetIdentity(runNameValue, catalogValue) {
    const configured = String(runNameValue ?? "").trim();
    if (configured && configured !== "h3_project") return configured;
    const catalog = serializedProjectAssetCatalog(catalogValue);
    const catalogProject = String(catalog?.project ?? "").trim();
    return catalogProject === "h3_project" ? "" : catalogProject;
}

export function publishProjectAssetCatalogChanged(manager, catalog) {
    const project = String(catalog?.project ?? "").trim();
    const revision = String(catalog?.revision ?? "").trim();
    const signature = `${project}\u0000${revision}`;
    if (manager?._h3ProjectAssetPublishedSignature === signature) return false;
    if (manager) manager._h3ProjectAssetPublishedSignature = signature;
    if (typeof globalThis.dispatchEvent !== "function"
            || typeof globalThis.CustomEvent !== "function") return false;
    globalThis.dispatchEvent(new CustomEvent(
        PROJECT_ASSET_CATALOG_CHANGED_EVENT,
        {detail: {manager, project, revision}},
    ));
    return true;
}

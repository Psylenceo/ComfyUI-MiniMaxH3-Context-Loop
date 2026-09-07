#!/usr/bin/env node
import assert from "node:assert/strict";
import {runRequeueLifecycle, selectAndClaim} from "../web/h3_chain_top_level_requeue_coordinator.mjs";
import {matchingNextSceneHandoff} from "../web/h3_chain_top_level_requeue_core.mjs";

function gate() { let resolve; return {promise: new Promise(r => { resolve = r; }), resolve}; }
async function scenario(which) {
  let enabled = true, claims = 0, submits = 0, releases = 0;
  const poll = gate(), delay = gate(), claimed = gate();
  const current = () => { if (!enabled) throw Object.assign(new Error("disabled"), {preDelivery: true}); };
  const work = runRequeueLifecycle({current,
    waitSafe: async () => { if (which === "poll") await poll.promise; },
    cleanup: async () => { if (which === "cleanup") await delay.promise; },
    claim: async () => { claims++; if (which === "claim") await claimed.promise; return "h"; },
    submit: async () => { submits++; return "ok"; }, release: async () => { releases++; }}).catch(() => {});
  await Promise.resolve(); await Promise.resolve();
  enabled = false;
  poll.resolve(); delay.resolve(); claimed.resolve(); await work;
  return {claims, submits, releases};
}
let r = await scenario("poll"); assert.deepEqual(r, {claims:0, submits:0, releases:0});
r = await scenario("cleanup"); assert.deepEqual(r, {claims:0, submits:0, releases:0});
r = await scenario("claim"); assert.deepEqual(r, {claims:1, submits:0, releases:1});
const record = {runName:"run", clipIndex:3, endClip:6, workflowFingerprint:"wf-current", sourceRevision:"rev-current", checkpointSha:"sha-current"};
const exact = {handoff_id:"exact", action:"next_scene", status:"pending", predecessor_scene:3,start_clip:4,end_clip:6,source_revision:"rev-current",source_checkpoint_sha256:"sha-current",workflow_fingerprint:"wf-current"};
const stale = [
 {...exact,handoff_id:"revision",source_revision:"bad"}, {...exact,handoff_id:"sha",source_checkpoint_sha256:"bad"},
 {...exact,handoff_id:"wf",workflow_fingerprint:"bad"}, {...exact,handoff_id:"range",end_clip:5},
 {...exact,handoff_id:"pred",predecessor_scene:2}, {...exact,handoff_id:"missing",source_revision:null}, exact];
let calls=[]; let selected=await selectAndClaim({record,handoffs:{handoffs:stale},match:matchingNextSceneHandoff,resolveRun:r=>r.runName,claim:async (...x)=>calls.push(x)});
assert.equal(selected.handoff_id,"exact"); assert.deepEqual(calls,[["run","exact"]]);
calls=[]; selected=await selectAndClaim({record,handoffs:{handoffs:stale.slice(0,-1)},match:matchingNextSceneHandoff,resolveRun:r=>r.runName,claim:async (...x)=>calls.push(x)});
assert.equal(selected,null); assert.deepEqual(calls,[]);
calls=[]; selected=await selectAndClaim({record,handoffs:{handoffs:[exact]},match:matchingNextSceneHandoff,resolveRun:()=>"actual-run",claim:async (...x)=>calls.push(x)});
assert.equal(selected,null); assert.deepEqual(calls,[]);
const projectRecord={...record,runName:"actual-run"}; calls=[]; selected=await selectAndClaim({record:projectRecord,handoffs:{handoffs:[{...exact,workflow_fingerprint:"wf-current"}]},match:matchingNextSceneHandoff,resolveRun:()=>"actual-run",claim:async (...x)=>calls.push(x)});
assert.equal(selected.handoff_id,"exact"); assert.deepEqual(calls,[["actual-run","exact"]]);
console.log("top-level requeue coordinator lifecycle: ok");

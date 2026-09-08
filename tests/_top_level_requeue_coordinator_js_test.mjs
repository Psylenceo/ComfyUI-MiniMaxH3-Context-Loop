#!/usr/bin/env node
import assert from "node:assert/strict";
import {runRequeueLifecycle, selectAndClaim, authoritativeRunName, createContinuationTracker, deliverClaimed, finalizeAcceptedSubmission, handleConfirmedSubmissionRejection, handleUncertainSubmission} from "../web/h3_chain_top_level_requeue_coordinator.mjs";
import {matchingNextSceneHandoff} from "../web/h3_chain_top_level_requeue_core.mjs";

function gate() { let resolve; return {promise: new Promise(r => { resolve = r; }), resolve}; }
async function scenario(which) {
  let enabled = true, claims = 0, submits = 0, releases = 0;
  const poll = gate(), delay = gate(), claimed = gate();
  const current = () => { if (!enabled) throw Object.assign(new Error("disabled"), {preDelivery: true}); };
  const work = runRequeueLifecycle({current,
    waitSafe: async () => { if (which === "poll") await poll.promise; },
    cleanup: async () => { if (which === "cleanup") await delay.promise; },
    select: async () => "h", claim: async () => { claims++; if (which === "claim") await claimed.promise; return "h"; },
    prepare: async () => {}, submit: async () => { submits++; return {kind:"accepted"}; }, release: async () => { releases++; }, uncertain: async () => {}}).catch(() => {});
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
for (const mode of ["rejected", "network"]) {
 let claim=0, submit=0, release=0, uncertain=0;
 await runRequeueLifecycle({current:()=>{},waitSafe:async()=>{},cleanup:async()=>{},select:async()=>exact,claim:async()=>claim++,prepare:async()=>{},submit:async()=>{submit++; if(mode==="network") throw Error("network"); return {kind:"rejected"};},release:async()=>release++,uncertain:async()=>uncertain++});
 assert.equal(claim,1); assert.equal(submit,1); assert.equal(release,mode==="rejected"?1:0); assert.equal(uncertain,mode==="network"?1:0);
}
const manager={comfyClass:"MiniMaxH3ProjectAssetManager",widgets:[{name:"run_name",value:"actual-run"}]};
const plan={widgets:[{name:"run_name",value:"stale-plan-name"}],inputs:[{name:"project_assets",link:1}],graph:{links:{1:{origin_id:2}},getNodeById:()=>manager}};
assert.equal(authoritativeRunName(plan),"actual-run");
assert.equal(authoritativeRunName({widgets:[{name:"run_name",value:"plain-run"}]}),"plain-run");
const emptyManager={comfyClass:"MiniMaxH3ProjectAssetManager",widgets:[{name:"run_name",value:""}]};
const emptyPlan={widgets:[{name:"run_name",value:"fallback-run"}],inputs:[{name:"project_assets",link:2}],graph:{links:{2:{origin_id:9}},getNodeById:id=>id===9?emptyManager:null}};
assert.equal(authoritativeRunName(emptyPlan),"fallback-run");
const events=[];
const failureTracker = createContinuationTracker({transition: async (...args) => events.push(args)});
failureTracker.track("run-a", "handoff-a", "prompt-123");
assert.equal(failureTracker.failed("prompt-999"), null);
assert.equal(failureTracker.current().promptId, "prompt-123");
assert.deepEqual(failureTracker.failed("prompt-123"), {runName:"run-a", handoffId:"handoff-a", promptId:"prompt-123"});
assert.equal(failureTracker.current(), null);
failureTracker.track("run-a", "handoff-a", "prompt-123");
assert.equal(await failureTracker.started("prompt-999"), false);
assert.equal(failureTracker.current().promptId, "prompt-123");
assert.equal(await failureTracker.started("prompt-123"), true);
assert.equal(failureTracker.current(), null);
assert.equal(failureTracker.failed("prompt-123"), null);
assert.equal(events.length, 1);
for (const mode of ["rejected","network","accepted","disabled"]) {
 let submit=0, release=0, transitions=[], tracked=[];
 const result=await deliverClaimed({current:()=>{if(mode==="disabled") throw Object.assign(Error(),{preDelivery:true})},handoff:{handoff_id:"h"},prepare:async()=>{},submit:async()=>{submit++;if(mode==="network")throw Error();return mode==="accepted"?{kind:"accepted",promptId:"accepted-123"}:{kind:"rejected"}},release:async()=>release++,transition:async (...x)=>transitions.push(x),track:x=>tracked.push(x)});
 assert.equal(submit,mode==="disabled"?0:1); if(mode==="rejected"||mode==="disabled")assert.equal(release,1); if(mode==="network")assert.equal(transitions[0][1],"uncertain"); if(mode==="accepted")assert.deepEqual(tracked,["accepted-123"]);
}
let routed=[];
const projectClaim=await selectAndClaim({planNode:plan,record:{...record,runName:"actual-run"},handoffs:{handoffs:[exact]},match:matchingNextSceneHandoff,loadHandoffs:async run=>{routed.push(["list",run]);return {handoffs:[exact]}},claim:async (run,id)=>routed.push(["claim",run,id])});
assert.equal(projectClaim._resolvedRunName,"actual-run"); assert.deepEqual(routed.map(x=>x[1]),["actual-run","actual-run"]);
routed=[];
await selectAndClaim({planNode:{widgets:[{name:"run_name",value:"plain-run"}]},record:{...record,runName:"plain-run"},handoffs:{handoffs:[exact]},match:matchingNextSceneHandoff,loadHandoffs:async run=>{routed.push(["list",run]);return {handoffs:[exact]}},claim:async run=>routed.push(["claim",run])});
assert.deepEqual(routed.map(x=>x[1]),["plain-run","plain-run"]);
routed=[];
assert.equal(await selectAndClaim({planNode:{...plan,graph:plan.graph},record:{...record,runName:"different-run"},match:matchingNextSceneHandoff,loadHandoffs:async run=>{routed.push(["list",run]);return {handoffs:[exact]}},claim:async run=>routed.push(["claim",run])}),null); assert.equal(routed.length,0);
const acceptedCalls=[];
assert.deepEqual(await finalizeAcceptedSubmission({runName:"actual-run",handoffId:"handoff-123",promptId:"prompt-456",transitionQueued:async (...x)=>acceptedCalls.push(["queued",...x]),trackContinuation:(...x)=>acceptedCalls.push(["track",...x])}),{kind:"accepted",promptId:"prompt-456"});
assert.deepEqual(acceptedCalls,[["queued","actual-run","handoff-123","queued","prompt-456"],["track","actual-run","handoff-123","prompt-456"]]);
await assert.rejects(finalizeAcceptedSubmission({runName:"actual-run",handoffId:"handoff-123",promptId:"",transitionQueued:async()=>acceptedCalls.push("bad"),trackContinuation:()=>acceptedCalls.push("bad")}));
assert.equal(acceptedCalls.length,2);
const releases=[];
assert.deepEqual(await handleConfirmedSubmissionRejection({runName:"actual-run",handoffId:"handoff-123",releaseHandoff:async (...x)=>releases.push(x)}),{kind:"rejected",released:true});
assert.deepEqual(releases,[["actual-run","handoff-123"]]);
await assert.rejects(handleConfirmedSubmissionRejection({runName:"",handoffId:"handoff-123",releaseHandoff:async()=>releases.push("bad")})); assert.equal(releases.length,1);
const uncertainCalls=[];
assert.deepEqual(await handleUncertainSubmission({runName:"actual-run",handoffId:"handoff-123",markUncertain:async (...x)=>uncertainCalls.push(x)}),{kind:"uncertain"});
assert.deepEqual(uncertainCalls,[["actual-run","handoff-123","uncertain"]]);
await assert.rejects(handleUncertainSubmission({runName:"",handoffId:"handoff-123",markUncertain:async()=>uncertainCalls.push("bad")})); assert.equal(uncertainCalls.length,1);
const projectFlow=[];
const claimedProject = await selectAndClaim({planNode:plan, record:{...record,runName:"actual-run"}, match:matchingNextSceneHandoff,
 loadHandoffs:async run=>{projectFlow.push(["list",run]); return {handoffs:[exact]};},
 claim:async (run,id)=>projectFlow.push(["claim",run,id])});
let projectSubmits=0;
await deliverClaimed({current:()=>{}, handoff:claimedProject, prepare:async()=>{}, submit:async()=>{projectSubmits++; return {kind:"accepted",promptId:"project-prompt-123"};}, release:async()=>{},
 transition:async()=>{}, track:()=>{}});
await finalizeAcceptedSubmission({runName:claimedProject._resolvedRunName,handoffId:claimedProject.handoff_id,promptId:"project-prompt-123",transitionQueued:async (...x)=>projectFlow.push(["queued",...x]),trackContinuation:(...x)=>projectFlow.push(["track",...x])});
assert.deepEqual(projectFlow,[["list","actual-run"],["claim","actual-run",exact.handoff_id],["queued","actual-run",exact.handoff_id,"queued","project-prompt-123"],["track","actual-run",exact.handoff_id,"project-prompt-123"]]);
assert.equal(projectSubmits,1);
const rejectedFlow=[];
const rejectedClaim = await selectAndClaim({planNode:plan,record:{...record,runName:"actual-run"},match:matchingNextSceneHandoff,
 loadHandoffs:async run=>{rejectedFlow.push(["list",run]);return {handoffs:[exact]};},claim:async (run,id)=>rejectedFlow.push(["claim",run,id])});
let rejectedSubmits=0;
await deliverClaimed({current:()=>{},handoff:rejectedClaim,prepare:async()=>{},submit:async()=>{rejectedSubmits++;return {kind:"rejected"};},release:async ()=>rejectedFlow.push(["release",rejectedClaim._resolvedRunName,rejectedClaim.handoff_id]),transition:async()=>rejectedFlow.push(["transition"]),track:()=>rejectedFlow.push(["track"])});
assert.deepEqual(rejectedFlow,[["list","actual-run"],["claim","actual-run",exact.handoff_id],["release","actual-run",exact.handoff_id]]);assert.equal(rejectedSubmits,1);
console.log("top-level requeue coordinator lifecycle: ok");

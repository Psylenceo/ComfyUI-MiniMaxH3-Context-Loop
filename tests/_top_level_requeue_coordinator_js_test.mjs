#!/usr/bin/env node
import assert from "node:assert/strict";
import {runRequeueLifecycle} from "../web/h3_chain_top_level_requeue_coordinator.mjs";

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
console.log("top-level requeue coordinator lifecycle: ok");

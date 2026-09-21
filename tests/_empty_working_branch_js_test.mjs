import assert from "node:assert/strict";
import {visibleWorkingBranches, emptyBranchKeepTarget} from "../web/h3_working_branches.mjs";

const main = {id:"main",name:"Original",hidden:true};
const keep = {id:"a".repeat(32),name:"SelfLift"};
const empty = {id:"b".repeat(32),name:"960x544"};
const records = [main, keep, empty];
assert.deepEqual(visibleWorkingBranches(records, keep.id, keep.id), [keep, empty]);
assert.deepEqual(visibleWorkingBranches(records, "main", keep.id), records,
    "An already-open workflow on hidden Original must not silently switch");
assert.deepEqual(visibleWorkingBranches(records, keep.id, "main"), records);
assert.equal(emptyBranchKeepTarget(records, empty.id, keep.id), keep.id);
assert.equal(emptyBranchKeepTarget(records, empty.id, "main"), keep.id,
    "Hidden Original cannot be used as a fallback keep target");
assert.equal(emptyBranchKeepTarget(records, empty.id, keep.id, keep.id), keep.id);
assert.equal(emptyBranchKeepTarget(records, empty.id, keep.id, empty.id), null,
    "The branch open in Plan Studio cannot be removed");
assert.equal(emptyBranchKeepTarget(records, empty.id, keep.id, "missing"), null);
assert.equal(emptyBranchKeepTarget([keep], keep.id, keep.id), null,
    "The last visible branch cannot be removed");
assert.equal(emptyBranchKeepTarget([main, keep], keep.id, keep.id), null);
assert.equal(emptyBranchKeepTarget(records, "main", "main", keep.id), keep.id);
console.log("Empty branch choices: active Plan protection, hidden Original and last branch guards pass");

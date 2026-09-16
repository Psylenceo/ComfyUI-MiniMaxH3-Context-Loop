import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {bindNodeWheel} from "../web/h3_dom_wheel.mjs";

class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.listeners = {}; this.style = {};
        this.value = ""; this.isConnected = true; this.loads = 0; this.currentTime = 0;
    }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    removeEventListener(name, fn) { if (this.listeners[name] === fn) delete this.listeners[name]; }
    setAttribute(name, value) { this[name] = value; }
    removeAttribute(name) { delete this[name]; }
    pause() { this.paused = true; }
    load() { this.loads++; this.currentTime = 0; }
    click() { assert.ok(!this.disabled, `${this.textContent} disabled`); this.listeners.click?.(); }
    change(value) { this.value = value; this.listeners.change?.(); }
}
let extension, nextTimer = 0, fail = false, confirm = true, delayPost = null, delayedGet = null;
const timers = new Map(), requests = [];
const window = new Element("window");
Object.assign(window, {setTimeout:fn => { timers.set(++nextTimer, fn); return nextTimer; },
    clearTimeout:id => timers.delete(id), confirm:() => confirm});
const document = new Element("document");
Object.assign(document, {visibilityState:"visible", createElement:tag => new Element(tag)});
const candidates = Array.from({length:10}, (_, n) => ({number:n+1, revision:String(n+1).padStart(32,"0"),
    seed:(2n**64n - BigInt(n+1)).toString(), scene_prompt:`Saved prompt ${n+1}`, raw_frames:73,
    has_audio:true, video:{filename:`take_${n+1}.mp4`,subfolder:"film",type:"output"}}));
const gate = {token:"live",run_name:"film",branch_id:"b".repeat(32),clip_index:2,node_id:"99:10",
    shot_id:"second",candidate_count:10,generated_count:10,actionable:true};
let reviews = [gate];
const response = (body, status=200) => ({ok:status===200,status,json:async()=>structuredClone(body)});
const api = {apiURL:path => `/proxy${path}`, async fetchApi(path, options={}) {
    requests.push({path,options});
    if (options.method === "POST") {
        if (delayPost) return delayPost;
        return response({ok:true});
    }
    if (fail) throw new Error("offline");
    const params = new URL(path,"http://test").searchParams;
    const selected = reviews.find(item => item.token === params.get("token"));
    const candidate = candidates.find(item => item.revision === params.get("candidate_revision")) ?? candidates[0];
    const reply = response({reviews,selected:selected ? {...selected,candidates,
        candidate,review_each_candidate:false,candidate_remaining:0} : null});
    if (delayedGet) { const pending = delayedGet; delayedGet = null; return pending; }
    return reply;
}};
const app = {configuringGraph:false,registerExtension:value=>{extension=value;},
    queuePrompt:()=>assert.fail("Relay must never queue the current canvas")};
const source = fs.readFileSync(new URL("../web/h3_review_relay.js", import.meta.url), "utf8")
    .replace(/^import .*;\n/gm, "");
vm.runInNewContext(source,{app,api,window,document,console,URLSearchParams,AbortController,bindNodeWheel});
class Node {
    constructor() { this.graph = {}; this.size=[200,200]; this.widgets=[]; }
    addDOMWidget(name,type,root,options) { this.root=root; this.options=options; return {}; }
    setSize(value) { this.size=value; }
}
extension.beforeRegisterNodeDef(Node,{name:"MiniMaxH3ReviewRelay"});
const node = new Node(); node.onNodeCreated();
const settle = () => new Promise(resolve => setImmediate(resolve));
const tick = async () => {
    const pending=[...timers]; timers.clear();
    for (const [,fn] of pending) fn();
    await settle();
};
const all = item => [item,...item.children.flatMap(all)];
const items=all(node.root);
const buttons=items.filter(item=>item.tag==="button");
const button=text=>buttons.find(item=>item.textContent===text);
const [gates,takes]=items.filter(item=>item.tag==="select");
const video=items.find(item=>item.tag==="video"), prompt=items.find(item=>item.tag==="textarea");
const keep=items.find(item=>item.tag==="input");
const status=items.find(item=>item.role==="status");
await tick();
assert.equal(requests.length,2,"one listing then one selected-gate request");
assert.equal(timers.size,1,"one polling timer per relay");
assert.equal(takes.children.length,10);
assert.equal(prompt.value,"Saved prompt 1");
assert.equal(prompt.readOnly,true);
assert.equal(node.options.serialize,false,"no live tokens or candidate content in workflow serialization");
assert.match(video.src,/^\/proxy\/view\?/);
video.currentTime=2.5;
const loads=video.loads;
await tick(); await tick();
assert.equal(video.loads,loads,"unchanged polling never reloads previews");
assert.equal(video.currentTime,2.5,"scrubbing survives refresh");

takes.change(candidates[4].revision); await settle();
assert.equal(prompt.value,"Saved prompt 5");
assert.match(takes.children[4].textContent,/18446744073709551611/);
button("▶").click(); await settle(); assert.equal(prompt.value,"Saved prompt 6");
button("◀").click(); await settle(); assert.equal(prompt.value,"Saved prompt 5");
keep.checked=false; keep.listeners.change();
await tick(); assert.equal(keep.checked,false,"keep marks survive polling");
button("▶").click(); await settle();
confirm=false; button("Approve selected").click(); await settle();
assert.equal(requests.filter(item=>item.options.method==="POST").length,0,"deletion requires confirmation");
confirm=true;
let resolvePost;
delayPost=new Promise(resolve=>{resolvePost=resolve;});
button("Approve selected").click();
assert.equal(button("Approve selected").disabled,true);
button("Approve selected").listeners.click();
const posted=requests.filter(item=>item.options.method==="POST");
assert.equal(posted.length,1,"double click submits only once");
const body=JSON.parse(posted[0].options.body);
assert.equal(body.candidate_revision,candidates[5].revision);
assert.equal(body.candidate_revisions.length,9);
assert.ok(!body.candidate_revisions.includes(candidates[4].revision));
assert.equal(body.run_name,"film"); assert.equal(body.branch_id,gate.branch_id);
assert.equal(body.clip_index,2);
assert.ok(!("scene_prompt" in body) && !("seed" in body));
assert.deepEqual(Object.keys(posted[0].options.headers),["Content-Type"],"no workflow ownership claim");
reviews=[]; resolvePost(response({ok:true})); await settle(); delayPost=null;
assert.match(status.textContent,/original job will resume/);
await tick(); assert.equal(button("Approve selected").disabled,true);

// A disappearing gate must not silently redirect actions to another project.
reviews=[{...gate,token:"other",run_name:"other"}]; await tick();
assert.equal(gates.value,""); assert.equal(button("Approve selected").disabled,true);
gates.change("other"); await settle(); assert.equal(button("Approve selected").disabled,false);
fail=true; await tick(); assert.equal(button("Approve selected").disabled,true);
assert.match(status.textContent,/Connection unavailable/);
fail=false; await tick(); assert.equal(button("Approve selected").disabled,false);

// Growing batches can be inspected but cannot be accepted through the wrong
// cancellation/queue path. The waiting-token transition reconnects by identity.
reviews=[{...reviews[0],actionable:false}]; await tick();
assert.equal(prompt.value,"Saved prompt 1");
assert.equal(button("Approve selected").disabled,true);
reviews=[{...reviews[0],actionable:true,token:"now-waiting"}]; await tick();
assert.equal(gates.value,"now-waiting"); assert.equal(button("Approve selected").disabled,false);

let resolveGet;
delayedGet=new Promise(resolve=>{resolveGet=resolve;});
void node._h3ReviewRelay.refresh();
takes.change(candidates[7].revision); await settle();
assert.equal(prompt.value,"Saved prompt 8");
resolveGet(response({reviews,selected:{...reviews[0],candidates,candidate:candidates[0]}}));
await settle(); assert.equal(prompt.value,"Saved prompt 8","stale response cannot replace selection");
assert.equal(timers.size,1);

document.visibilityState="hidden";
const requestCount=requests.length; await tick(); assert.equal(requests.length,requestCount);
document.visibilityState="visible"; await tick();
node.root.isConnected=false;
const detachedCount=requests.length; await tick(); assert.equal(requests.length,detachedCount);
node.onRemoved();
assert.equal(timers.size,0); assert.equal(node._h3ReviewRelay,null);
assert.equal(window.listeners.focus,undefined); assert.equal(document.listeners.visibilitychange,undefined);
console.log("Review relay UI: real mount, 10 takes, lazy stable video, keep marks, original-job decisions, reconnect, races and teardown pass");

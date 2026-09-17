import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.style = {}; this.dataset = {};
        this.isConnected = true; this.listeners = {}; this.currentTime = 0; this.loads = 0;
    }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    querySelectorAll(tag) { return this.children.flatMap(x => [x, ...x.querySelectorAll("*")]).filter(x => tag === "*" || x.tag === tag); }
    pause() { this.paused = true; }
    load() { this.loads++; this.currentTime = 0; }
    removeAttribute(name) { delete this[name]; }
}
let extension, nextTimer=0, gets=0;
const timers = new Map(), events = new Map(), posts=[];
const batch = { id:"a".repeat(64), run_name:"test", scene:1, scene_name:"walk", batch_name:"hunt_1",
    phase:"waiting", active:true, selected:null, low_steps:20, high_steps:5,
    candidates:Array.from({length:3}, (_, i) => ({ordinal:i+1,seed:(2n**64n-1n-BigInt(i)).toString(),
        checkpoint:`take_${i+1}.safetensors`,preview:`h3_chains/test/processing/take_${i+1}.mp4`})) };
const document = {hidden:false,createElement:tag => new Element(tag)};
const app = {graph:{}, registerExtension:value => {extension=value;}, queuePrompt:()=>assert.fail("Never autoqueue")};
const api = { apiURL:path=>`/proxy${path}`, addEventListener:(name,cb)=>events.set(name,cb),
    async fetchApi(path, options={}) {
        if (options.method === "POST") {
            const body=JSON.parse(options.body); posts.push(body); batch.selected=body.ordinal;
            return {ok:true,json:async()=>({ok:true})};
        }
        gets++;
        return {ok:true,json:async()=>({batches:[structuredClone(batch)]})};
    }};
const source = fs.readFileSync(new URL("../web/h3_selflift_hunt.js",import.meta.url),"utf8").replace(/^import .*;\n/gm, "");
vm.runInNewContext(source,{app,api,document,URLSearchParams,queueMicrotask,bindNodeWheel:()=>{},
    setInterval:fn=>{timers.set(++nextTimer,fn);return nextTimer;},clearInterval:id=>timers.delete(id)});
class Node {
    constructor(id) { this.id=id; this.graph=app.graph; this.size=[200,200]; this.properties={}; }
    addDOMWidget(name,type,root,options) {
        this.root=root;
        assert.equal(options.serialize,false);
        return this.reviewWidget={options};
    }
    setSize(size) { this.size=size; }
    reviewHeight() {
        // LiteGraph gives computeSize priority over flexible DOM sizing.
        const widget=this.reviewWidget;
        return widget.computeSize ? widget.computeSize(this.size[0])[1] + 4
            : Math.max(widget.options.getMinHeight?.() ?? 50, this.size[1]-320);
    }
}
extension.beforeRegisterNodeDef(Node,{name:"MiniMaxH3SelfLiftSeedHunt"});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const tick=async()=>{for(const fn of timers.values())fn();await settle();};
const node=new Node(1);node.onNodeCreated();await settle();
node.setSize([920,1100]);
assert.equal(node.reviewHeight(),780,"review uses the node's spare height instead of staying fixed at 440px");
assert.equal(node.reviewWidget.options.getMinHeight(),440,"small nodes retain a usable scrollable minimum");
assert.equal(timers.size,1);
assert.equal(node.root.querySelectorAll("video").length,3);
const videos=node.root.querySelectorAll("video");
assert.ok(videos.every(v=>v.preload==="none" && v.loads===0));
assert.ok(node.root.querySelectorAll("div").some(v=>v.textContent?.includes("18446744073709551615")),"keep uint64 seed as a string");
videos[0].currentTime=1.4;
node.setSize([720,950]);
assert.equal(node.reviewHeight(),630,"review follows manual resizing in both directions");
await tick();
assert.equal(node.root.querySelectorAll("video")[0],videos[0]);
assert.equal(videos[0].currentTime,1.4);
const button=node.root.querySelectorAll("button").find(b=>b.dataset.ordinal==="2");
batch.phase="low";batch.current=4;await tick();
assert.equal(button.disabled,false,"completed takes stay selectable while low sampling is busy");
assert.equal(button.textContent,"Use take 2 now");
await button.onclick();
assert.deepEqual(posts,[{id:batch.id,ordinal:2}]);
assert.equal(node.properties.h3_selflift_batch,batch.id);
assert.ok(node._h3SelfLiftHunt.status.textContent.includes("Finishing and saving take 4; then upscale take 2"));
assert.equal(node.root.querySelectorAll("video")[0],videos[0],"choosing early preserves preview playback");
batch.phase="preview";await tick();
assert.equal(button.disabled,false,"completed takes stay selectable during tiny decode");
assert.ok(node._h3SelfLiftHunt.status.textContent.includes("skip remaining candidates"));
batch.phase="high";await tick();
assert.ok(node.root.querySelectorAll("button").filter(b=>b.dataset.ordinal).every(b=>b.disabled));
assert.ok(!node._h3SelfLiftHunt.status.textContent.includes("Finishing and saving"));
batch.active=false;await tick();
assert.equal(button.disabled,false,"offline saved take can be chosen before requeue");
const second=new Node(2);second.size=[920,1200];second.properties.h3_selflift_batch=batch.id;second.onNodeCreated();await settle();
assert.deepEqual(Array.from(second.size),[920,1200],"recreation must not shrink the saved viewport");
assert.equal(second.reviewHeight(),880);
assert.equal(timers.size,1,"shared polling, not a timer per widget");
assert.equal(second.root.querySelectorAll("video").length,3,"refresh/recreate restores saved takes");
document.hidden=true;const before=gets;await tick();assert.equal(gets,before);
document.hidden=false;
node.onRemoved();second.onRemoved();assert.equal(timers.size,0);
assert.equal(videos[0].src,undefined);
console.log("SelfLift hunt UI: early choice, pending status, high-pass lock, persistent takes, stable playback, uint64 seeds, shared/hidden polling and cleanup pass");

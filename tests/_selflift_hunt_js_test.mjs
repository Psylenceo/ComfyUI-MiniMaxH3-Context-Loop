import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.style = {}; this.dataset = {};
        this.isConnected = true; this.listeners = {}; this.currentTime = 0; this.loads = 0;
    }
    append(...items) { this.children.push(...items); }
    appendChild(item) { this.append(item); }
    replaceChildren(...items) { this.children = items; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    querySelectorAll(tag) { return this.children.flatMap(x => [x, ...x.querySelectorAll("*")]).filter(x => tag === "*" || x.tag === tag); }
    pause() { this.paused = true; }
    load() { this.loads++; this.currentTime = 0; }
    removeAttribute(name) { delete this[name]; }
    setAttribute(name, value) { this[name] = value; }
    getAttribute(name) { return this[name]; }
}
let extension, nextTimer=0, gets=0, confirmClean=false, cleanError=false, removedBatch=false;
const timers = new Map(), events = new Map(), posts=[];
const batch = { id:"a".repeat(64), run_name:"test", scene:1, scene_name:"walk", batch_name:"hunt_1",
    created_at:123,
    phase:"waiting", active:true, selected:null, low_steps:20, high_steps:5,
    candidates:Array.from({length:3}, (_, i) => ({ordinal:i+1,seed:(2n**64n-1n-BigInt(i)).toString(),
        checkpoint:`take_${i+1}.safetensors`,preview:`h3_chains/test/processing/take_${i+1}.mp4`})) };
const head = new Element("head");
const document = {hidden:false,head,createElement:tag => new Element(tag),
    getElementById:id=>head.children.find(item=>item.id===id)};
const app = {graph:{}, registerExtension:value => {extension=value;}, queuePrompt:()=>assert.fail("Never autoqueue")};
const api = { apiURL:path=>`/proxy${path}`, addEventListener:(name,cb)=>events.set(name,cb),
    async fetchApi(path, options={}) {
        if (options.method === "POST") {
            if (path === "/h3/selflift/clean") {
                if (cleanError) return {ok:false,json:async()=>({error:"Stop the running hunt"})};
                const body=JSON.parse(options.body); posts.push(body); removedBatch=true;
                return {ok:true,json:async()=>({ok:true,files:7,bytes:1048576})};
            }
            const body=JSON.parse(options.body); posts.push(body); batch.selected=body.ordinal;
            return {ok:true,json:async()=>({ok:true})};
        }
        gets++;
        return {ok:true,json:async()=>({batches:removedBatch ? [] : [structuredClone(batch)]})};
    }};
const source = fs.readFileSync(new URL("../web/h3_selflift_hunt.js",import.meta.url),"utf8").replace(/^import .*;\n/gm, "");
vm.runInNewContext(source,{app,api,document,URLSearchParams,queueMicrotask,bindNodeWheel:()=>{},
    window:{confirm:()=>confirmClean},
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
const part=(node, name)=>node.root.querySelectorAll("*").find(el=>el.className?.split(" ").includes(`h3sh-${name}`));
assert.equal(node.root.querySelectorAll("video").length,1,"one focused player, not a wall of videos");
assert.equal(part(node,"dots").children.length,3);
assert.equal(part(node,"help").open,undefined,"help starts collapsed");
const clean=node.root.querySelectorAll("button").find(b=>b.textContent==="Clean saved takes");
assert.equal(clean.disabled,true,"cannot clean a running or waiting hunt");
await clean.onclick();assert.deepEqual(posts,[]);
const videos=node.root.querySelectorAll("video");
assert.ok(videos.every(v=>v.preload==="metadata" && v.loads===1),"only the viewed preview preloads");
assert.ok(part(node,"seed").textContent.includes("18446744073709551615"),"keep uint64 seed as a string");
assert.equal(part(node,"dots").children[0].getAttribute("aria-pressed"),"true");
videos[0].currentTime=1.4;
node.setSize([720,950]);
assert.equal(node.reviewHeight(),630,"review follows manual resizing in both directions");
await tick();
assert.equal(node.root.querySelectorAll("video")[0],videos[0]);
assert.equal(videos[0].currentTime,1.4);
batch.candidates.push({ordinal:4,seed:"4",preview:"test/take_4.mp4"});await tick();
assert.equal(part(node,"dots").children.length,4,"new takes update navigation");
assert.equal(videos[0].currentTime,1.4,"arrival of a new take does not restart playback");
assert.equal(videos[0].loads,1,"status polling never reloads the video");
part(node,"nav").children[2].onclick();
assert.equal(node.properties.h3_selflift_preview.ordinal,2);
assert.deepEqual(posts,[],"browsing is not approval");
assert.ok(videos[0].src.includes("take_2.mp4"));
videos[0].currentTime=2.5;
const button=part(node,"approve");
batch.phase="low";batch.current=4;await tick();
assert.equal(button.disabled,false,"completed takes stay selectable while low sampling is busy");
assert.equal(button.textContent,"Use take 2 now");
await button.onclick();
assert.deepEqual(posts,[{id:batch.id,ordinal:2}]);
assert.equal(node.properties.h3_selflift_batch,batch.id);
assert.ok(node._h3SelfLiftHunt.status.textContent.includes("Finishing and saving take 4; then upscale take 2"));
assert.equal(node.root.querySelectorAll("video")[0],videos[0],"choosing early preserves preview playback");
assert.equal(videos[0].currentTime,2.5);
batch.phase="preview";await tick();
assert.equal(button.disabled,false,"completed takes stay selectable during tiny decode");
assert.ok(node._h3SelfLiftHunt.status.textContent.includes("skip remaining candidates"));
batch.phase="high";await tick();
assert.equal(button.disabled,true);
part(node,"dots").children[0].onclick();
assert.equal(node.properties.h3_selflift_preview.ordinal,1,"can inspect other takes during upscale");
assert.equal(part(node,"dots").children[1].dataset.chosen,"true","chosen and viewed take stay distinct");
assert.equal(part(node,"dots").children[0].getAttribute("aria-pressed"),"true");
await button.onclick();assert.equal(posts.length,1,"cannot change high-pass approval");
assert.ok(!node._h3SelfLiftHunt.status.textContent.includes("Finishing and saving"));
batch.active=false;await tick();
assert.equal(button.disabled,false,"offline saved take can be chosen before requeue");
const second=new Node(2);second.size=[920,1200];second.properties.h3_selflift_batch=batch.id;second.onNodeCreated();await settle();
assert.deepEqual(Array.from(second.size),[920,1200],"recreation must not shrink the saved viewport");
assert.equal(second.reviewHeight(),880);
assert.equal(timers.size,1,"shared polling, not a timer per widget");
assert.equal(second.root.querySelectorAll("video").length,1,"refresh/recreate restores the focused player");
assert.equal(second.properties.h3_selflift_preview.ordinal,2,"reopening defaults to the chosen take");
assert.equal(head.children.length,1,"styles are shared, independent of normal gate mounting");
const recovered=new Node(3);
recovered.properties=structuredClone(node.properties);
recovered.onNodeCreated();await settle();
assert.equal(recovered.properties.h3_selflift_preview.ordinal,1,"saved browsing position survives recreation without changing the approval");
recovered.onRemoved();
videos[0].listeners.error();
assert.equal(part(node,"notice").hidden,false,"failed video loading has a readable retry hint");
videos[0].error={code:4};const loadsBeforeRetry=videos[0].loads;
await node.root.querySelectorAll("button").find(b=>b.textContent==="Refresh saved takes").onclick();
assert.equal(videos[0].loads,loadsBeforeRetry+1,"refresh retries a failed media load");
videos[0].error=null;videos[0].listeners.loadeddata();
assert.equal(part(node,"notice").hidden,true);
batch.created_at=124;batch.selected=null;await tick();
assert.ok(videos[0].src.includes("h3_hunt_created=124"),"recreated batches do not reuse cached media from a deleted hunt");
assert.equal(node.properties.h3_selflift_preview.ordinal,1);
document.hidden=true;const before=gets;await tick();assert.equal(gets,before);
document.hidden=false;
assert.equal(clean.disabled,false,"offline batch can be cleaned");
await clean.onclick();assert.equal(posts.length,1,"cancelled confirmation deletes nothing");
confirmClean=true;cleanError=true;await clean.onclick();
assert.equal(part(node,"dots").children.length,4,"failed cleanup retains takes");
assert.equal(clean.disabled,false,"cleanup error can be retried");
assert.ok(node.root.querySelectorAll("p").some(p=>p.textContent==="Stop the running hunt"));
cleanError=false;await clean.onclick();
assert.deepEqual(posts[1],{id:batch.id,created_at:124,confirm:true});
assert.equal(videos[0].src,undefined,"last batch cleanup clears the media source");
assert.equal(part(node,"candidates").hidden,true);
assert.equal(part(second,"candidates").hidden,true,"other panels refresh too");
assert.equal(clean.disabled,true);
assert.ok(node.root.querySelectorAll("p").some(p=>p.textContent?.includes("Saved scenes were kept")));
node.onRemoved();second.onRemoved();assert.equal(timers.size,0);
assert.equal(videos[0].src,undefined);
console.log("SelfLift hunt UI: focused carousel, browse vs approve, early choice, high-pass lock, stable playback/new arrivals, restored view, media retry, uint64 seeds, shared/hidden polling and cleanup pass");

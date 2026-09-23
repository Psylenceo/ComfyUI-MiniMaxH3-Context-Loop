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
const timers = new Map(), events = new Map(), posts=[], previewPosts=[], selectionPosts=[];
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
            if (path === "/h3/selflift/selection") {
                const body=JSON.parse(options.body); selectionPosts.push(body);
                if (body.selection_version !== (batch.selection_version ?? 0))
                    return {ok:false,json:async()=>({error:"This hunt selection changed; refresh"})};
                Object.assign(batch,{marked:body.ordinals,main:body.main,
                    selection_version:(batch.selection_version ?? 0)+1});
                return {ok:true,json:async()=>({ok:true})};
            }
            if (path === "/h3/selflift/upscale-preview") {
                const body=JSON.parse(options.body); previewPosts.push(body); batch.upscale_request=body.ordinal;
                return {ok:true,json:async()=>({ok:true})};
            }
            if (path === "/h3/selflift/clean") {
                if (cleanError) return {ok:false,json:async()=>({error:"Stop the running hunt"})};
                const body=JSON.parse(options.body); posts.push(body); removedBatch=true;
                return {ok:true,json:async()=>({ok:true,files:7,bytes:1048576})};
            }
            const body=JSON.parse(options.body); posts.push(body); batch.selected=body.ordinal;
            Object.assign(batch,{marked:body.ordinals,main:body.ordinal,
                selected_ordinals:body.ordinals,selection_version:(batch.selection_version ?? 0)+1});
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
    constructor(id) {
        this.id=id; this.graph=app.graph; this.size=[200,200]; this.properties={};
        this.widgets=[{name:"review_enabled",value:true,callback(value) { this.lastChange=value; }}];
    }
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
const review=node.widgets[0];
assert.equal(review.label,"Review gate");
assert.equal(part(node,"gate-notice").hidden,true,"review remains enabled by default");
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
const upscale=part(node,"upscale-preview");
assert.equal(upscale.disabled,false);
assert.equal(upscale.textContent,"Preview upscale");
await upscale.onclick();
assert.deepEqual(previewPosts,[{id:batch.id,ordinal:2,created_at:123}]);
assert.deepEqual(posts,[],"requesting a lift preview never approves or autoqueues");
assert.equal(batch.selected,null);
assert.equal(button.disabled,true,"approval waits until optional preview completes");
assert.equal(upscale.disabled,true,"only one preview request at a time");
assert.equal(videos[0].currentTime,2.5,"request metadata does not restart the low preview");
batch.phase="upscale_preview";await tick();
assert.ok(part(node,"status").textContent.includes("no high denoising or approval"));
batch.candidates[1].upscale_preview="h3_chains/test/processing/take_2.upscale.mp4";
batch.upscale_request=null;batch.phase="waiting";await tick();
assert.ok(videos[0].src.includes("take_2.upscale.mp4"),"completed requested preview opens in the same player");
assert.equal(upscale.textContent,"Show low preview");
assert.ok(part(node,"view").textContent.includes("before high denoising"));
videos[0].currentTime=1.2;const upscaleLoads=videos[0].loads;await tick();
assert.equal(videos[0].loads,upscaleLoads);
assert.equal(videos[0].currentTime,1.2,"polling does not restart the upscaled preview");
await upscale.onclick();
assert.ok(videos[0].src.includes("take_2.mp4"));
assert.equal(upscale.textContent,"Show upscale preview");
await upscale.onclick();
assert.equal(previewPosts.length,1,"cached preview switching performs no generation request");
batch.active=false;await tick();
assert.equal(upscale.disabled,false,"saved upscaled preview is viewable while stopped");
part(node,"dots").children[0].onclick();
assert.equal(upscale.disabled,true,"new preview requires a running hunt");
assert.ok(upscale.title.includes("resume mode"));
part(node,"dots").children[1].onclick();
batch.active=true;await tick();videos[0].currentTime=2.5;
batch.phase="low";batch.current=4;await tick();
assert.equal(button.disabled,false,"completed takes stay selectable while low sampling is busy");
assert.equal(button.textContent,"Use take 2 now");
await button.onclick();
assert.deepEqual(posts,[{id:batch.id,ordinal:2,ordinals:[2],created_at:123,selection_version:0}]);
assert.equal(node.properties.h3_selflift_batch,batch.id);
assert.ok(node._h3SelfLiftHunt.status.textContent.includes("Finishing and saving take 4; then upscale take 2"));
assert.equal(node.root.querySelectorAll("video")[0],videos[0],"choosing early preserves preview playback");
assert.equal(videos[0].currentTime,2.5);
batch.phase="preview";await tick();
assert.equal(button.disabled,true,"approved marks stay locked during the remaining tiny decode");
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
recovered.widgets=[];
recovered.properties=structuredClone(node.properties);
recovered.onNodeCreated();await settle();
assert.equal(part(recovered,"gate-notice").hidden,true,"old workflows without the widget default to review on");
assert.equal(recovered.properties.h3_selflift_preview.ordinal,1,"saved browsing position survives recreation without changing the approval");
recovered.onRemoved();
// Draft marks are durable, independent of browsing and shared by both tabs.
batch.selected=null;delete batch.marked;delete batch.main;delete batch.selected_ordinals;
batch.phase="waiting";batch.active=true;batch.selection_version=0;await tick();
part(node,"dots").children[0].onclick();
await part(node,"mark").onclick();
assert.deepEqual(batch.marked,[1]);assert.equal(batch.main,1);
assert.equal(batch.selected,null,"marking does not release the gate");
part(node,"dots").children[2].onclick();
await part(node,"mark").onclick();
assert.deepEqual(batch.marked,[1,3]);assert.equal(batch.main,1);
await part(node,"main").onclick();
assert.equal(batch.main,3);assert.deepEqual(batch.marked,[1,3]);
assert.equal(part(node,"mark").disabled,true,"main is always included");
assert.equal(part(second,"approve").textContent,"Finish 2 marked · main take 3");
assert.equal(part(second,"dots").children[0].dataset.marked,"true");
assert.equal(part(second,"dots").children[2].dataset.marked,"true");
part(node,"dots").children[0].onclick();
assert.equal(batch.main,3,"browsing an alternate never changes the main");
const reload=new Node(4);reload.properties.h3_selflift_batch=batch.id;reload.onNodeCreated();await settle();
assert.equal(part(reload,"approve").textContent,"Finish 2 marked · main take 3");
reload.onRemoved();
// Stale-client edits fail and refresh instead of silently overwriting marks.
batch.selection_version++;await part(node,"mark").onclick();
assert.deepEqual(batch.marked,[1,3]);
assert.ok(part(node,"status").textContent.includes("selection changed"));
await button.onclick();
assert.deepEqual(posts.pop(),{id:batch.id,ordinal:3,ordinals:[1,3],created_at:123,selection_version:4});
assert.deepEqual(batch.selected_ordinals,[1,3]);
batch.phase="awaiting_save";batch.active=false;await tick();
assert.equal(button.disabled,true,"a returned sampler is still finishing through Save");
assert.equal(part(node,"clean").disabled,true,"cannot delete low passes before all full saves");
assert.equal(part(node,"mark").disabled,true);
assert.equal(part(node,"main").disabled,true);
Object.assign(batch,{phase:"finished",active:false,selected:2,marked:[2],main:2});await tick();
batch.cleanup_error="[Errno 5] Input/output error: source.safetensors";await tick();
const cleanupWarning=part(node,"cleanup-warning");
assert.equal(cleanupWarning.hidden,false,"automatic cleanup failure is visible after polling");
assert.ok(cleanupWarning.textContent.includes("Input/output error"));
assert.ok(cleanupWarning.textContent.includes("Clean saved takes to retry"));
assert.ok(cleanupWarning.textContent.includes("Saved scene files are not affected"));
assert.equal(part(second,"cleanup-warning").textContent,cleanupWarning.textContent,"failure is shared across mounted gates");
assert.equal(clean.disabled,false,"completed batches can retry cleanup without another generation");
const warningReload=new Node(5);warningReload.properties.h3_selflift_batch=batch.id;warningReload.onNodeCreated();await settle();
assert.equal(part(warningReload,"cleanup-warning").textContent,cleanupWarning.textContent,"warning survives reopening the gate");
warningReload.onRemoved();
delete batch.cleanup_error;await tick();
assert.equal(cleanupWarning.hidden,true,"warning clears when the displayed batch has no cleanup error");
videos[0].listeners.error();
assert.equal(part(node,"media-notice").hidden,false,"failed video loading has a readable retry hint");
videos[0].error={code:4};const loadsBeforeRetry=videos[0].loads;
await node.root.querySelectorAll("button").find(b=>b.textContent==="Refresh saved takes").onclick();
assert.equal(videos[0].loads,loadsBeforeRetry+1,"refresh retries a failed media load");
videos[0].error=null;videos[0].listeners.loadeddata();
assert.equal(part(node,"media-notice").hidden,true);
batch.created_at=124;batch.selected=null;delete batch.marked;delete batch.main;await tick();
assert.ok(videos[0].src.includes("h3_hunt_created=124"),"recreated batches do not reuse cached media from a deleted hunt");
assert.equal(node.properties.h3_selflift_preview.ordinal,1);
document.hidden=true;const before=gets;await tick();assert.equal(gets,before);
document.hidden=false;
review.value=false;review.callback(false);
assert.equal(review.lastChange,false,"the gate callback preserves the original widget handler");
assert.equal(part(node,"gate-notice").hidden,false);
assert.ok(part(node,"gate-notice").textContent.includes("next queue"),"widget changes do not claim to cancel an active review");
assert.equal(posts.length,1,"changing the switch never approves, deletes or queues anything");
const savedBatch=structuredClone(batch);
Object.assign(batch,{review_enabled:false,active:true,phase:"high",selected:1,
    candidates:[{ordinal:1,seed:"42",checkpoint:"take_0001.safetensors",preview:null}]});
await tick();
assert.equal(part(node,"player").hidden,true,"automatic takes have no empty video player");
assert.equal(part(node,"candidates").hidden,false,"automatic take metadata stays available");
assert.equal(videos[0].src,undefined,"previewless takes must not request a fabricated media URL");
assert.equal(button.disabled,true);
assert.equal(button.textContent,"Automatic take — no review required");
assert.ok(node._h3SelfLiftHunt.status.textContent.includes("upscaling take 1 automatically"));
const previewlessLoads=videos[0].loads;
await tick();
assert.equal(videos[0].loads,previewlessLoads,"polling a previewless take does not load video");
batch.phase="low";await tick();
assert.ok(node._h3SelfLiftHunt.status.textContent.includes("saving one low-resolution take"));
assert.ok(!node._h3SelfLiftHunt.status.textContent.includes("skip remaining candidates"));
batch.candidates=structuredClone(savedBatch.candidates);await tick();
assert.equal(button.disabled,true,"existing previews cannot change selection during an automatic run");
await button.onclick();assert.equal(posts.length,1);
batch.active=false;await tick();
assert.equal(button.disabled,false,"saved previews can still be selected when the automatic run is stopped");
Object.assign(batch,savedBatch);delete batch.review_enabled;
review.value=true;review.callback(true);await tick();
assert.equal(part(node,"gate-notice").hidden,true);
assert.equal(part(node,"player").hidden,false,"regular review still displays saved previews");
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
review.value=false;review.callback(false);
assert.ok(node._h3SelfLiftHunt.status.textContent.includes("middle pass will still be saved"));
assert.equal(part(node,"badge").textContent,"Automatic upscale");
node.onRemoved();second.onRemoved();assert.equal(timers.size,0);
assert.equal(videos[0].src,undefined);
console.log("SelfLift hunt UI: carousel, approval, gate toggle, previewless recovery, high-pass lock, stable playback, restored view, media retry, uint64 seeds, shared polling and cleanup pass");

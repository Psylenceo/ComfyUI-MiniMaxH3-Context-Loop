// Optional real-browser smoke test. Uses synthetic media and an isolated UI,
// not the user's ComfyUI server, projects, browser profile, or generation queue.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import {spawn, spawnSync} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-review-relay-browser-"));
const profile = path.join(temporary, "chrome-profile");
const movie = path.join(temporary, "synthetic.mp4");
const generated = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
    "color=c=steelblue:s=160x90:r=24:d=3", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
    "-movflags", "+faststart", movie]);
assert.equal(generated.status, 0, generated.stderr?.toString());
const candidates = Array.from({length:10}, (_, index) => ({number:index+1,
    revision:String(index+1).padStart(32, "0"), seed:(2n**64n-BigInt(index+1)).toString(),
    scene_prompt:`Saved candidate ${index+1}. A blue test card.`, raw_frames:73,
    has_audio:false, video:{filename:`take_${index+1}.mp4`,subfolder:"synthetic",type:"output"}}));
let decisions = [], mediaRequests = 0;
const summary = {token:"browser-test",run_name:"synthetic",branch_id:"main",node_id:"14",clip_index:2,
    shot_id:"test_scene",candidate_count:10,generated_count:10,actionable:true};
const server = http.createServer(async (req,res) => {
    const url = new URL(req.url,"http://localhost");
    if (["/scripts/app.js", "/scripts/api.js"].includes(url.pathname)) {
        res.setHeader("Content-Type", "text/javascript");
        const name = path.basename(url.pathname, ".js");
        res.end(`export const ${name} = window.${name};`); return;
    }
    if (/^\/web\/[\w.-]+\.(mjs|js)$/.test(url.pathname)) {
        res.setHeader("Content-Type", "text/javascript");
        res.end(fs.readFileSync(new URL(".." + url.pathname, import.meta.url))); return;
    }
    if (url.pathname === "/view") {
        mediaRequests++;
        const data=fs.readFileSync(movie), range=req.headers.range?.match(/bytes=(\d+)-(\d*)/);
        const start=range ? Number(range[1]) : 0, end=range?.[2] ? Number(range[2]) : data.length-1;
        res.writeHead(range ? 206 : 200,{"Content-Type":"video/mp4", "Accept-Ranges":"bytes",
            "Content-Length":end-start+1,...(range ? {"Content-Range":`bytes ${start}-${end}/${data.length}`} : {})});
        res.end(data.subarray(start,end+1)); return;
    }
    if (url.pathname.endsWith("/review-relay")) {
        res.setHeader("Content-Type","application/json");
        if (req.method === "POST") {
            let body=""; for await (const part of req) body+=part;
            decisions.push(JSON.parse(body)); res.end(JSON.stringify({ok:true})); return;
        }
        const candidate = candidates.find(item=>item.revision===url.searchParams.get("candidate_revision")) ?? candidates[0];
        res.end(JSON.stringify({reviews:[summary],selected:url.searchParams.get("token")===summary.token
            ? {...summary,candidates,candidate} : null})); return;
    }
    res.setHeader("Content-Type","text/html; charset=utf-8");
    res.end(`<!doctype html><html><body style="margin:20px;background:#17191c"><script type="module">
        window.app={configuringGraph:false,registerExtension:value=>window.extension=value,
            queuePrompt:()=>{throw Error('Must never queue');}};
        window.api={fetchApi:(url,options)=>fetch(url,options),apiURL:url=>url};
        await import('/web/h3_review_relay.js');
        class TestNode {
            constructor(){this.graph={};this.size=[620,760];}
            setSize(size){this.size=size;}
            addDOMWidget(name,type,root){
                this.root=root;const host=document.createElement('div');
                host.style.cssText='width:620px;height:760px';host.append(root);document.body.append(host);return {};
            }
        }
        extension.beforeRegisterNodeDef(TestNode,{name:'MiniMaxH3ReviewRelay'});
        window.testNode=new TestNode();testNode.onNodeCreated();
    </script></body></html>`);
});
await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
let chrome, socket;
try {
    chrome=spawn(process.env.CHROME_BINARY || "google-chrome", ["--headless=new", "--no-first-run",
        "--disable-extensions", "--disable-gpu", "--disable-background-networking", "--disable-dev-shm-usage",
        "--window-size=1050,1000", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
        {stdio:["ignore","ignore","pipe"]});
    const debuggerUrl=await new Promise((resolve,reject)=>{
        const timeout=setTimeout(()=>reject(Error("Private Chrome startup timed out")),10000);
        chrome.once("error",reject);
        chrome.stderr.on("data",chunk=>{
            const match=chunk.toString().match(/DevTools listening on (ws:\/\/\S+)/);
            if(match){clearTimeout(timeout);resolve(match[1]);}
        });
    });
    socket=new WebSocket(debuggerUrl);
    await new Promise((resolve,reject)=>{socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
    let sequence=0;const pending=new Map();
    socket.addEventListener("message",event=>{
        const value=JSON.parse(event.data);const task=pending.get(value.id);
        if(task){pending.delete(value.id);value.error?task.reject(Error(JSON.stringify(value.error))):task.resolve(value.result);}
    });
    const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{
        const id=++sequence;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params,sessionId}));
    });
    const {targetId}=await send("Target.createTarget",{url:"about:blank"});
    const {sessionId}=await send("Target.attachToTarget",{targetId,flatten:true});
    const evaluate=async expression=>{
        const result=await send("Runtime.evaluate",{expression,awaitPromise:true,returnByValue:true},sessionId);
        if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));
        return result.result.value;
    };
    await send("Page.navigate",{url:`http://127.0.0.1:${server.address().port}/`},sessionId);
    const waitFor=async expression=>{
        for(let attempt=0;attempt<100;attempt++){
            if(await evaluate(`Boolean(${expression})`))return;
            await new Promise(resolve=>setTimeout(resolve,50));
        }
        throw Error(`Timed out: ${expression}`);
    };
    await waitFor("document.querySelectorAll('select')[1]?.options.length===10 && document.querySelector('video')?.readyState>=1");
    const firstRequests=mediaRequests;
    await evaluate("testNode._h3ReviewRelay.refresh()");
    assert.equal(mediaRequests,firstRequests,"refresh must not reload media");
    await evaluate("document.querySelectorAll('select')[1].value='00000000000000000000000000000005';document.querySelectorAll('select')[1].dispatchEvent(new Event('change'))");
    await waitFor("document.querySelector('textarea').value.startsWith('Saved candidate 5')");
    await waitFor("document.querySelector('video').readyState>=1 && document.querySelector('video').duration===3");
    await evaluate("document.querySelector('video').currentTime=1.25");
    await waitFor("document.querySelector('video').currentTime>1");
    await waitFor("document.querySelector('video').readyState>=2 && !document.querySelector('video').seeking");
    await evaluate("testNode._h3ReviewRelay.refresh()");
    assert.ok(await evaluate("document.querySelector('video').currentTime>1"));
    assert.ok(await evaluate("[...document.querySelectorAll('button')].filter(x=>!x.hidden).every(x=>x.getBoundingClientRect().width>0)"));
    const screenshot=await send("Page.captureScreenshot",{format:"png"},sessionId);
    fs.writeFileSync(path.join(temporary,"relay.png"),Buffer.from(screenshot.data,"base64"));
    await evaluate("[...document.querySelectorAll('button')].find(x=>x.textContent==='Approve selected').click()");
    await waitFor("document.querySelector('[role=status]').textContent.includes('original job will resume')");
    assert.equal(decisions.length,1);assert.equal(decisions[0].candidate_revision,candidates[4].revision);
    assert.equal(decisions[0].candidate_revisions.length,10);
    console.log(`Real Chrome relay: 10 takes, native video seek, stable refresh, selected approval pass. Screenshot: ${temporary}/relay.png`);
} finally {
    socket?.close();
    if(chrome && chrome.exitCode===null){chrome.kill("SIGTERM");await new Promise(resolve=>chrome.once("exit",resolve));}
    await new Promise(resolve=>server.close(resolve));
    fs.rmSync(profile,{recursive:true,force:true});
}

import assert from 'node:assert/strict';
import {checkpointClickSelection, checkpointBoxHits} from '../web/h3_checkpoint_multiselect.mjs';
const ordered = ['a','b','c','d'];
const click = (selected, anchor, key, mods) => [...checkpointClickSelection(new Set(selected), ordered, anchor, key, mods)];
assert.deepEqual(click([],null,'b',{ctrlKey:true}), ['b']);
assert.deepEqual(click(['a'],'a','c',{metaKey:true}), ['a','c']);
assert.deepEqual(click(['a','c'],'a','a',{ctrlKey:true}), ['c']);
assert.deepEqual(click(['d'],'a','c',{shiftKey:true}), ['a','b','c']);
assert.deepEqual(click(['d'],'c','a',{shiftKey:true}), ['a','b','c']);
assert.deepEqual(click(['d'],'a','b',{shiftKey:true,ctrlKey:true}), ['d','a','b']);
assert.deepEqual(click([],null,'c',{shiftKey:true}), ['c']);
const cards = [{key:'left',rect:{left:-20,right:20,top:10,bottom:30}},
    {key:'right',rect:{left:60,right:90,top:10,bottom:30}},
    {key:'offscreen',rect:{left:120,right:160,top:10,bottom:30}}];
const viewport = {left:0,right:100,top:0,bottom:100};
assert.deepEqual(checkpointBoxHits(cards,{left:0,right:200,top:0,bottom:40},viewport), ['left','right']);
assert.deepEqual(checkpointBoxHits(cards,{left:-30,right:-1,top:0,bottom:40},viewport), []);
assert.deepEqual(checkpointBoxHits(cards,{left:65,right:80,top:15,bottom:20},viewport), ['right']);
console.log('Checkpoint multi-selection: toggle, ranges, additive ranges and clipped rectangle hits pass');

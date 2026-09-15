import assert from "node:assert/strict";
import * as chapters from "../web/h3_studio_chapters.mjs";
import * as core from "../web/h3_chain_plan_studio_core.mjs";

const rows = Array.from({length:6}, (_, index) => ({id:`s${index + 1}`, deliveredFrames:240}));
const markers = [{id:"a", title:"Opening", start_scene_id:"s1"},
    {id:"b", title:"Second", start_scene_id:"s4"}];
const trims = [{scene_id:"s4", in_frame:24, out_frame:144}];
const placements = [{scene_id:"s6", start_frame:1440}];
const layout = core.studioTimelineLayout(rows, 1200, 2, placements, 3000, trims);
const original = JSON.stringify({rows, markers, trims, placements, layout});
const groups = chapters.studioChapterGroups(markers, rows, layout.segments);
assert.equal(groups[0].durationSeconds, 30);
assert.equal(groups[1].durationSeconds, 40, "chapter includes its internal black gap, not trailing workspace");
assert.equal(groups[1].sceneCount, 3);
const entries = chapters.studioChapterEntries(layout.segments, groups, ["a"]);
const folded = chapters.studioChapterLayout(layout, entries);
assert.equal(folded.entries[0].width, 160);
assert.equal(folded.entries[1].key, "scene:3");
assert.equal(folded.entries[1].left, 160);
for (let frame = 0; frame <= 3000; frame++) {
    const seconds = frame / 24;
    assert.ok(Math.abs(chapters.studioChapterSecond(folded.entries,
        chapters.studioChapterPixel(folded.entries, seconds)) - seconds) < 1e-8,
    `global ruler and playhead round trip at frame ${frame}`);
}
const expanded = chapters.studioChapterLayout(layout,
    chapters.studioChapterEntries(layout.segments, groups, []));
assert.ok(Math.abs(expanded.contentWidth - layout.contentWidth) < 1e-8);
expanded.entries.forEach((entry, index) => assert.equal(entry.width, layout.widths[index]));
const model = chapters.studioChapterPlayback({...layout, result:{shots:rows}}, groups[1]);
assert.equal(model.startSeconds, 30);
assert.equal(model.durationSeconds, 40);
assert.equal(model.totalSeconds, 70);
assert.equal(chapters.studioChapterGlobalSecond(model, 0), 30);
assert.equal(chapters.studioChapterGlobalSecond(model, 40), 70);
assert.equal(chapters.studioChapterLocalSecond(model, 42), 12);
const gap = core.locateStudioTimelineSegment(model.segments,
    chapters.studioChapterGlobalSecond(model, 20));
assert.equal(gap.kind, "gap");
assert.equal(core.studioPlayerSegmentClock(model.segments, "scene:3", 1).timelineSeconds, 30,
    "trimmed/slipped source in is preserved for the chapter-local player");
assert.equal(core.studioPlayerSegmentClock(model.segments, "scene:3", 6).boundaryReached, true);
assert.equal(JSON.stringify({rows, markers, trims, placements, layout}), original,
    "fold/playback helpers never mutate generation, editorial or source data");

const key = chapters.studioChapterViewKey("test", "main");
const saved = JSON.parse(JSON.stringify({[key]:{collapsed:["a", "b", "a", "deleted"], focused:"b"}}));
assert.deepEqual(chapters.studioChapterView(saved, key, markers), {collapsed:["a", "b"], focused:"b"});
assert.deepEqual(chapters.studioChapterView(saved, chapters.studioChapterViewKey("other", "main"), markers),
    {collapsed:[], focused:""});
assert.deepEqual(chapters.studioChapterView(saved, chapters.studioChapterViewKey("test", "new-branch"), markers),
    {collapsed:[], focused:""});
assert.deepEqual(chapters.studioChapterView(saved, key, []), {collapsed:[], focused:""});
assert.deepEqual(chapters.studioChapterGroups([], rows, layout.segments), []);

// Reordering scenes across chapters must neither hide foreign scenes inside a
// folded card nor play them when focusing that chapter.
const reordered = core.studioTimelineLayout(rows, 800, 1, [{scene_id:"s2", start_frame:1200}]);
const movedGroups = chapters.studioChapterGroups(markers, rows, reordered.segments);
const moved = chapters.studioChapterEntries(reordered.segments, movedGroups, ["a", "b"]);
assert.deepEqual(moved.map(entry => entry.chapter?.id), ["a", "b", "a", "b"]);
const movedPlayback = chapters.studioChapterPlayback(reordered, movedGroups[0]);
assert.equal(movedPlayback.durationSeconds, 40, "retains the internal gap left by the moved scene");
assert.equal(chapters.studioChapterGlobalSecond(movedPlayback, 30), 50);
assert.equal(chapters.studioChapterLocalSecond(movedPlayback, 50), 30);
assert.equal(core.locateStudioTimelineSegment(movedPlayback.segments,
    chapters.studioChapterGlobalSecond(movedPlayback, 31)).sceneId, "s2");
console.log("Studio chapters: collapse mapping, reload, branch isolation, trim/slip, gaps and moved scenes pass");

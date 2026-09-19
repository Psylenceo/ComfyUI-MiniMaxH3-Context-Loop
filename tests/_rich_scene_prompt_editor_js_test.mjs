#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import {
    PromptUndoHistory,
    RICH_PROMPT_GUIDES,
    normalizeRichGuide,
    optimizerSource,
    promptUndoDirection,
    richGenerationMode,
    richGuideInstruction,
    tokenizeRichPrompt,
} from "../web/h3_rich_prompt_editor_core.mjs";

const undo = new PromptUndoHistory("A", {coalesceMs:750});
assert.equal(undo.record("AB", {inputType:"insertText", now:100}), true);
assert.equal(undo.record("ABC", {inputType:"insertText", now:200}), true);
assert.equal(undo.undo(), "A");
assert.equal(undo.redo(), "ABC");
undo.record("ABC pasted", {inputType:"insertFromPaste", now:250});
assert.equal(undo.undo(), "ABC");
undo.record("AB changed", {inputType:"insertReplacementText", now:300});
assert.equal(undo.redo(), null);
assert.equal(undo.align("External"), true);
assert.equal(undo.undo(), null);
assert.equal(promptUndoDirection({ctrlKey:true, key:"z"}), "undo");
assert.equal(promptUndoDirection({metaKey:true, shiftKey:true, key:"Z"}), "redo");
assert.equal(promptUndoDirection({ctrlKey:true, key:"y"}), "redo");
assert.equal(promptUndoDirection({ctrlKey:true, altKey:true, key:"z"}), null);
const synchronizedUndo = new PromptUndoHistory("Before");
synchronizedUndo.record("After", {inputType:"insertReplacementText", now:400});
assert.equal(synchronizedUndo.align("After"), false);
assert.equal(synchronizedUndo.undo(), "Before");

const records = [
    {kind:"picture", token:"@hero", label:"<Picture 1>", active:true},
    {kind:"audio", token:"@voice", label:"<Audio 1>", active:true},
];
const tokens = tokenizeRichPrompt(
    "<Subject 1> uses @hero and <Audio 1>. <d>Hello.</d> @missing",
    records,
);
assert.deepEqual(
    tokens.filter((item) => item.type !== "text").map((item) => [item.type, item.kind, item.text, item.unresolved]),
    [
        ["subject", undefined, "<Subject 1>", undefined],
        ["reference", "picture", "@hero", false],
        ["reference", "audio", "<Audio 1>", false],
        ["dialogue", undefined, "<d>", undefined],
        ["dialogue", undefined, "</d>", undefined],
        ["reference", "unknown", "@missing", true],
    ],
);
const punctuated = tokenizeRichPrompt(
    "Keep @hero. Then use @voice, without absorbing punctuation.",
    records,
);
assert.deepEqual(
    punctuated.filter((item) => item.type !== "text").map((item) => item.text),
    ["@hero", "@voice"],
);
assert.equal(punctuated.map((item) => item.text).join(""),
    "Keep @hero. Then use @voice, without absorbing punctuation.");
const caseSensitiveRecords = [
    {kind:"picture", tag:"Maison", token:"@Maison", active:true},
    {kind:"picture", tag:"maison", token:"@maison", active:true},
];
const caseSensitiveTokens = tokenizeRichPrompt(
    "Use @Maison and @maison, but not @MAISON. Keep #Maison distinct from #maison.",
    caseSensitiveRecords,
).filter((item) => item.type === "reference");
assert.equal(caseSensitiveTokens[0].record, caseSensitiveRecords[0]);
assert.equal(caseSensitiveTokens[1].record, caseSensitiveRecords[1]);
assert.equal(caseSensitiveTokens[2].record, null);
assert.equal(caseSensitiveTokens[2].unresolved, true);
assert.equal(caseSensitiveTokens[3].record, caseSensitiveRecords[0]);
assert.equal(caseSensitiveTokens[4].record, caseSensitiveRecords[1]);
const schemaTokens = tokenizeRichPrompt(
    "summary:\n[reference generation]\n<d>[English] (S1) Continue <scenetrans> now.<cutoff></d>",
    records,
).filter((item) => item.type !== "text");
assert.deepEqual(schemaTokens.map((item) => [item.type, item.text]), [
    ["section", "summary:"],
    ["dialogue", "<d>"],
    ["speaker", "(S1)"],
    ["flow", "<scenetrans>"],
    ["flow", "<cutoff>"],
    ["dialogue", "</d>"],
]);
assert.equal(richGenerationMode("scheduled"), "Ref2VA");
assert.equal(richGenerationMode("tagged"), "Ref2VA");
assert.equal(richGenerationMode("native_keyframes"), "I2VA/FL2VA");
assert.equal(normalizeRichGuide("bogus"), "auto");
assert.ok(RICH_PROMPT_GUIDES.some((item) => item.id === "music_video"));
const guidedRefRewrite = richGuideInstruction("music_video", "Ref2VA");
assert.match(guidedRefRewrite, /subject_definitions/);
assert.match(guidedRefRewrite, /lyrics/);
assert.match(guidedRefRewrite, /only when the user explicitly asks for a full H3 rewrite/);
assert.match(guidedRefRewrite, /change only what the request requires/);
assert.match(guidedRefRewrite, /Connected references prove only/);
assert.match(guidedRefRewrite, /Do not invent image content, motion, lyrics, voice, timbre/);
assert.match(guidedRefRewrite, /inside the supplied scene duration/);
const compactRewrite = richGuideInstruction("general", "I2VA/FL2VA");
assert.match(compactRewrite, /keyframe-alignment sentence only when/);
assert.match(compactRewrite, /do not force headings onto a compact prompt/);
assert.equal(optimizerSource("AI result", {source:"Original", result:"AI result"}), "Original");
assert.equal(optimizerSource("Manual edit", {source:"Original", result:"AI result"}), "Manual edit");

const source = fs.readFileSync(
    new URL("../web/h3_chain_rich_scene_prompt_editor.js", import.meta.url),
    "utf8",
);
assert.match(source, /MiniMaxH3ChainRichScenePromptEditor/);
assert.match(source, /edits only the selected scene prompt/i);
assert.match(source, /contentEditable = "true"/);
assert.match(source, /"keydown", "keyup", "keypress", "copy", "cut", "paste"/);
assert.match(source, /stopPropagation deliberately preserves the browser's default/);
assert.match(source, /selectedEditorPlainText/);
assert.match(source, /data-token value/);
assert.match(source, /setData\("text\/plain", text\)/);
assert.match(source, /editor\.addEventListener\("copy"/);
assert.match(source, /editor\.addEventListener\("cut"/);
assert.match(source, /shared text-level undo survives later tag decoration/);
assert.match(source, /PromptUndoHistory/);
assert.match(source, /promptUndoDirection/);
assert.match(source, /applyPromptUndo/);
assert.match(source, /activeSceneIndexAfterRefresh/);
assert.match(source, /ownsPromptHistoryTarget/);
assert.match(source, /stopImmediatePropagation\(\)/);
assert.match(source, /recordPromptReplacement/);
assert.match(source, /promptUndoForScene\(shotId, mergedText, \{external:true\}\)/);
assert.match(source, /tokenizeRichPrompt/);
assert.match(source, /h3rp-token-picture/);
assert.match(source, /h3rp-token-audio/);
assert.match(source, /h3rp-token-thumb/);
assert.match(source, /function referenceMediaPreview\(record, kind\)/);
assert.match(source, /url: api\.apiURL\(record\.previewUrl\)/);
assert.doesNotMatch(source, /record\.previewUrl\s*\?\s*api\.apiURL/);
assert.match(source, /--h3rp-accent:color-mix\(in srgb,var\(--h3rp-text\)/);
assert.match(source, /--h3rp-token-subject:color-mix/);
assert.match(source, /color:var\(--h3rp-token-subject\)/);
assert.doesNotMatch(source, /h3rp-token-subject \{ color:#8ed7a4/);
assert.match(source, /h3rp-popover audio/);
assert.match(source, /mediaElement\.controls = true/);
assert.match(source, /Audio never autoplays/);
assert.match(source, /pointerdown.*preventDefault/);
assert.match(source, /createPromptCompletionController/);
assert.match(source, /createH3PromptSchemaController/);
assert.match(source, /h3_rich_scene_prompt_editor/);
assert.match(source, /getMode:\(\) => state\.schema/);
assert.match(source, /h3rp-token-section/);
assert.match(source, /h3rp-token-flow/);
assert.match(source, /h3rp-token-speaker/);
assert.match(source, /PRESENTATION_PROPERTY/);
assert.match(source, /state\.decorated \? "Rich text" : "Plain text"/);
assert.match(source, /state\.completion\?\.handleKeydown/);
assert.match(source, /restoreTextSelection/);
assert.match(source, /editingSemanticTime/);
assert.match(source, /convertTaggedPictureReference/);
assert.match(source, /replacePromptReferenceOccurrence/);
assert.match(source, /click to replace or edit/);
assert.match(source, /Time \(sec\)/);
assert.match(source, /makeToken\(part, partStart, partEnd\)/);
assert.match(source, /popoverPinned/);
assert.match(source, /taggedPictureReferenceMode/);
assert.match(source, /h3rp-ref-mode/);
assert.match(source, /Use untimed Qwen-only #tag/);
assert.match(source, /refsButton\.addEventListener\("pointerdown", rememberPromptSelection\)/);
assert.match(source, /document\.activeElement === state\.editor/);
assert.match(source, /current\.slice\(0, insertionStart\)/);
assert.match(source, /if \(record\.semanticOnly\) return "semantic"/);
assert.match(source, /maxItems:40/);
assert.match(source, /Type @, #, <, or \[/);
assert.match(source, /Ctrl\/Cmd\+Space for all H3 completions/);
assert.doesNotMatch(source, /event\.key === "@"/);
assert.match(source, /RICH_PROMPT_GUIDES/);
assert.match(source, /richGuideInstruction/);
assert.match(source, /PromptAssistantClient/);
assert.match(source, /prompt_assist_ready/);
assert.match(source, /optimizerProviders/);
assert.match(source, /promptOptimizerBackend/);
assert.match(source, /makeDirectPromptOptimizeRequest/);
assert.match(source, /openPromptOptimizerSettings/);
assert.doesNotMatch(source, /h3rp-provider/);
assert.match(source, /rebaseActivePromptOntoLivePlan/);
assert.match(source, /publishCompanionScene/);
assert.doesNotMatch(source, /state\.provider === "hermes"/);
assert.match(source, /Optimize/);
assert.match(source, /Apply changed result/);
assert.match(source, /scheduleHistoryDraft/);
assert.match(source, /flushHistoryDraft/);
assert.match(source, /node\._h3FlushProjectWrites/);
assert.match(source, /promptRevisionTree/);
assert.match(source, /mutateHistoryRevision\(\s*"label"/);
assert.match(source, /mutateHistoryRevision\(\s*"archive"/);
assert.match(source, /mutateHistoryRevision\(\s*"delete"/);
assert.match(source, /parent → child progression/);
assert.match(source, /Delete unexecuted leaf/);
assert.match(source, /serialize:false/);
assert.match(source, /PROJECT_ASSET_CATALOG_CHANGED_EVENT/);
assert.match(source, /function refreshReferenceState/);
assert.match(source, /refreshReferenceState\(editorPlainText\(state\.editor\)\)/);
assert.match(source, /onProjectAssetCatalogChanged/);
assert.match(source, /removeEventListener\?\.\(\s*PROJECT_ASSET_CATALOG_CHANGED_EVENT/);
assert.match(source, /const scrollTop = state\.editor\.scrollTop/);
assert.match(source, /state\.editor\.scrollTop = scrollTop/);
assert.match(source, /editor\.focus\(\{preventScroll:true\}\)/);
assert.match(source, /const PROMPT_SYNC_DELAY_MS = 140/);
assert.match(source, /const PROMPT_ANALYSIS_DELAY_MS = 90/);
assert.match(source, /writePlan\("Saved to connected Plan", \{deferEffects:true\}\)/);
assert.match(source, /function flushPlanEffects\(\)/);
assert.match(source, /state\.planWidget\.value = value/);
assert.match(source, /window\.setTimeout\(\s*flushPlanEffects, PROMPT_SYNC_DELAY_MS/);
assert.match(source, /liveValue !== state\.lastValue && !rebaseActivePromptOntoLivePlan\(\)/);
assert.match(source, /editor\.addEventListener\("blur", \(\) => \{\s*flushPlanEffects\(\)/);
assert.match(source, /schedulePromptAnalysis\(\)/);
const teardown = source.slice(
    source.indexOf("node.onRemoved = function ()"),
    source.indexOf("node._h3PromptCompanionSetActiveScene"),
);
assert.match(teardown, /state\.disposed = true/);
assert.match(teardown, /state\.planSyncPending = null/);
assert.match(teardown, /state\.history\.pendingDraft = null/);
assert.doesNotMatch(teardown, /flushPlanEffects\(\)/);
assert.doesNotMatch(teardown, /flushPromptAnalysis\(\)/);
assert.doesNotMatch(teardown, /flushHistoryDraft\(\)/);

// A tag can be introduced in the basic prompt before Optimize ever turns it
// into H3 text. Reference scanning for the optimizer request must include
// the shot's basic_prompt, or that tag is reported inactive and its media
// is left out of the Direct API resources list even though it belongs.
const optimizePromptSource = source.slice(
    source.indexOf("async function optimizePrompt()"),
    source.indexOf("function stopOptimizer()"),
);
assert.match(optimizePromptSource,
    /const refs = availableReferenceRecords\(node, sceneIndex \+ 1, \{[\s\S]{0,600}shot\.basic_prompt/,
    "optimizer reference scanning must include the shot's basic_prompt, " +
    "not just the shared/H3 prompt text, or a tag used only in the basic " +
    "draft is excluded from the optimizer's active media list");

// Applying an optimizer result (Direct API, MCP, or a previously pending
// result) must refresh the reference tray/highlighting against the new
// text before redrawing, or a newly active tag stays shown "inactive"
// until an unrelated manual edit happens to trigger a refresh. This refresh
// must NOT include basic_prompt: it reflects the compiled H3 prompt that
// generation actually uses, so a draft-only tag omitted from the rewrite
// must show inactive, and a tag's <Picture N> numbering must match the
// resulting H3 text alone - unlike optimizePrompt()'s own reference scan
// above, which deliberately does include basic_prompt for the separate
// purpose of preparing the outgoing request.
assert.match(source, /function refreshTaggedReferencesForShot\(text\)/,
    "a shared helper must refresh reference records for the resulting H3 " +
    "text so all optimizer-apply sites stay consistent");
assert.match(source,
    /prompt:\[sharedPrompt\(state\.plan\)\.text\.trim\(\), String\(text \?\? ""\)\.trim\(\)\]\s*\n\s*\.filter\(Boolean\)\.join\("\\n\\n"\),\s*\n\s*\},\s*\n\s*\);\s*\n\s*state\.records = refreshed\.records;/,
    "the post-apply reference refresh must scan only the shared prompt " +
    "plus the resulting H3 text, not basic_prompt - otherwise a tag only " +
    "in the basic draft (and omitted from the optimized result) keeps " +
    "showing active/mismapped even though generation will not see it");
for (const fnName of [
    "handleOptimizerFrame", "applyPendingOptimizerResult", "applyOptimizerResponse",
]) {
    const start = source.indexOf(`function ${fnName}(`);
    assert.notEqual(start, -1, `${fnName} must exist`);
    const end = source.indexOf("\n    }\n\n", start);
    const body = source.slice(start, end === -1 ? undefined : end);
    assert.match(body,
        /refreshTaggedReferencesForShot\([\s\S]{0,80}renderEditorText/,
        `${fnName} must refresh tagged references before redrawing the ` +
        "applied optimizer result, covering Direct API, MCP, and " +
        "pending-result application alike");
}

console.log("H3 Rich Scene Prompt Editor: tokens, guides, previews, optimizer, and history pass");

"""New file-backed workflow wiring, independent fallback and layout contract."""
import json
from pathlib import Path
from _workflow_schema_unit_test import load_schemas, validate_workflow

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / 'example_workflows/Deferred Upscale - Pixel USDU Continuity - EXPERIMENTAL - MiniMax H3 0.6.json'
wf = json.loads(path.read_text())
validate_workflow(wf, load_schemas())
nodes = {n['type']:n for n in wf['nodes']}
by_id = {n['id']:n for n in wf['nodes']}
links = {link[0]:link for link in wf['links']}

def origin(target, name):
    socket = next(s for s in nodes[target]['inputs'] if s['name'] == name)
    if socket['link'] is None: return None
    _, source, slot, target_id, target_slot, _ = links[socket['link']]
    assert target_id == nodes[target]['id']
    assert nodes[target]['inputs'][target_slot] == socket
    return by_id[source]['type'], by_id[source]['outputs'][slot]['name']

current = 'CATH3UpscaleVideoCurrent'
protect = 'MiniMaxH3PixelContinuityPrepare'
finish = 'MiniMaxH3PixelContinuityFinish'
conditioning = 'CATH3UpscaleVideoConditioning'
refine = 'UltimateSDUpscaleNoUpscaleGuiderVideo'
assert nodes['MiniMaxH3ChainCheckpointManager']['widgets_values'] == ['']
capture = 'MiniMaxH3PixelBoundaryCapture'
options = 'MiniMaxH3PixelBoundarySettings'
export = 'MiniMaxH3PixelBoundaryExport'
assert origin(protect,'video') == (capture,'video')
assert origin(capture,'video') == ('CATDLSS5EnhanceVideo','video')
assert origin(capture,'state') == (current,'state')
assert origin(capture,'options') == (options,'options')
assert nodes[options]['widgets_values'][:4] == [False,17,0.2,3]
assert origin('MiniMaxH3ChainUpscaleAdapter','recipe_json') == (options,'recipe_json')
assert origin(export,'video_path') == ('MiniMaxH3ChainAssemble','video_path')
assert origin(export,'manifest') == ('CATH3UpscaleVideoLoopEnd','manifest')
assert origin(export,'options') == (options,'options')
assert origin(export,'model') == ('LoraLoaderModelOnly','MODEL')
assert origin(export,'prompt_override') == ('MiniMaxH3UpscaleReferencePromptOverride','prompt_override')
assert origin(export,'tagged_references') == ('MiniMaxH3UpscaleReferencePromptOverride','references')
assert origin(protect,'state') == (current,'state')
assert origin(conditioning,'video') == (protect,'video')
assert origin(refine,'video') == (conditioning,'video')
assert origin(refine,'mask') == (protect,'mask')
assert origin(refine,'anchor_context') == (protect,'anchor_context')
assert origin(refine,'seed') == (current,'seed')
assert origin(finish,'video') == (refine,'video')
assert origin(finish,'continuity') == (protect,'continuity')
assert nodes[refine]['widgets_values'][-2:] == ['disk','']
assert nodes[finish]['widgets_values'] == [1,39]
for target in ('CATH3UpscaleVideoSegmentSave','CATH3UpscaleVideoLoopEnd'):
    assert origin(target,'video') == (finish,'video')
    assert origin(target,'state') == (current,'state')
    assert origin(target,'upscaled_latent') is None
assert origin('CATH3UpscaleVideoSegmentSave','recovered_audio') is None
assert origin('MiniMaxH3ChainAssemble','manifest') == ('CATH3UpscaleVideoLoopEnd','manifest')
assert origin('CATH3UpscaleVideoLoopEnd','flow') == ('MiniMaxH3ChainUpscaleAdapter','flow')
assert not any('ContextAnchoredVideoRefine' in typ for typ in nodes), 'transport only; no CAT refinement'
for i,a in enumerate(wf['nodes']):
    ax,ay=a['pos']; aw,ah=a['size']
    for b in wf['nodes'][i+1:]:
        bx,by=b['pos']; bw,bh=b['size']
        assert not (ax < bx+bw and bx < ax+aw and ay-30 < by+bh and by-30 < ay+ah), (a['type'],b['type'])
print('Pixel continuity workflow: full RAW VIDEO path, mark-driven mask/anchor, original audio, disk canvas and non-overlapping layout pass')

"""Disposable generation checkpoints only; never opens a user project."""
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bulk_helpers', Path(__file__).with_name('_checkpoint_revision_unit_test.py'))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
bulk = importlib.import_module(h.PACKAGE + '.checkpoint_bulk_delete')


def snapshot(run):
    return {str(p.relative_to(run)):p.read_bytes() for p in run.rglob('*') if p.is_file() and not p.name.endswith('.lock')}


async def check():
    with tempfile.TemporaryDirectory() as directory:
        h.folder_paths.output_directory = directory
        root = Path(directory)
        run = root / 'h3_chains' / 'bulk'
        manager = bulk.BulkCheckpointManager(root)
        revisions, previous = [], None
        for scene in range(1,5):
            token = str(scene) * 32
            previous, _ = h.write_revision(run, scene, token, scene, predecessor=previous,
                active=True, run_name=run.name)
            revisions.append({'scene':scene,'revision':token})
        before = snapshot(run)
        with patch.object(manager.graph, '_scan', wraps=manager.graph._scan) as scan:
            preview = manager.preview(run.name, revisions[1:])
            assert scan.call_count == 1
        assert preview['allowed'], preview['blockers']
        assert preview['rollback_scenes'] == [2,3,4]
        assert snapshot(run) == before, 'preview changed files'
        assert not manager.preview(run.name, revisions[1:2])['allowed'], 'unselected continuations must block'
        assert len(manager.preview(run.name, revisions[1:] + revisions[1:])['revisions']) == 3
        for bad in (None, [], [{}], [{'scene':True,'revision':'1'*32}], [{'scene':1,'revision':'../escape'}]):
            try:
                manager.preview(run.name, bad)
            except (ValueError, FileNotFoundError):
                pass
            else:
                raise AssertionError('invalid selection accepted')

        async def route(action, body):
            request = h.JsonRequest(body)
            request.method, request.path = 'POST', '/minimax_h3_context_loop/checkpoint-revisions/bulk-' + action
            async def inline(function, *args):
                return function(*args)
            with patch.object(h.chain.asyncio, 'to_thread', side_effect=inline):
                return await h.chain._bulk_checkpoint_deletion(request)

        body = {'run_name':run.name, 'branch_id':'main', 'revisions':revisions[1:]}
        assert (await route('preview', body)).status == 200
        assert (await route('preview', [])).status == 400
        assert (await route('preview', dict(body, run_name='../escape'))).status == 400
        assert (await route('delete', body)).status == 409
        assert (await route('delete', dict(body, snapshot='stale'))).status == 409
        with patch.object(h.chain, '_project_write_rejection', return_value=h.chain.web.json_response({'error':'read only'},status=423)):
            assert (await route('delete', dict(body, snapshot=preview['snapshot']))).status == 423
        assert snapshot(run) == before

        # Retained named-branch assignments still veto the whole transaction.
        pointer = run / 'branches' / ('a'*32) / 'checkpoints' / 'clip_0002.json'
        pointer.parent.mkdir(parents=True)
        pointer.write_bytes((run / 'checkpoints' / 'clip_0002.json').read_bytes())
        assert any('Working branch' in b for b in manager.preview(run.name, revisions[1:])['blockers'])
        pointer.unlink()
        sealed = run / 'chapters' / '01_one' / 'manifests' / ('f'*32 + '.json')
        sealed.parent.mkdir(parents=True)
        sealed.write_text(json.dumps({'format':'h3_chain_chapter_manifest_v1', 'run_name':run.name,
            'chapter':{'number':1,'title':'One'}, 'segments':[json.loads((run / 'checkpoints' / 'clip_0002.json').read_text())['segment']]}))
        assert any('Sealed Chapter' in b for b in manager.preview(run.name, revisions[1:])['blockers'])
        sealed.unlink()
        metadata = run / 'checkpoints' / ('clip_0002.' + '2'*32 + '.json')
        content = metadata.read_bytes()
        metadata.write_bytes(content + b'\n')
        assert (await route('delete', dict(body, snapshot=preview['snapshot']))).status == 409
        metadata.write_bytes(content)
        preview = manager.preview(run.name, revisions[1:])
        before = snapshot(run)
        replace = bulk.os.replace
        count = 0
        def fail_second(source,destination):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('synthetic staging failure')
            return replace(source,destination)
        with patch.object(bulk.os, 'replace', side_effect=fail_second):
            try:
                manager.delete(run.name, revisions[1:], preview['snapshot'])
            except OSError:
                pass
            else:
                raise AssertionError('staging failure not raised')
        assert snapshot(run) == before, 'staging failure did not roll back all files'
        response = await route('delete', dict(body, snapshot=preview['snapshot']))
        assert response.status == 200, response.text
        result = json.loads(response.text)
        assert len(result['deleted_revisions']) == 3 and result['cleanup_pending'] == 0
        expected = {str((root / f['path']).relative_to(run)) for f in preview['files'] if f['owned'] and f['exists']}
        after = snapshot(run)
        assert set(before) - set(after) == expected
        assert all(before[path] == data for path,data in after.items())
        assert (run / 'checkpoints' / 'clip_0001.json').exists()
        assert all(not (run / 'checkpoints' / f'clip_{i:04}.json').exists() for i in (2,3,4))
        print('Bulk checkpoint deletion: exact set, one scan, dependencies, active tail, branch pins, stale preview, ownership and staging rollback pass')

        # Shared reattached media remains readable after removing old links.
        reuse = root / 'h3_chains' / 'reuse'
        parent, _ = h.write_revision(reuse, 1, 'a'*32, 1, run_name=reuse.name)
        h.write_revision(reuse, 1, 'b'*32, 2, run_name=reuse.name, active=True)
        child, _ = h.write_revision(reuse, 2, 'c'*32, 3, predecessor=parent,
            run_name=reuse.name, context_length=0, audio_context_length=0, generated_continuity='off')
        attached = manager.graph.attribute(reuse.name, 1, 'b'*32, 2, 'c'*32)
        targets = [{'scene':1,'revision':'a'*32},{'scene':2,'revision':'c'*32}]
        keep = [root / child['segment'][field] for field in ('segment','checkpoint','prompt_file')]
        keep.append(reuse / 'checkpoints' / ('clip_0002.' + attached['revision'] + '.json'))
        saved = {p:p.read_bytes() for p in keep}

        # Test the same deletion against an organized duplicate too. Source
        # fixtures stay byte-for-byte untouched by conversion and copy deletion.
        converter = importlib.import_module(h.PACKAGE + '.chain_layout_conversion')
        layout = importlib.import_module(h.PACKAGE + '.chain_layout')
        copy_root = root / 'converted'
        converter.convert_copy(reuse, copy_root)
        copy_manager = bulk.BulkCheckpointManager(copy_root)
        copy_preview = copy_manager.preview(reuse.name, targets)
        assert copy_preview['allowed'], copy_preview['blockers']
        assert any('/.h3/checkpoints/' in part['path'] for part in copy_preview['files'])
        copy_manager.delete(reuse.name, targets, copy_preview['snapshot'])
        assert all(p.read_bytes() == data for p,data in saved.items())
        for p,data in saved.items():
            historical = p.relative_to(root)
            assert Path(layout.output_path(copy_root, str(historical))).read_bytes() == data

        shared_preview = manager.preview(reuse.name, targets)
        assert shared_preview['allowed'], shared_preview['blockers']
        assert any(part['shared'] and not part['owned'] for part in shared_preview['files'])
        manager.delete(reuse.name, targets, shared_preview['snapshot'])
        assert all(p.read_bytes() == data for p,data in saved.items())
        assert (2,attached['revision']) in manager.graph._scan(reuse.name)['records']
        print('Bulk checkpoint deletion: sealed chapters, retained aliases/shared media, legacy and organized duplicate storage pass')

        # An ALT selected in this branch's cut can be removed in the same
        # confirmed transaction, without editing another branch or the Plan.
        cut = root / 'h3_chains' / 'cut'
        base, _ = h.write_revision(cut, 1, 'a'*32, 1, active=True, run_name=cut.name)
        alt, _ = h.write_revision(cut, 1, 'b'*32, 2, run_name=cut.name)
        alt['segment'].update(take_kind='editorial_alternate', alternate_of_revision='a'*32)
        (cut / 'checkpoints' / ('clip_0001.' + 'b'*32 + '.json')).write_text(json.dumps(alt))
        replacement = {'scene':1,'base_revision':'a'*32,'alternate_revision':'b'*32}
        unrelated = {'scene':2,'base_revision':'c'*32,'alternate_revision':'d'*32}
        editorial_path = cut / 'editorial.json'
        editorial = {'run_name':cut.name, 'replacements':[replacement, unrelated], 'timeline':{'keep':'exact'}}
        editorial_path.write_text(json.dumps(editorial))
        targets = [{'scene':1,'revision':'b'*32}]
        before = snapshot(cut)
        assert manager.graph.deletion_preview(cut.name, 1, 'b'*32)['final_cut_selection']
        plan = manager.preview(cut.name, targets)
        assert plan['allowed'], plan['blockers']
        assert plan['editorial_releases'] == [replacement]
        assert snapshot(cut) == before, 'editorial preview mutated the cut'
        base_only = manager.preview(cut.name, [{'scene':1,'revision':'a'*32}])
        assert not base_only['allowed'], 'an unselected ALT still protects its base'
        both = targets + [{'scene':1,'revision':'a'*32}]
        assert manager.preview(cut.name, both)['allowed'], 'base and ALT must be deletable together'

        # Changing even an unrelated cut setting invalidates the confirmation.
        editorial_path.write_text(json.dumps(dict(editorial, timeline={'keep':'changed'})))
        try:
            manager.delete(cut.name, targets, plan['snapshot'])
        except bulk.CheckpointDeleteBlocked:
            pass
        else:
            raise AssertionError('a stale final-cut preview was accepted')
        editorial_path.write_bytes(before['editorial.json'])
        other_cut = cut / 'branches' / ('e'*32) / 'editorial.json'
        other_cut.parent.mkdir(parents=True)
        other_cut.write_bytes(editorial_path.read_bytes())
        assert not manager.preview(cut.name, targets)['allowed'], 'another branch still uses this ALT'
        other_cut.write_text(json.dumps({'replacements':[unrelated]}))
        before = snapshot(cut)
        plan = manager.preview(cut.name, targets)
        with patch.object(manager.graph, '_atomic_json', side_effect=OSError('synthetic cut write failure')):
            try:
                manager.delete(cut.name, targets, plan['snapshot'])
            except OSError:
                pass
            else:
                raise AssertionError('cut write failure was not raised')
        assert snapshot(cut) == before, 'cut write failure must restore media and editorial bytes'
        manager.delete(cut.name, targets, plan['snapshot'])
        assert json.loads(editorial_path.read_text()) == dict(editorial, replacements=[unrelated])
        assert other_cut.read_bytes() == before[str(other_cut.relative_to(cut))]
        assert (cut / 'checkpoints' / 'clip_0001.json').exists(), 'deleting ALT must retain original assignment'
        assert manager.graph._scan(cut.name)['records'][(1,'a'*32)]['active']

        # Repeat under a named branch: Original's cut stays byte-for-byte
        # unchanged, even when it references a different ALT in the same scene.
        alt, _ = h.write_revision(cut, 1, 'b'*32, 2, run_name=cut.name)
        alt['segment'].update(take_kind='editorial_alternate', alternate_of_revision='a'*32)
        (cut / 'checkpoints' / ('clip_0001.' + 'b'*32 + '.json')).write_text(json.dumps(alt))
        branch = cut / 'branches' / ('f'*32)
        (branch / 'checkpoints').mkdir(parents=True)
        (branch / 'branch.json').write_text(json.dumps({'id':'f'*32,'name':'Obsolete'}))
        (branch / 'checkpoints' / 'clip_0001.json').write_text(json.dumps(base))
        (branch / 'editorial.json').write_text(json.dumps(editorial))
        scope = importlib.import_module(h.PACKAGE + '.branch_scope')
        before = snapshot(cut)
        with scope.branch_scope(cut.name, 'f'*32):
            plan = manager.preview(cut.name, targets)
            assert plan['allowed'], plan['blockers']
            manager.delete(cut.name, targets, plan['snapshot'])
        assert editorial_path.read_bytes() == before['editorial.json']
        assert json.loads((branch / 'editorial.json').read_text()) == dict(editorial, replacements=[unrelated])
        for path, data in before.items():
            if path.startswith('branches/' + 'e'*32):
                assert (cut / path).read_bytes() == data
        print('Bulk final-cut deletion: exact releases, base/ALT selection, other branches, stale preview and atomic rollback pass')


if __name__ == '__main__':
    asyncio.run(check())

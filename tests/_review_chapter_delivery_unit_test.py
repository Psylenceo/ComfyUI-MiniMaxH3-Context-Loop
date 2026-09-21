#!/usr/bin/env python3
"""Approve & Stop follows this gate's chapter scope; no user projects touched."""
import asyncio
import copy
import json
import os
from pathlib import Path
import runpy
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

fixture = runpy.run_path(str(Path(__file__).with_name('_chapter_delivery_unit_test.py')))
chain = fixture['chain']


class DynamicPrompt:
    def __init__(self, prompt, display=None):
        self.prompt = prompt
        self.display = display or {}

    def get_original_prompt(self):
        return self.prompt

    def get_display_node_id(self, node_id):
        return self.display.get(node_id, node_id)


def graph(enabled=True, prefix=''):
    return {
        prefix + 'gate': {'class_type': 'MiniMaxH3ChainReview', 'inputs': {}},
        prefix + 'end': {'class_type': 'MiniMaxH3ChainLoopEnd',
                         'inputs': {'segment': [prefix + 'gate', 0]}},
        prefix + 'chapter': {'class_type': 'MiniMaxH3ChainChapterDelivery',
                             'inputs': {'manifest': [prefix + 'end', 0], 'enabled': enabled}},
        prefix + 'assemble': {'class_type': 'MiniMaxH3ChainAssemble',
                              'inputs': {'manifest': [prefix + 'chapter', 0]}},
    }


class ReviewChapterDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='h3-review-chapter-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture['folder_paths'].output_directory = self.temp.name
        if os.environ.get('H3_TEST_LAYOUT') == 'organized':
            chain.create_project(self.root / 'h3_chains/chapter_delivery')
        self.segments = [fixture['make_segment'](self.root, i) for i in range(1, 7)]
        for segment in self.segments:
            segment['resolution'] = {'width': 64, 'height': 64}
        chain._atomic_json(chain._run_editorial_path('chapter_delivery'), fixture['make_editorial']())

    def scope(self, prompt, gate='gate', display=None):
        return chain._review_partial_current_chapter(DynamicPrompt(prompt, display), gate)

    def test_on_off_default_and_absent(self):
        for enabled in (True, False):
            self.assertIs(self.scope(graph(enabled)), enabled)
        prompt = graph()
        del prompt['chapter']['inputs']['enabled']
        self.assertIs(self.scope(prompt), True)
        del prompt['chapter']
        self.assertIsNone(self.scope(prompt))
        self.assertIsNone(chain._review_partial_current_chapter(None, 'gate'))

    def test_recursive_retry_and_flattened_subgraph(self):
        gate = '1705.0.0.Recurse.0.0.1705.0.0.1931'
        for enabled in (True, False):
            self.assertIs(self.scope(graph(enabled), gate, {gate: 'gate'}), enabled)
            self.assertIs(self.scope(graph(enabled, '42:'), '42:gate'), enabled)
            self.assertIs(self.scope(graph(enabled, '42:'), gate, {gate: '42:gate'}), enabled)

    def test_reroutes_and_unrelated_delivery_do_not_change_scope(self):
        prompt = graph(False)
        prompt.update(graph(True, 'other:'))
        prompt['seg_route'] = {'class_type': 'Reroute', 'inputs': {'value': ['gate', 0]}}
        prompt['end']['inputs']['segment'] = ['seg_route', 0]
        prompt['manifest_route'] = {'class_type': 'Reroute', 'inputs': {'value': ['end', 0]}}
        prompt['chapter']['inputs']['manifest'] = ['manifest_route', 0]
        self.assertIs(self.scope(prompt), False)
        prompt['seg_route']['inputs']['value'] = ['gate', 1]  # status, not segment
        self.assertIsNone(self.scope(prompt))

    def test_linked_boolean(self):
        for value in (True, False):
            prompt = graph(['toggle_route', 0])
            prompt['toggle_route'] = {'class_type': 'Reroute', 'inputs': {'value': ['toggle', 0]}}
            prompt['toggle'] = {'class_type': 'PrimitiveBoolean', 'inputs': {'value': value}}
            self.assertIs(self.scope(prompt), value)
        prompt['toggle']['class_type'] = 'UnknownBooleanComputation'
        with self.assertRaisesRegex(ValueError, 'cannot read'):
            self.scope(prompt)

    def test_multiple_connected_deliveries(self):
        prompt = graph()
        prompt['chapter_two'] = copy.deepcopy(prompt['chapter'])
        self.assertIs(self.scope(prompt), True)
        prompt['chapter_two']['inputs']['enabled'] = False
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.scope(prompt)

    def assemble(self, count, enabled, *, audio_fails=False):
        manifest = fixture['make_manifest'](self.segments[:count], False)
        state = {'plan': {'run_name': 'chapter_delivery'}, 'index': count,
                 'segments': copy.deepcopy(self.segments[:count - 1])}
        before = copy.deepcopy(state)
        media_before = {path: path.read_bytes() for path in self.root.rglob('*')
                        if path.suffix in ('.mp4', '.safetensors')}
        result = {'result': ('test-preview.mp4',)}
        outcomes = [ValueError('test missing audio'), result] if audio_fails else [result]
        with patch.object(chain, '_partial_manifest', return_value=manifest), \
                patch.object(chain.MiniMaxH3ChainAssemble, 'assemble', side_effect=outcomes) as assemble:
            preview, warning = chain._assemble_review_partial(state, self.segments[count - 1], 'checkpointed', export_current_chapter=enabled)
        self.assertEqual(preview, 'test-preview.mp4')
        self.assertEqual(bool(warning), audio_fails)
        self.assertEqual(state, before)
        self.assertTrue(all(path.read_bytes() == data for path, data in media_before.items()))
        return [call.args[0] for call in assemble.call_args_list]

    def test_same_resolution_current_partial_and_finished_chapter(self):
        for count, enabled, expected in ((3, True, [1, 2, 3]), (4, True, [4]),
                                          (6, True, [4, 5, 6]), (6, False, list(range(1, 7))),
                                          (6, None, list(range(1, 7)))):
            with self.subTest(count=count, enabled=enabled):
                manifest = self.assemble(count, enabled)[0]
                self.assertEqual([item['index'] for item in manifest['segments']], expected)
                self.assertEqual(manifest['total_delivered_frames'], 10 * len(expected))
                if enabled and count >= 4:
                    self.assertEqual(manifest['chapter']['source_start_frame'], 30)
                    self.assertEqual(manifest['chapter']['complete'], count == 6)

    def test_follows_new_chapter_without_changing_toggle(self):
        editorial = fixture['make_editorial']()
        editorial['chapters'].append({'id': 'third', 'title': 'Third', 'start_scene': 6,
                                      'start_scene_id': 'scene_06'})
        chain._atomic_json(chain._run_editorial_path('chapter_delivery'), editorial)
        manifest = self.assemble(6, True)[0]
        self.assertEqual(manifest['chapter']['number'], 3)
        self.assertEqual([item['index'] for item in manifest['segments']], [6])

    def test_audio_fallback_keeps_chapter_scope(self):
        manifests = self.assemble(6, True, audio_fails=True)
        self.assertEqual(len(manifests), 2)
        for manifest in manifests:
            self.assertEqual([item['index'] for item in manifest['segments']], [4, 5, 6])

    def test_mixed_resolution_legacy_fallback_but_explicit_off_is_full(self):
        for segment in self.segments[3:]:
            segment['resolution'] = {'width': 128, 'height': 96}
        for enabled, expected in ((None, [4, 5, 6]), (False, list(range(1, 7)))):
            manifest = self.assemble(6, enabled)[0]
            self.assertEqual([item['index'] for item in manifest['segments']], expected)

    def test_gate_stop_passes_scope_and_still_blocks_downstream(self):
        plan = chain._normalize_plan(json.dumps({'shots': [
            {'id': f'scene_{i:02d}', 'prompt': 'Test scene', 'length': 39}
            for i in range(1, 7)]}), 'chapter_delivery', 64, 64, 5, 'video',
            'head', 'disabled', 'generated_audio', 5, 1.0, 8, 11, 18, 'test', 0, 'guide')
        state = {'plan': plan, 'index': 6, 'segments': self.segments[:5]}
        server = types.SimpleNamespace(instance=types.SimpleNamespace(client_id='test', send_sync=Mock()))
        blocker = type('TestExecutionBlocker', (), {'__init__': lambda self, _: None})
        for enabled in (True, False):
            with patch.object(chain, 'PromptServer', server), \
                    patch.object(chain, 'ExecutionBlocker', blocker), \
                    patch.object(chain, '_await_review_decision', AsyncMock(return_value={'action': 'stop'})), \
                    patch.object(chain, '_select_review_candidate', return_value=(self.segments[5], state)), \
                    patch.object(chain, '_prune_review_candidates', return_value=None), \
                    patch.object(chain, '_assemble_review_partial', return_value=('preview.mp4', '')) as assemble:
                result = asyncio.run(chain.MiniMaxH3ChainReview().review(
                    state, self.segments[5], True, False, 0.0, False, True, 'none',
                    dynprompt=DynamicPrompt(graph(enabled), {'recursive.gate': 'gate'}),
                    unique_id='recursive.gate'))
            self.assertIs(assemble.call_args.kwargs['export_current_chapter'], enabled)
            self.assertIs(assemble.call_args.args[0], state)
            self.assertIsInstance(result['result'][0], blocker)
            self.assertIn('partial video', result['result'][1])

    def test_real_video_same_size_chapters_exports_five_or_ten_frames(self):
        import torch

        raw = {'shots': [{'id': f'scene_{i:02d}', 'prompt': 'Test frame', 'length': 5,
                          'context_length': 0, 'audio_context_length': 0} for i in (1, 2)],
               'chapters': [{'id': 'first', 'title': 'First', 'start_scene': 1,
                             'start_scene_id': 'scene_01'},
                            {'id': 'second', 'title': 'Second', 'start_scene': 2,
                             'start_scene_id': 'scene_02'}]}
        plan = chain._normalize_plan(json.dumps(raw), 'chapter_delivery', 64, 64, 5, 'video',
                                     'head', 'disabled', 'generated_audio', 5, 1.0,
                                     8, 11, 18, 'test', 0, 'guide')
        editorial = {'scene_order': [{'scene': i, 'scene_id': f'scene_{i:02d}'} for i in (1, 2)],
                     'chapters': raw['chapters']}
        chain._atomic_json(chain._run_editorial_path('chapter_delivery'), editorial)
        with patch.object(chain, '_streams_from_latent', side_effect=lambda value: value['samples']):
            for index in (1, 2):
                state = chain._initial_state(plan, index)
                latent = {'samples': [torch.zeros(1, 24, 2, 4, 4), torch.zeros(1, 32, 2, 9)]}
                audio = {'waveform': torch.zeros(1, 2, round(5 / 24 * 8000)), 'sample_rate': 8000}
                segment = chain.MiniMaxH3ChainSegmentSave().save(
                    state, torch.full((5, 64, 64, 3), index / 3), latent, audio)['result'][0]
            for enabled, expected_frames in ((True, 5), (False, 10)):
                with self.subTest(enabled=enabled):
                    path, warning = chain._assemble_review_partial(state, segment, 'checkpointed', export_current_chapter=self.scope(graph(enabled)))
                    self.assertFalse(warning)
                    with chain.av.open(path) as container:
                        self.assertEqual(len(container.streams.audio), 1)
                        frames = list(container.decode(video=0))
                    self.assertEqual(len(frames), expected_frames)
                    if enabled:
                        self.assertGreater(frames[0].to_ndarray(format='rgb24').mean(), 150)
                    self.assertEqual(len(state['segments']), 1)


if __name__ == '__main__':
    unittest.main()

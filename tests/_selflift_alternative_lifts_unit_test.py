"""CPU-only alternative lift, download, schema and sampler contracts."""
import dataclasses
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import _selflift_unit_test as fixtures

torch = fixtures.torch
torch.set_num_threads(2)
choices = importlib.import_module(fixtures.PACKAGE + '.selflift_upscalers')
alternative = importlib.import_module(fixtures.PACKAGE + '.selflift_runtime.alternative_lifts')
architecture = importlib.import_module(fixtures.PACKAGE + '.selflift_runtime.h3_clean_upscaler')
mm = sys.modules['comfy.model_management']


def load_dispatcher():
    vae = types.ModuleType('comfy.ldm.minimax.vae')
    vae.LATENTS_MEAN, vae.LATENTS_STD = [0.] * 24, [1.] * 24
    folder = types.ModuleType('folder_paths')
    folder.folder_names_and_paths = {'latent_upscale_models': ()}
    name = fixtures.PACKAGE + '.selflift_runtime._alternative_dispatch_test'
    spec = importlib.util.spec_from_file_location(name, fixtures.ROOT / 'selflift_runtime/h3_upscaler.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'comfy.ldm.minimax.vae': vae, 'folder_paths': folder}):
        spec.loader.exec_module(module)
    return module


class AlternativeTests(unittest.TestCase):
    def test_schema_lists_builtins_without_loading_runtime_or_weights(self):
        folder = types.ModuleType('folder_paths')
        folder.folder_names_and_paths = {'latent_upscale_models': ()}
        folder.get_filename_list = lambda _: ['z.pth', 'a.safetensors', choices.TRIDAE_CHECKPOINT]
        with patch.dict(sys.modules, {'folder_paths': folder}), \
                patch.object(alternative, '_checkpoint_path', side_effect=AssertionError('UI must not load')), \
                patch.object(alternative, '_load_tridae', side_effect=AssertionError('UI must not load')):
            schema = fixtures.nodes.MiniMaxH3SelfLiftProject.INPUT_TYPES()
        self.assertEqual(schema['required']['upscaler_model'][0],
                         ['none', 'bilinear', 'tridae', 'a.safetensors', 'z.pth'])
        self.assertFalse(schema['required']['enabled'][1]['default'])

    def test_bilinear_is_spatial_only_fp32_and_does_not_mutate_input(self):
        z = torch.randn(2, 24, 3, 4, 6, dtype=torch.bfloat16)
        original = z.clone()
        with patch.object(alternative, '_load_tridae', side_effect=AssertionError('No model')), \
                patch.object(mm, 'get_torch_device', side_effect=AssertionError('No GPU'), create=True):
            result = alternative.latent_lift(z, (7, 9), 'bilinear', temporal_split=1, cleanup_after=True)
        expected = torch.stack([torch.nn.functional.interpolate(z[:, :, t].float(), size=(7, 9),
                                mode='bilinear', align_corners=False) for t in range(3)], dim=2)
        torch.testing.assert_close(result, expected, rtol=0, atol=0)
        torch.testing.assert_close(z, original, rtol=0, atol=0)
        self.assertEqual(result.dtype, torch.float32)

    def test_invalid_shapes_rejected(self):
        for z in (torch.zeros(1, 24, 2, 3), torch.zeros(1, 4, 2, 3, 4),
                  torch.zeros(1, 24, 2, 3, 4, dtype=torch.int32)):
            with self.assertRaisesRegex(ValueError, 'Bx24'):
                alternative.latent_lift(z, (6, 8), 'bilinear')
        with self.assertRaisesRegex(ValueError, 'positive integers'):
            alternative.latent_lift(torch.ones(1, 24, 2, 3, 4), (0, 8), 'bilinear')

    def test_tridae_grid_validation_precedes_sampling(self):
        for samples in (torch.zeros(1, 24, 5, 68, 120),
                        [torch.zeros(1, 24, 5, 68, 120)],
                        fixtures.Nested([torch.zeros(1, 24, 5, 68, 120)])):
            choices.validate_upscaler_grid('tridae', {'samples': samples})
        with self.assertRaisesRegex(ValueError, 'multiples of 64'):
            choices.validate_upscaler_grid('tridae', {'samples': torch.zeros(1, 24, 5, 34, 60)})
        for name in ('bilinear', 'old_bf16.safetensors'):
            choices.validate_upscaler_grid(name, {'samples': torch.zeros(1, 24, 5, 34, 60)})

    def test_lift_dispatch_leaves_legacy_path_unchanged(self):
        dispatcher = load_dispatcher()
        z = torch.zeros(1, 24, 2, 4, 6)
        with patch.object(dispatcher, '_learned_latent_lift', return_value=z) as legacy, \
                patch.object(alternative, 'latent_lift', return_value=z) as builtin:
            for name in ('bilinear', 'tridae'):
                self.assertIs(dispatcher.learned_latent_lift(z, (8, 12), name, device='cpu',
                              temporal_split=1, cleanup_after=True), z)
                builtin.assert_called_with(z, (8, 12), name, device='cpu', temporal_split=1, cleanup_after=True)
            legacy.assert_not_called()
            dispatcher.learned_latent_lift(z, (8, 12), 'old.safetensors', device='cpu', temporal_split=1)
            legacy.assert_called_once_with(z, (8, 12), 'old.safetensors', 'cpu', 1)

    def test_tridae_has_no_lbh_normalization_or_temporal_split(self):
        model = torch.nn.Conv3d(24, 24, 1)
        model.forward = Mock(side_effect=lambda x: alternative.spatial_bilinear(x, (8, 12)) + 0.1)
        patcher = types.SimpleNamespace(model=model, model_size=lambda: 100, load_device='cpu')
        z = torch.randn(1, 24, 7, 4, 6)
        with patch.object(alternative, '_load_tridae', return_value=patcher), \
                patch.object(mm, 'load_models_gpu', create=True) as load, \
                patch.object(alternative, '_retire_tridae') as retire:
            result = alternative.latent_lift(z, (8, 12), 'tridae', device='cpu', temporal_split=3)
        model.forward.assert_called_once()
        torch.testing.assert_close(model.forward.call_args.args[0], z, rtol=0, atol=0)
        torch.testing.assert_close(result, alternative.spatial_bilinear(z, (8, 12)) + .1)
        self.assertTrue(load.call_args.kwargs['force_full_load'])
        retire.assert_not_called()

    def test_tridae_rejects_non_2x_before_loading(self):
        with patch.object(alternative, '_load_tridae') as load:
            with self.assertRaisesRegex(ValueError, 'exactly 2x'):
                alternative.latent_lift(torch.ones(1, 24, 3, 4, 6), (9, 12), 'tridae', device='cpu')
            load.assert_not_called()

    def test_cleanup_only_after_success(self):
        result, patcher = torch.ones(1), object()
        with patch.object(alternative, '_tridae_lift', return_value=(result, patcher)) as run, \
                patch.object(alternative, '_retire_tridae') as retire:
            z = torch.zeros(1, 24, 2, 4, 6)
            self.assertIs(alternative.latent_lift(z, (8, 12), 'tridae', device='cpu', cleanup_after=True), result)
            retire.assert_called_once_with(patcher)
            retire.reset_mock()
            run.side_effect = RuntimeError('OOM')
            with self.assertRaisesRegex(RuntimeError, 'OOM'):
                alternative.latent_lift(z, (8, 12), 'tridae', device='cpu', cleanup_after=True)
            retire.assert_not_called()

    def test_cleanup_only_retires_own_model(self):
        ours = types.SimpleNamespace(model=object(), load_device='cpu')
        other = types.SimpleNamespace(model=object(), load_device='cpu')
        a, b = (types.SimpleNamespace(model=p, model_unload=Mock(return_value=True)) for p in (ours, other))
        registry = [a, b]
        with patch.object(mm, 'current_loaded_models', registry, create=True), \
                patch.object(mm, 'throw_exception_if_processing_interrupted', create=True):
            alternative._retire_tridae(ours)
        self.assertEqual(registry, [b])
        a.model_unload.assert_called_once()
        b.model_unload.assert_not_called()

    def test_settings_keep_old_signature_and_separate_new_lifters(self):
        old = {'upscaler_model': 'old.safetensors'}
        payload = {'version': 1, 'scale': .5, **old}
        self.assertEqual(fixtures.state.settings_signature(old),
                         hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
        signatures = {fixtures.state.settings_signature({'upscaler_model': name})
                      for name in ('old.safetensors', 'bilinear', 'tridae')}
        self.assertEqual(len(signatures), 3)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.destination = self.root / choices.TRIDAE_CHECKPOINT
        self.content = b'test checkpoint'
        self.stack = [patch.object(alternative, 'TRIDAE_SHA256', hashlib.sha256(self.content).hexdigest()),
                      patch.object(mm, 'throw_exception_if_processing_interrupted', create=True)]
        for p in self.stack:
            p.start(); self.addCleanup(p.stop)

    def test_download_is_verified_and_published_atomically(self):
        def response(*args, **kwargs):
            self.assertFalse(self.destination.exists())
            return io.BytesIO(self.content)
        with patch.object(alternative.urllib.request, 'urlopen', side_effect=response):
            self.assertEqual(alternative._download_checkpoint(self.destination), self.destination)
        self.assertEqual(self.destination.read_bytes(), self.content)
        self.assertEqual(list(self.root.iterdir()), [self.destination])

    def test_bad_download_never_installs_partial(self):
        with patch.object(alternative.urllib.request, 'urlopen', return_value=io.BytesIO(b'bad')):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                alternative._download_checkpoint(self.destination)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_file_is_not_overwritten_or_downloaded(self):
        self.destination.write_bytes(b'user file')
        with patch.object(alternative.urllib.request, 'urlopen') as network:
            alternative._download_checkpoint(self.destination)
            network.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'checksum'):
            alternative._verify_checkpoint(self.destination)
        self.assertEqual(self.destination.read_bytes(), b'user file')

    def test_cancel_leaves_no_installed_file_or_partial(self):
        with patch.object(alternative.urllib.request, 'urlopen', return_value=io.BytesIO(self.content)), \
                patch.object(mm, 'throw_exception_if_processing_interrupted', side_effect=RuntimeError('cancel')):
            with self.assertRaisesRegex(RuntimeError, 'cancel'):
                alternative._download_checkpoint(self.destination)
        self.assertEqual(list(self.root.iterdir()), [])


class ArchitectureTests(unittest.TestCase):
    def test_loader_uses_owned_fp32_weights_legacy_patcher_and_cache(self):
        from safetensors.torch import save_file
        base = architecture.UpscalerConfigV2(24, 8, 1, 8, 1, 3)
        config = architecture.UpscalerConfigV3(8, 1, 2, 2, 2)
        model = architecture.H3LatentUpscalerV3(base, config).eval()
        metadata = {'metadata': json.dumps({'format': architecture.CHECKPOINT_FORMAT,
                    'strict_latent_only': True, 'base_config': dataclasses.asdict(base),
                    'config': dataclasses.asdict(config)})}
        patcher_module = sys.modules['comfy.model_patcher']
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.safetensors'
            save_file(model.state_dict(), str(path), metadata=metadata)
            def wrap(model, load_device, offload_device):
                self.assertEqual(offload_device, torch.device('cpu'))
                self.assertTrue(all(p.dtype == torch.float32 for p in model.parameters()))
                return types.SimpleNamespace(model=model, load_device=load_device)
            with patch.object(alternative, '_checkpoint_path', return_value=path), \
                    patch.object(alternative, '_verify_checkpoint') as verify, \
                    patch.object(alternative, '_patcher_cache', {}), \
                    patch.object(patcher_module, 'ModelPatcher', side_effect=wrap, create=True) as legacy, \
                    patch.object(patcher_module, 'CoreModelPatcher', side_effect=AssertionError('No DynamicVRAM'), create=True):
                loaded = alternative._load_tridae(torch.device('cpu'))
                self.assertIs(alternative._load_tridae(torch.device('cpu')), loaded)
                verify.assert_called_once_with(path)
                legacy.assert_called_once()
                z = torch.randn(1, 24, 3, 4, 6)
                with torch.inference_mode():
                    torch.testing.assert_close(loaded.model(z), model(z), rtol=0, atol=0)

    def test_strict_metadata_and_tensors_and_output_contract(self):
        from safetensors.torch import save_file
        base = architecture.UpscalerConfigV2(24, 8, 1, 8, 1, 3)
        config = architecture.UpscalerConfigV3(8, 2, 2, 2, 2)
        reference = architecture.H3LatentUpscalerV3(base, config).eval()
        metadata = {'metadata': json.dumps({'format': architecture.CHECKPOINT_FORMAT,
                    'strict_latent_only': True, 'base_config': dataclasses.asdict(base),
                    'config': dataclasses.asdict(config)}), 'step': '87800'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.safetensors'
            save_file(reference.state_dict(), str(path), metadata=metadata)
            info = architecture.read_checkpoint_info(path)
            loaded = architecture.build_upscaler(reference.state_dict(), info)
            z = torch.randn(1, 24, 3, 4, 6)
            with torch.inference_mode():
                result = loaded(z)
                torch.testing.assert_close(result, reference(z), rtol=0, atol=0)
            self.assertEqual(result.shape, (1, 24, 3, 8, 12))
            self.assertTrue(torch.isfinite(result).all())
            broken = dict(reference.state_dict()); broken.pop('stem.weight')
            with self.assertRaisesRegex(ValueError, 'declared architecture'):
                architecture.build_upscaler(broken, info)
            metadata['metadata'] = json.dumps({'format': 'wrong'})
            save_file(reference.state_dict(), str(path), metadata=metadata)
            with self.assertRaisesRegex(ValueError, 'Unsupported checkpoint format'):
                architecture.read_checkpoint_info(path)


class SamplerTests(unittest.TestCase):
    setUp = fixtures.SelfLiftTests.setUp

    def test_bad_tridae_grid_stops_before_low_sampling(self):
        latent = {'samples': fixtures.Nested([torch.zeros(1, 24, 17, 10, 12), self.audio])}
        with patch.object(fixtures.nodes, 'upscaler_models', return_value=['tridae']):
            with self.assertRaisesRegex(ValueError, 'multiples of 64'):
                fixtures.nodes.MiniMaxH3ChainSelfLiftSampler().sample(
                    {'plan': {fixtures.nodes.SETTINGS_KEY: {**self.settings, 'upscaler_model': 'tridae'}}},
                    fixtures.Model(), [], object(), latent, fixtures.Euler(), self.sigmas, 42)
        self.assertEqual(fixtures.CALLS, [])

    def test_alternatives_use_same_high_stage_masks_and_signatures(self):
        dispatcher = load_dispatcher()
        stub = sys.modules[fixtures.PACKAGE + '.selflift_runtime.h3_upscaler']
        for name in ('bilinear', 'tridae'):
            with self.subTest(name=name):
                fixtures.CALLS.clear()
                settings = {**self.settings, 'upscaler_model': name}
                fake = types.SimpleNamespace(model=torch.nn.Conv3d(24, 24, 1), load_device='cpu', model_size=lambda: 100)
                fake.model.forward = lambda z: alternative.spatial_bilinear(z, (8, 12)) + .1
                with patch.object(fixtures.nodes, 'upscaler_models', return_value=['none', 'bilinear', 'tridae']), \
                        patch.object(stub, 'learned_latent_lift', dispatcher.learned_latent_lift, create=True), \
                        patch.object(alternative, '_load_tridae', return_value=fake), \
                        patch.object(mm, 'get_torch_device', return_value='cpu', create=True), \
                        patch.object(mm, 'load_models_gpu', create=True):
                    out, _ = fixtures.nodes.MiniMaxH3ChainSelfLiftSampler().sample(
                        {'plan': {fixtures.nodes.SETTINGS_KEY: settings}}, fixtures.Model(), [], object(),
                        self.latent, fixtures.Euler(), self.sigmas, 42)
                self.assertEqual([call['shape'][-2:] for call in fixtures.CALLS], [(4, 6), (8, 12)])
                torch.testing.assert_close(out['samples'].unbind()[1], self.audio)
                self.assertEqual(out[fixtures.state.SIGNATURE], fixtures.state.settings_signature(settings))
                for call in fixtures.CALLS:
                    self.assertTrue(torch.all(call['masks'][0][:, :, :2] == 0))


if __name__ == '__main__':
    unittest.main()

"""Alternative selection/resume through the actual hunt loop; CPU only."""
import importlib
import unittest
from unittest.mock import patch

import _selflift_hunt_unit_test as fixtures
from _selflift_alternative_lifts_unit_test import alternative, load_dispatcher


class AlternativeHuntTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.HuntTests.setUp
    fake_preview = fixtures.HuntTests.fake_preview
    run_node = fixtures.HuntTests.run_node

    async def test_bilinear_and_tridae_have_distinct_hunts_and_resume(self):
        dispatcher = load_dispatcher()
        stub = importlib.import_module(fixtures.PACKAGE + '.selflift_runtime.h3_upscaler')
        mm = importlib.import_module('comfy.model_management')
        def fake_tridae(z, hw, device):
            return alternative.spatial_bilinear(z, hw) + .1, object()
        with patch.object(fixtures.nodes, 'upscaler_models', return_value=['bilinear', 'tridae']), \
                patch.object(stub, 'learned_latent_lift', dispatcher.learned_latent_lift), \
                patch.object(alternative, '_tridae_lift', side_effect=fake_tridae), \
                patch.object(mm, 'get_torch_device', return_value='cpu', create=True):
            for name in ('bilinear', 'tridae'):
                self.settings['upscaler_model'] = name
                fixtures.CALLS.clear()
                result = await self.run_node(1, review_enabled=False)
                self.assertEqual([call['shape'][-2:] for call in fixtures.CALLS], [(4, 6), (8, 12)])
                self.assertEqual(result['result'][0][fixtures.carry.SIGNATURE],
                                 fixtures.carry.settings_signature(self.settings))
                fixtures.CALLS.clear()
                await self.run_node(1, review_enabled=False)
                self.assertEqual(fixtures.CALLS, [])
        records = self.store.list()
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record['phase'] == 'finished' for record in records))

    async def test_tridae_bad_grid_fails_before_any_hunt_files_or_sampling(self):
        self.settings['upscaler_model'] = 'tridae'
        self.latent['samples'] = fixtures.Nested([fixtures.torch.zeros(1, 24, 17, 10, 12), self.audio])
        with patch.object(fixtures.nodes, 'upscaler_models', return_value=['tridae']):
            with self.assertRaisesRegex(ValueError, 'multiples of 64'):
                await self.run_node(1, review_enabled=False)
        self.assertEqual(fixtures.CALLS, [])
        self.assertEqual(self.store.list(), [])


if __name__ == '__main__':
    unittest.main()

"""Isolated Radau Seed Hunt recovery; only temporary synthetic projects."""
import asyncio
import json
import unittest
from unittest.mock import patch

import _selflift_radau_unit_test as radau_tests
import _selflift_hunt_unit_test as hunt_tests
import comfy.k_diffusion.sampling
import comfy.samplers
import comfy.utils


class RadauHuntTests(unittest.IsolatedAsyncioTestCase):
    fake_preview = hunt_tests.HuntTests.fake_preview

    async def select_when_ready(self, task):
        async with asyncio.timeout(5):
            while not task.done():
                waiting = next((r for r in self.store.list() if r["phase"] == "waiting"), None)
                if waiting:
                    hunt_tests.hunt.approve(self.store, waiting["id"], 1)
                    break
                await asyncio.sleep(.01)
            return await task

    def setUp(self):
        hunt_tests.HuntTests.setUp(self)
        radau_tests.STAGES.clear()
        self.sampler = radau_tests.Radau()
        for p in (patch.object(comfy.k_diffusion.sampling, "sample_rk_beta", radau_tests.sample_rk_beta, create=True),
                  patch.object(comfy.samplers, "sample", radau_tests.packed_sampler),
                  patch.object(comfy.utils, "unpack_latents", radau_tests.unpack, create=True)):
            p.start()
            self.addCleanup(p.stop)

    async def run_node(self, candidates=1, **kwargs):
        kwargs.setdefault("sampler", self.sampler)
        return await hunt_tests.HuntTests.run_node(self, candidates, **kwargs)

    async def test_gate_off_matches_ordinary_radau_and_reuses_finished_take(self):
        with patch.object(hunt_tests.preview, "check_preview", side_effect=AssertionError("No decoder needed")), \
             patch.object(hunt_tests.preview, "save_preview", side_effect=AssertionError("No preview needed")):
            result = await asyncio.wait_for(self.run_node(4, review_enabled=False), 5)
        self.assertEqual([v["shape"][-2:] for v in radau_tests.STAGES], [(4, 6), (8, 12)])
        record = self.store.list()[0]
        self.assertEqual((record["selected"], len(record["candidates"])), (1, 1))
        self.assertIsNone(record["candidates"][0]["preview"])
        middle = hunt_tests.store_module.load_bundle(self.store.locate(record["id"]) / "take_0001.safetensors")
        self.assertEqual(middle["format"], radau_tests.radau.FORMAT)
        expected = hunt_tests.runtime.progressive_sample(hunt_tests.Model(), self.positive, self.positive,
            object(), self.latent, self.sampler, self.sigmas, 42, 1., 2, .5, 0., .5, 1., "nearest",
            latent_lifter=hunt_tests.lift)
        for a, b in zip(expected["samples"].unbind(), result["result"][0]["samples"].unbind()):
            radau_tests.torch.testing.assert_close(a, b, rtol=0, atol=0)
        radau_tests.STAGES.clear()
        self.store = hunt_tests.HuntStore(self.root)
        await asyncio.wait_for(self.run_node(4, review_enabled=False), 5)
        self.assertEqual(radau_tests.STAGES, [], "Automatic Radau still recovers after downstream failure/reboot")

    async def test_preview_then_high_oom_recover_without_repeating_low_pass(self):
        with patch.object(hunt_tests.preview, "save_preview", side_effect=RuntimeError("preview OOM")):
            with self.assertRaisesRegex(RuntimeError, "preview OOM"):
                await self.run_node()
        record = self.store.list()[0]
        folder = self.store.locate(record["id"])
        middle = hunt_tests.store_module.load_bundle(folder / "take_0001.safetensors")
        self.assertEqual(middle["format"], radau_tests.radau.FORMAT)
        self.assertEqual(len(radau_tests.STAGES), 1)
        self.store = hunt_tests.HuntStore(self.root)
        radau_tests.STAGES.clear()
        original = hunt_tests.runtime.progressive_sample

        def fail_high(*args, **kwargs):
            if kwargs.get("handoff") is not None:
                raise RuntimeError("high OOM")
            return original(*args, **kwargs)

        with patch.object(hunt_tests.runtime, "progressive_sample", side_effect=fail_high):
            with self.assertRaisesRegex(RuntimeError, "high OOM"):
                await self.select_when_ready(asyncio.create_task(self.run_node()))
        self.assertEqual(radau_tests.STAGES, [])
        self.assertEqual(self.store.list()[0]["selected"], 1)
        result = await self.run_node()
        self.assertEqual([c["shape"][-2:] for c in radau_tests.STAGES], [(8, 12)])
        radau_tests.STAGES.clear()
        restored = await self.run_node()
        self.assertEqual(radau_tests.STAGES, [])
        for a, b in zip(result["result"][0]["samples"].unbind(), restored["result"][0]["samples"].unbind()):
            radau_tests.torch.testing.assert_close(a, b, rtol=0, atol=0)

    async def test_sampler_settings_separate_batches_without_prompt_graph(self):
        for sampler in (self.sampler, radau_tests.Radau(), radau_tests.Euler()):
            if sampler is not self.sampler and isinstance(sampler, radau_tests.Radau):
                sampler.extra_options["BONGMATH"] = False
            # Simulated crash leaves a saved low take for each exact sampler.
            with patch.object(hunt_tests.preview, "save_preview", side_effect=RuntimeError("preview OOM")):
                with self.assertRaisesRegex(RuntimeError, "preview OOM"):
                    await self.run_node(sampler=sampler)
        records = self.store.list()
        self.assertEqual(len(records), 3)
        contracts = [json.loads((self.store.locate(r["id"]) / "recovery.json").read_text())["contract"]
                     for r in records]
        self.assertEqual(sum("sampler_contract" in c for c in contracts), 2)
        for record, contract in zip(records, contracts):
            self.assertEqual(record["id"], hunt_tests.store_module.digest(contract))
        # No change to the connected sampler, and the first Radau take reuses
        # its original batch after both another Radau setup and Euler ran.
        radau_tests.STAGES.clear()
        await self.select_when_ready(asyncio.create_task(self.run_node()))
        self.assertEqual([c["shape"][-2:] for c in radau_tests.STAGES], [(8, 12)])
        self.assertEqual(len(self.store.list()), 3)


if __name__ == "__main__":
    unittest.main()

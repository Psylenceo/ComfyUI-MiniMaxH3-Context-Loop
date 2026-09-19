"""On-demand lift previews: CPU contracts, durable recovery, no real projects/GPU."""
import asyncio
import importlib
import sys
import types
import unittest
from unittest.mock import patch

import _selflift_unit_test as f
import _selflift_hunt_unit_test as h
import _selflift_radau_unit_test as r


class LiftEndpointTests(unittest.TestCase):
    setUp = f.SelfLiftTests.setUp

    def test_preview_is_exact_clean_endpoint_without_low_or_high_sampling(self):
        for rho, drift_enabled in ((0., False), (.2, False), (0., True)):
            with self.subTest(rho=rho, drift=drift_enabled):
                model = f.Model()
                if drift_enabled:
                    drift = importlib.import_module(f.PACKAGE + ".drift_control")
                    model.model_options[drift._WRAPPER_KEY] = drift._DriftControlMaskState(
                        self.video.shape, 12, schedule_override=self.sigmas)
                args = (model, [], [], object(), self.latent, f.Euler(), self.sigmas,
                        42, 1., 2, .5, rho, .5, 1., "nearest")
                middle = f.runtime.progressive_sample(*args, stop_after_low=True)
                before = middle["video_prediction"].clone()
                f.CALLS.clear()
                with patch.object(f.runtime.selflift, "_pixel_anchor_video",
                                  side_effect=lambda z, vae, hw: f.lift(z, hw) + .2):
                    preview = f.runtime.progressive_sample(*args, handoff=middle,
                        stop_after_lift=True, latent_lifter=f.lift)
                    self.assertEqual(f.CALLS, [], "Neither low nor high denoiser may execute")
                    seen = []
                    def noise(z, seed, batch_index=None):
                        if seed == 43 and tuple(z.shape) == tuple(self.video.shape):
                            seen.append(z.clone())
                        return f.prepare_noise(z, seed, batch_index)
                    with patch.object(f.runtime.comfy.sample, "prepare_noise", side_effect=noise):
                        f.runtime.progressive_sample(*args, handoff=middle, latent_lifter=f.lift)
                self.assertGreaterEqual(len(seen), 1)  # Further noise draws can belong to the guide anchor.
                f.torch.testing.assert_close(preview, seen[0], rtol=0, atol=0)
                f.torch.testing.assert_close(middle["video_prediction"], before, rtol=0, atol=0)
                self.assertEqual(preview.device.type, "cpu")

    def test_preview_requires_a_handoff(self):
        args = (f.Model(), [], [], object(), self.latent, f.Euler(), self.sigmas,
                42, 1., 2, .5, 0., .5, 1., "nearest")
        for kw in ({}, {"stop_after_low": True, "handoff": {}}):
            with self.assertRaisesRegex(ValueError, "saved low-pass"):
                f.runtime.progressive_sample(*args, stop_after_lift=True, **kw)
        self.assertEqual(f.CALLS, [])


class UpscalePreviewHuntTests(unittest.IsolatedAsyncioTestCase):
    run_node = h.HuntTests.run_node
    fake_preview = h.HuntTests.fake_preview

    def setUp(self):
        h.HuntTests.setUp(self)
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def wait_record(self, predicate):
        async with asyncio.timeout(10):
            while True:
                for task in self.tasks:
                    if task.done():
                        task.result()  # Surface unexpected execution errors immediately.
                records = self.store.list()
                if records and predicate(records[0]):
                    return records[0]
                await asyncio.sleep(.01)

    async def start_waiting(self, **kwargs):
        task = asyncio.create_task(self.run_node(1, **kwargs))
        self.tasks.append(task)
        record = await self.wait_record(lambda b: b["phase"] == "waiting")
        return task, record

    def request(self, record, ordinal=1):
        return h.hunt.request_upscale_preview(self.store, record["id"], ordinal, record["created_at"])

    async def test_preview_waits_for_approval_reuses_cache_and_preserves_identity(self):
        task, record = await self.start_waiting()
        folder = self.store.locate(record["id"])
        source = (folder / "take_0001.safetensors").read_bytes()
        f.CALLS.clear()
        self.request(record)
        with self.assertRaisesRegex(ValueError, "Wait for the upscale preview"):
            h.hunt.approve(self.store, record["id"], 1)
        ready = await self.wait_record(lambda b: b["candidates"][0].get("upscale_preview"))
        self.assertEqual(f.CALLS, [])
        self.assertIsNone(ready["selected"])
        self.assertEqual(ready["phase"], "waiting")
        self.assertFalse(task.done())
        self.assertEqual(ready["id"], record["id"])
        self.assertEqual((folder / "take_0001.safetensors").read_bytes(), source)
        preview = self.store.root / ready["candidates"][0]["upscale_preview"]
        self.assertTrue(preview.name.endswith(".upscale.mp4"))
        mtime = preview.stat().st_mtime_ns
        with patch.object(f.runtime, "progressive_sample", side_effect=AssertionError("cached preview must not lift")):
            self.request(record)
            await self.wait_record(lambda b: b.get("upscale_request") is None)
        self.assertEqual(preview.stat().st_mtime_ns, mtime)
        h.hunt.approve(self.store, record["id"], 1)
        await asyncio.wait_for(task, 5)
        self.assertEqual([c["shape"][-2:] for c in f.CALLS], [(8, 12)])
        self.assertEqual(self.state["plan"]["shots"][0]["seed"], 42)

    async def test_preview_failure_keeps_gate_and_low_take_retryable(self):
        fail = True
        def decode(video, path, tiny, raw, trim):
            if fail and path.name.endswith(".upscale.mp4"):
                raise RuntimeError("decode OOM")
            self.fake_preview(video, path, tiny, raw, trim)
        with patch.object(h.preview, "save_preview", side_effect=decode):
            task, record = await self.start_waiting()
            f.CALLS.clear()
            self.request(record)
            failed = await self.wait_record(lambda b: b["candidates"][0].get("upscale_error"))
            self.assertEqual(failed["phase"], "waiting")
            self.assertIsNone(failed["selected"])
            self.assertIsNone(failed["upscale_request"])
            self.assertFalse(task.done())
            fail = False
            self.request(record)
            ready = await self.wait_record(lambda b: b["candidates"][0].get("upscale_preview"))
            self.assertNotIn("upscale_error", ready["candidates"][0])
            self.assertEqual(f.CALLS, [])

    async def test_request_during_low_runs_after_current_take_before_next_candidate(self):
        original = h.preview.save_preview.side_effect
        def decoded(video, path, tiny, raw, trim):
            original(video, path, tiny, raw, trim)
            record = self.store.list()[0]
            if record["current"] == 2 and record["phase"] == "preview":
                self.request(record, 1)
        with patch.object(h.preview, "save_preview", side_effect=decoded):
            task = asyncio.create_task(self.run_node(3))
            self.tasks.append(task)
            ready = await self.wait_record(lambda b: len(b["candidates"]) == 3 and b["phase"] == "waiting")
        self.assertTrue(ready["candidates"][0]["upscale_preview"])
        self.assertIsNone(ready["selected"])
        self.assertEqual([c["shape"][-2:] for c in f.CALLS], [(4, 6)] * 3)

    async def test_interrupt_releases_worker_and_resume_reuses_low_and_preview(self):
        task, record = await self.start_waiting()
        self.request(record)
        await self.wait_record(lambda b: b["candidates"][0].get("upscale_preview"))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.tasks.remove(task)
        f.CALLS.clear()
        # Simulate a process dying with an unfulfilled preview request.
        self.store.update(record["id"], lambda b: b.update(upscale_request=1))
        task, resumed = await self.start_waiting()
        self.assertEqual(resumed["id"], record["id"])
        self.assertIsNone(resumed["upscale_request"])
        self.assertTrue(resumed["candidates"][0]["upscale_preview"])
        self.assertEqual(f.CALLS, [])

    async def test_pending_preview_blocks_cleanup_stale_requests_and_second_take(self):
        task, record = await self.start_waiting()
        with self.assertRaisesRegex(ValueError, "changed"):
            h.hunt.request_upscale_preview(self.store, record["id"], 1, -1)
        with self.assertRaisesRegex(ValueError, "completed low"):
            self.request(record, 9)
        self.request(record)
        with self.assertRaisesRegex(ValueError, "Stop the running hunt"):
            h.hunt.clean_saved_hunt(self.store, record["id"])
        for values in ({"selected": 1}, {"phase": "high"}, {"review_enabled": False}):
            self.store.update(record["id"], lambda b: b.update(values))
            with self.assertRaisesRegex(ValueError, "unapproved take"):
                self.request(record)
            self.store.update(record["id"], lambda b: b.update(selected=None, phase="waiting", review_enabled=True))

    async def test_route_records_request_only_and_validates_payload(self):
        task, record = await self.start_waiting()
        handlers = {}
        class Routes:
            def get(self, path):
                return lambda handler: handlers.setdefault(path, handler)
            post = get
        server = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=Routes())))
        class Request:
            def __init__(self, body): self.body = body
            async def json(self): return self.body
        with patch.dict(sys.modules, {"server": server}):
            h.hunt.register_routes()
        handler = handlers["/h3/selflift/upscale-preview"]
        for body in ({"id":record["id"], "ordinal":True, "created_at":record["created_at"]},
                     {"id":record["id"], "ordinal":1}):
            response = await handler(Request(body))
            self.assertEqual(response.status, 400)
        # Do not let the worker consume this request during the route check.
        with patch.object(h.hunt, "request_upscale_preview", return_value={}) as request:
            response = await handler(Request({"id":record["id"], "ordinal":1, "created_at":record["created_at"]}))
            self.assertEqual(response.status, 200)
            request.assert_called_once()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.tasks.remove(task)
        with self.assertRaisesRegex(ValueError, "resume mode"):
            self.request(record)


class RadauLiftEndpointTests(unittest.TestCase):
    setUp = r.RadauTests.setUp
    run_runtime = r.RadauTests.run_runtime

    def test_radau_preview_does_not_call_sampler_or_endpoint_evaluation(self):
        middle = self.run_runtime(stop_after_low=True)
        r.STAGES.clear()
        preview = self.run_runtime(handoff=middle, stop_after_lift=True)
        self.assertEqual(r.STAGES, [])
        self.assertEqual(tuple(preview.shape), tuple(self.video.shape))
        f.torch.testing.assert_close(preview[:, :, :2], self.video[:, :, :2], rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()

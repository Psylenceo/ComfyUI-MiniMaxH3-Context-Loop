"""Slow branch I/O and lock waits must not block ComfyUI's request loop."""
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "checkpoint_helpers", Path(__file__).with_name("_checkpoint_revision_unit_test.py"))
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
chain = helpers.chain
scope = importlib.import_module(chain.__package__ + ".branch_scope")


class Request(helpers.JsonRequest):
    query = {}
    method = "POST"


class ResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = patch.object(helpers.folder_paths, "output_directory", self.temp.name)
        self.output.start()
        self.addCleanup(self.output.stop)
        self.run = "responsive_test"

    async def slow_call(self, owner, method, handler, request, result, expected_branch="main"):
        loop = asyncio.get_running_loop()
        released = threading.Event()
        observed = []

        def slow(*args, **kwargs):
            observed.append((threading.get_ident(), scope.current_branch(self.run)))
            # Only the event loop can release this simulated slow disk read.
            # A bounded wait fails safely if the route regresses to inline I/O.
            loop.call_soon_threadsafe(released.set)
            if not released.wait(1):
                raise AssertionError("Disk I/O blocked the request loop")
            return result

        with patch.object(owner, method, side_effect=slow):
            response = await handler(request)
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(json.loads(response.text), result)
        self.assertEqual(len(observed), 1)
        self.assertNotEqual(observed[0][0], threading.get_ident())
        self.assertEqual(observed[0][1], expected_branch)

    async def test_branch_reads(self):
        for action, method in (("list", "listing"), ("load", "load")):
            with self.subTest(action=action):
                request = Request({})
                request.method = "GET"
                request.query = {"run_name": self.run, "action": action, "branch_id": "a" * 32}
                await self.slow_call(chain.WorkingBranches, method,
                    chain._working_branch_command, request, {"id": "a" * 32})

    async def test_switch_autosave_and_branch_changes(self):
        for action, method in (("save", "save"), ("default", "make_default"),
                               ("create", "retry_create")):
            with self.subTest(action=action):
                # A create retry must return its receipt without validating or
                # copying a prefix again. The existing route tests cover new forks.
                await self.slow_call(chain.WorkingBranches, method,
                    chain._working_branch_command,
                    Request({"run_name": self.run, "action": action}), {"id": "main"})

    async def test_empty_branch_actions(self):
        for action, method in (("empty-preview", "empty_branch_preview"),
                               ("delete-empty", "retire_empty"),
                               ("show-original", "show_original")):
            with self.subTest(action=action):
                await self.slow_call(chain.WorkingBranches, method,
                    chain._working_branch_command, Request({"run_name": self.run,
                        "action": action, "branch_id": "main" if action == "show-original" else "a" * 32,
                        "keep_branch_id": "b" * 32}), {"ok": True})

    async def test_preview_preserves_branch_context(self):
        selected = "b" * 32
        await self.slow_call(chain.CheckpointGraphManager, "deletion_preview",
            chain._preview_checkpoint_revision_deletion,
            Request({"run_name": self.run, "branch_id": selected,
                     "scene": 1, "revision": "c" * 32}),
            {"allowed": False, "blockers": ["Protected take"]}, selected)
        self.assertEqual(scope.current_branch(self.run), "main")

    async def test_contended_save_lock_keeps_loop_responsive(self):
        entered, release = threading.Event(), threading.Event()
        expired = []

        def hold_lock():
            with chain.checkpoint_run_lock(self.temp.name, self.run):
                entered.set()
                expired.append(not release.wait(1))

        holder = threading.Thread(target=hold_lock)
        holder.start()
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            # Only a responsive request loop can end this lock contention.
            timer = asyncio.get_running_loop().call_later(.02, release.set)
            try:
                with patch.object(chain.WorkingBranches, "save", return_value={"id": "main"}) as save:
                    response = await chain._working_branch_command(
                        Request({"run_name": self.run, "action": "save"}))
                self.assertEqual(response.status, 200, response.text)
                save.assert_called_once()
                self.assertEqual(expired, [False], "Run lock blocked the request loop")
            finally:
                timer.cancel()
        finally:
            release.set()
            await asyncio.to_thread(holder.join, 1)
        self.assertFalse(holder.is_alive())

    async def test_worker_errors_keep_http_status(self):
        for error, status in ((OSError("disk unavailable"), 500),
                              (ValueError("stale branch"), 400)):
            with self.subTest(error=error), patch.object(chain.WorkingBranches, "load", side_effect=error):
                response = await chain._working_branch_command(
                    Request({"run_name": self.run, "action": "load"}))
                self.assertEqual(response.status, status)
                self.assertEqual(json.loads(response.text)["error"], str(error))
        with patch.object(chain.CheckpointGraphManager, "deletion_preview",
                          side_effect=FileNotFoundError("missing revision")):
            response = await chain._preview_checkpoint_revision_deletion(
                Request({"run_name": self.run, "scene": 1, "revision": "c" * 32}))
            self.assertEqual(response.status, 404)

    async def test_get_cannot_save(self):
        request = Request({})
        request.method = "GET"
        request.query = {"run_name": self.run, "action": "save"}
        with patch.object(chain.WorkingBranches, "save") as save:
            response = await chain._working_branch_command(request)
        self.assertEqual(response.status, 405)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()

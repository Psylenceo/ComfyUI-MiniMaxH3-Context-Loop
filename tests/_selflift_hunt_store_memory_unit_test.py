"""Bundle-buffer lifetime regressions; synthetic CPU tensors and temporary files only."""
from contextlib import contextmanager
import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import weakref

import safetensors.torch

from _selflift_hunt_unit_test import Nested, store_module, torch


@contextmanager
def observe_save_copies():
    """Keep only weak refs, with cyclic collection disabled for the assertion."""
    refs = []
    original_clone = torch.Tensor.clone
    gc_enabled = gc.isenabled()

    def clone(tensor, *args, **kwargs):
        copied = original_clone(tensor, *args, **kwargs)
        refs.append(weakref.ref(copied))
        return copied

    gc.disable()
    try:
        with patch.object(torch.Tensor, "clone", new=clone):
            yield refs
    finally:
        if gc_enabled:
            gc.enable()


class SaveBundleMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="h3-hunt-save-memory-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "take.safetensors"
        self.source = torch.arange(24, dtype=torch.float32).reshape(2, 3, 4)

    def assert_copies_released(self, refs, expected):
        self.assertEqual(len(refs), expected, "The test must observe the actual save copies")
        self.assertTrue(all(ref() is None for ref in refs),
                        "Completed/failed saves must release their copies without cyclic GC")

    def test_repeated_saves_release_copies_and_preserve_shared_nested_tensors(self):
        value = {"samples": Nested([self.source, self.source]),
                 "conditioning": [[self.source, {"frame_idx": 0}]], "tuple": (1, None)}
        with observe_save_copies() as refs:
            for index in range(6):
                store_module.save_bundle(self.path, value)
                # Repeated references to the same source still need only one copy.
                self.assert_copies_released(refs, index + 1)
        restored = store_module.load_bundle(self.path)
        for tensor in restored["samples"].unbind():
            torch.testing.assert_close(tensor, self.source, rtol=0, atol=0)
        torch.testing.assert_close(restored["conditioning"][0][0], self.source, rtol=0, atol=0)
        self.assertEqual(restored["tuple"], (1, None))
        torch.testing.assert_close(self.source.flatten(), torch.arange(24, dtype=torch.float32))
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_encoding_failures_release_partial_copies_with_retained_tracebacks(self):
        store_module.save_bundle(self.path, {"samples": self.source})
        original = self.path.read_bytes()
        for invalid, expected_error in ((object(), TypeError), (float("nan"), ValueError)):
            with self.subTest(error=expected_error.__name__), observe_save_copies() as refs:
                errors = []
                try:
                    # The tensor is encoded before the unsupported object/non-finite value.
                    store_module.save_bundle(self.path, [self.source, invalid])
                except expected_error as error:
                    errors.append(error)
                self.assertEqual(len(errors), 1)
                self.assertIsNotNone(errors[0].__traceback__)
                self.assert_copies_released(refs, 1)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_io_failures_release_copies_and_preserve_checkpoint_with_retained_tracebacks(self):
        store_module.save_bundle(self.path, {"samples": self.source})
        original = self.path.read_bytes()
        original_save = safetensors.torch.save_file

        def fail(*args, **kwargs):
            raise OSError("simulated save failure")

        def fail_after_write(*args, **kwargs):
            original_save(*args, **kwargs)
            fail()

        targets = ((Path, "mkdir", fail),
                   (safetensors.torch, "save_file", fail_after_write),
                   (store_module.os, "fsync", fail),
                   (store_module.os, "replace", fail))
        for target, name, replacement in targets:
            with self.subTest(stage=name), observe_save_copies() as refs:
                errors = []
                with patch.object(target, name, new=replacement):
                    try:
                        store_module.save_bundle(self.path, {"samples": self.source + 1})
                    except OSError as error:
                        errors.append(error)
                self.assertEqual(len(errors), 1)
                self.assertIsNotNone(errors[0].__traceback__)
                self.assert_copies_released(refs, 1)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_interrupted_save_also_releases_copies(self):
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt("simulated interruption")

        with observe_save_copies() as refs:
            errors = []
            with patch.object(safetensors.torch, "save_file", new=interrupt):
                try:
                    store_module.save_bundle(self.path, {"samples": self.source})
                except KeyboardInterrupt as error:
                    errors.append(error)
            self.assertEqual(len(errors), 1)
            self.assert_copies_released(refs, 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.root.glob("*.tmp")), [])


class LoadBundleMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="h3-hunt-load-memory-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "source.safetensors"
        self.source = torch.arange(24, dtype=torch.float32).reshape(2, 3, 4)

    def test_loaded_tensors_own_storage_and_shared_values_are_copied_once(self):
        store_module.save_bundle(self.path, {
            "samples": Nested([self.source, self.source]),
            "conditioning": [[self.source, {"frame_idx": 0}]],
            "seed": 2 ** 64 - 1, "tuple": (None, True),
        })
        original_open = safetensors.safe_open
        mapped_refs = []

        class ObservedOpen:
            def __init__(self, *args, **kwargs):
                self.handle = original_open(*args, **kwargs)
            def __enter__(self):
                self.handle.__enter__()
                return self
            def __exit__(self, *args):
                return self.handle.__exit__(*args)
            def metadata(self):
                return self.handle.metadata()
            def get_tensor(self, key):
                tensor = self.handle.get_tensor(key)
                mapped_refs.append(weakref.ref(tensor))
                return tensor

        with patch.object(safetensors, "safe_open", ObservedOpen):
            loaded = store_module.load_bundle(self.path)
        self.assertEqual(len(mapped_refs), 1, "Repeated tree references load/copy each tensor only once")
        self.assertTrue(all(ref() is None for ref in mapped_refs),
                        "Returned tensors must not retain file-backed storage")
        first, second = loaded["samples"].unbind()
        self.assertIs(first, second)
        self.assertIs(first, loaded["conditioning"][0][0])
        torch.testing.assert_close(first, self.source, rtol=0, atol=0)
        self.assertEqual(loaded["seed"], 2 ** 64 - 1)
        self.assertEqual(loaded["tuple"], (None, True))
        self.path.unlink()
        torch.testing.assert_close(first, self.source, rtol=0, atol=0)

    @unittest.skipUnless(Path("/proc/self/maps").is_file(), "Linux mapping inspection")
    def test_no_file_mapping_survives_while_loaded_output_is_cached(self):
        store_module.save_bundle(self.path, {"samples": self.source})
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            loaded = [store_module.load_bundle(self.path) for _ in range(3)]
            self.assertFalse(str(self.path) in Path("/proc/self/maps").read_text(),
                             "Cleanup must not depend on dropping cached outputs or cyclic GC")
            for item in loaded:
                torch.testing.assert_close(item["samples"], self.source, rtol=0, atol=0)
        finally:
            if was_enabled:
                gc.enable()


if __name__ == "__main__":
    unittest.main()

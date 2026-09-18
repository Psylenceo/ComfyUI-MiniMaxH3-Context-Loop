"""Stage retirement safety and ordering; CPU fixtures, no live ComfyUI/files."""
import ast
import importlib
import importlib.util
import sys
import types
import unittest
from unittest.mock import Mock, patch

import _selflift_unit_test as fixtures
from _selflift_hires_unit_test import Checkpoint

torch = fixtures.torch
memory = importlib.import_module(fixtures.PACKAGE + ".selflift_runtime.memory")


class Interrupted(Exception):
    pass


def loaded(model, *, dynamic=True):
    model.is_dynamic = lambda: dynamic
    return types.SimpleNamespace(model=model, model_unload=Mock(return_value=True))


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.registry = []
        for p in (
            patch.object(memory.mm, "current_loaded_models", self.registry, create=True),
            patch.object(memory.mm, "throw_exception_if_processing_interrupted", create=True),
            patch.object(memory, "log_memory"),
            patch.object(memory.torch.cuda, "synchronize"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_only_retired_clone_unloaded_and_registry_kept_in_place(self):
        low, high, unrelated = [Checkpoint(n) for n in ("low", "high", "other")]
        records = [loaded(m) for m in (low.clone(), high, unrelated)]
        self.registry.extend(records)
        with self.assertLogs(level="INFO") as log:
            count = memory.release_stage_models([low], keep_models=[high], stage="test")
        self.assertEqual(count, 1)
        self.assertIs(memory.mm.current_loaded_models, self.registry)
        self.assertEqual(self.registry, records[1:])
        records[0].model_unload.assert_called_once_with()
        records[1].model_unload.assert_not_called()
        records[2].model_unload.assert_not_called()
        memory.torch.cuda.synchronize.assert_not_called()
        self.assertEqual(memory.log_memory.call_count, 2)
        self.assertTrue(all(c.kwargs["force"] for c in memory.log_memory.call_args_list))
        self.assertIn("unloaded=1", log.output[-1])

    def test_same_underlying_checkpoint_is_protected(self):
        low = Checkpoint("low")
        record = loaded(low)
        self.registry.append(record)
        self.assertEqual(memory.release_stage_models([low.clone()], keep_models=[low.clone()], stage="test"), 0)
        record.model_unload.assert_not_called()

    def test_finishing_model_dependencies_are_protected_even_with_cycles(self):
        low, high = Checkpoint("low"), Checkpoint("high")
        low.model_patches_models = lambda: [high]
        high.model_patches_models = lambda: [low]
        record = loaded(low)
        self.registry.append(record)
        self.assertEqual(memory.release_stage_models([low], keep_models=[high], stage="test"), 0)
        record.model_unload.assert_not_called()

    def test_classic_and_unloaded_models_are_skipped(self):
        low, absent = Checkpoint("low"), Checkpoint("absent")
        record = loaded(low, dynamic=False)
        self.registry.append(record)
        with self.assertLogs(level="INFO") as log:
            self.assertEqual(memory.release_stage_models([low, absent], stage="test"), 0)
        record.model_unload.assert_not_called()
        self.assertIn("non_dynamic=1; not_loaded=1", log.output[-1])

    def test_cuda_fenced_before_unload_and_cancel_checked_again(self):
        low = Checkpoint("low")
        low.load_device = "cuda:0"
        record = loaded(low)
        self.registry.append(record)
        events = []
        memory.mm.throw_exception_if_processing_interrupted.side_effect = lambda: events.append("check")
        memory.torch.cuda.synchronize.side_effect = lambda device: events.append(str(device))
        record.model_unload.side_effect = lambda: events.append("unload") or True
        memory.release_stage_models([low], stage="test")
        self.assertEqual(events, ["check", "cuda:0", "check", "check", "unload"])

    def test_interrupt_before_or_after_fence_never_unloads(self):
        low = Checkpoint("low")
        low.load_device = "cuda:0"
        record = loaded(low)
        self.registry.append(record)
        for sequence in ([Interrupted()], [None, Interrupted()], [None, None, Interrupted()]):
            with self.subTest(sequence=sequence):
                memory.mm.throw_exception_if_processing_interrupted.side_effect = sequence
                with self.assertRaises(Interrupted):
                    memory.release_stage_models([low], stage="test")
                record.model_unload.assert_not_called()
                self.assertEqual(self.registry, [record])

    def test_sync_failure_does_not_attempt_destructive_cleanup(self):
        low = Checkpoint("low")
        low.load_device = "cuda:0"
        record = loaded(low)
        self.registry.append(record)
        memory.torch.cuda.synchronize.side_effect = RuntimeError("CUDA sync failed")
        with self.assertRaisesRegex(RuntimeError, "CUDA sync failed"):
            memory.release_stage_models([low], stage="test")
        record.model_unload.assert_not_called()
        self.assertEqual(self.registry, [record])

    def test_failed_or_partial_unload_is_not_removed_from_registry(self):
        record = loaded(Checkpoint("low"))
        self.registry.append(record)
        record.model_unload.return_value = False
        self.assertEqual(memory.release_stage_models([record.model], stage="test"), 0)
        self.assertEqual(self.registry, [record])
        record.model_unload.side_effect = RuntimeError("unload failed")
        with self.assertRaisesRegex(RuntimeError, "unload failed"):
            memory.release_stage_models([record.model], stage="test")
        self.assertEqual(self.registry, [record])

    def test_only_successfully_unloaded_entry_removed_on_later_interrupt(self):
        records = [loaded(Checkpoint(n)) for n in ("one", "two")]
        self.registry.extend(records)
        memory.mm.throw_exception_if_processing_interrupted.side_effect = [None, None, None, Interrupted()]
        with self.assertRaises(Interrupted):
            memory.release_stage_models([r.model for r in records], stage="test")
        self.assertEqual(self.registry, [records[1]])
        records[1].model_unload.assert_not_called()


class StageTests(unittest.TestCase):
    setUp = fixtures.SelfLiftTests.setUp

    def run_stage(self, **kwargs):
        return fixtures.runtime.progressive_sample(
            kwargs.pop("model", Checkpoint("low")), [], [], object(), self.latent,
            fixtures.Euler(), self.sigmas, 42, 1., 2, .5, 0., .5, 1., "nearest",
            model_hires=kwargs.pop("model_hires", Checkpoint("high")),
            latent_lifter=kwargs.pop("latent_lifter", fixtures.lift), **kwargs)

    def test_default_off_and_enabled_produce_identical_outputs(self):
        with patch.object(memory, "release_stage_models") as release:
            baseline = self.run_stage()
            release.assert_not_called()
            result = self.run_stage(cleanup_between_stages=True)
            release.assert_called_once()
            self.assertEqual(release.call_args.args[0][0].name, "low")
            self.assertEqual(release.call_args.kwargs["keep_models"][0].name, "high")
        for a, b in zip(baseline["samples"].unbind(), result["samples"].unbind()):
            torch.testing.assert_close(a, b, rtol=0, atol=0)
        torch.testing.assert_close(baseline[fixtures.state.LOW_CARRY], result[fixtures.state.LOW_CARRY], rtol=0, atol=0)

    def test_hunt_low_pass_not_cleaned_but_resume_cleaned_before_lift(self):
        with patch.object(memory, "release_stage_models") as release:
            middle = self.run_stage(stop_after_low=True, cleanup_between_stages=True)
            release.assert_not_called()
            events = []
            release.side_effect = lambda *a, **k: events.append("cleanup")
            def lifter(z, hw, temporal_split=None):
                events.append("lift")
                return fixtures.lift(z, hw, temporal_split=temporal_split)
            fixtures.CALLS.clear()
            self.run_stage(handoff=middle, cleanup_between_stages=True, latent_lifter=lifter)
        self.assertEqual(events, ["cleanup", "lift"])
        self.assertEqual([c["shape"][-2:] for c in fixtures.CALLS], [(8, 12)])

    def test_no_cleanup_when_low_sampling_fails(self):
        with patch.object(memory, "release_stage_models") as release, \
                patch.object(fixtures.runtime.comfy.samplers, "sample", side_effect=Interrupted()):
            with self.assertRaises(Interrupted):
                self.run_stage(cleanup_between_stages=True)
            release.assert_not_called()

    def test_cleanup_failure_stops_before_lifter_and_high_pass(self):
        with patch.object(memory, "release_stage_models", side_effect=Interrupted()), \
                patch.object(fixtures.runtime.selflift, "paired_lifts") as lifter:
            with self.assertRaises(Interrupted):
                self.run_stage(cleanup_between_stages=True)
        lifter.assert_not_called()
        self.assertEqual(len(fixtures.CALLS), 1)

    def test_project_widget_is_optional_and_does_not_change_carry_signature(self):
        project = fixtures.nodes.MiniMaxH3SelfLiftProject
        with patch.object(fixtures.nodes, "upscaler_models", return_value=["none"]):
            self.assertFalse(project.INPUT_TYPES()["optional"]["cleanup_between_stages"][1]["default"])
        old_plan = {"shots": []}
        default, _ = project().configure(old_plan)
        enabled, status = project().configure(old_plan, True, "test", 2, cleanup_between_stages=True)
        self.assertEqual(old_plan, {"shots": []})
        self.assertFalse(default[fixtures.nodes.SETTINGS_KEY]["cleanup_between_stages"])
        self.assertIn("cleanup ON", status)
        settings = enabled[fixtures.nodes.SETTINGS_KEY]
        self.assertEqual(fixtures.state.settings_signature(settings),
                         fixtures.state.settings_signature({**settings, "cleanup_between_stages": False}))

    def test_chain_wrapper_forwards_cleanup_to_runtime_and_upscaler(self):
        upscaler = importlib.import_module(fixtures.PACKAGE + ".selflift_runtime.h3_upscaler")
        def lifter(z, hw, name, **kwargs):
            self.assertTrue(kwargs.pop("cleanup_after"))
            return fixtures.lift(z, hw, **kwargs)
        with patch.object(memory, "release_stage_models") as release, \
                patch.object(fixtures.nodes, "upscaler_models", return_value=["h3_test.safetensors"]), \
                patch.object(upscaler, "learned_latent_lift", side_effect=lifter, create=True) as lift:
            fixtures.nodes.MiniMaxH3ChainSelfLiftSampler().sample(
                {"plan": {fixtures.nodes.SETTINGS_KEY: {**self.settings, "cleanup_between_stages": True}}},
                Checkpoint("low"), [], object(), self.latent, fixtures.Euler(), self.sigmas, 42,
                model_hires=Checkpoint("high"))
        release.assert_called_once()
        lift.assert_called_once()


class LoopBoundaryTests(unittest.TestCase):
    def setUp(self):
        # Exercise the actual boundary function without importing the UI/routes.
        source = ast.parse((fixtures.ROOT / "chain_nodes.py").read_text())
        function = next(n for n in source.body if isinstance(n, ast.FunctionDef)
                        and n.name == "_release_loop_boundary_resources")
        self.events, self.registry = [], []
        namespace = {"__package__": fixtures.PACKAGE, "sys": sys,
                     "LOOP_MEMORY_POLICIES": ("off", "unload_models", "fresh_scene"),
                     "_LOG": Mock(), "gc": types.SimpleNamespace(collect=Mock(return_value=0))}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "chain_nodes.py", "exec"), namespace)
        self.cleanup = namespace[function.name]
        self.ram = Mock(side_effect=lambda *a, **k: self.events.append("cache") or 0)
        self.generic = Mock(side_effect=lambda: self.events.append("generic"))
        for p in (
            patch.object(memory.mm, "current_loaded_models", self.registry, create=True),
            patch.object(memory.mm, "throw_exception_if_processing_interrupted", create=True),
            patch.object(memory.mm, "unload_all_models", self.generic, create=True),
            patch.object(memory.mm, "soft_empty_cache", create=True),
            patch.object(memory.mm, "cleanup_models_gc", create=True),
            patch.object(memory.mm, "free_pins", return_value=0, create=True),
            patch.dict(sys.modules, {"comfy.memory_management": types.SimpleNamespace(extra_ram_release=self.ram)}),
            patch.object(memory, "log_memory"),
            patch.object(memory.torch.cuda, "synchronize"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_dynamic_release_before_cache_eviction_and_classic_fallback(self):
        dynamic, classic = loaded(Checkpoint("vae")), loaded(Checkpoint("classic"), dynamic=False)
        dynamic.model_unload.side_effect = lambda: self.events.append("dynamic") or True
        self.registry.extend([dynamic, classic])
        result = self.cleanup("fresh_scene", 3)
        self.assertEqual(self.events, ["dynamic", "cache", "generic"])
        dynamic.model_unload.assert_called_once_with()  # full detach, no memory budget
        classic.model_unload.assert_not_called()
        self.assertEqual(self.registry, [classic])
        self.assertEqual(result["dynamic_models"], 1)

    def test_unload_policy_uses_stage_release_without_evicting_outputs(self):
        record = loaded(Checkpoint("high"))
        self.registry.append(record)
        self.cleanup("unload_models", 1)
        record.model_unload.assert_called_once_with()
        self.ram.assert_not_called()
        self.assertEqual(self.registry, [])

    def test_off_does_not_touch_models_or_caches(self):
        self.cleanup("off", 1)
        memory.mm.throw_exception_if_processing_interrupted.assert_not_called()
        self.generic.assert_not_called()
        self.ram.assert_not_called()

    def test_cancel_or_cuda_failure_stops_before_cache_eviction(self):
        record = loaded(Checkpoint("high"))
        record.model.load_device = "cuda:0"
        self.registry.append(record)
        for error in (Interrupted(), RuntimeError("CUDA sync failed")):
            with self.subTest(error=type(error)):
                memory.torch.cuda.synchronize.side_effect = error
                with self.assertRaises(type(error)):
                    self.cleanup("fresh_scene", 1)
                record.model_unload.assert_not_called()
                self.generic.assert_not_called()
                self.ram.assert_not_called()


class UpscalerRetirementTests(unittest.TestCase):
    def setUp(self):
        # Load the real wrapper without ComfyUI/GPU model initialization.
        stub = types.ModuleType("comfy.ldm.minimax.vae")
        stub.LATENTS_MEAN, stub.LATENTS_STD = [0.] * 24, [1.] * 24
        folder = types.ModuleType("folder_paths")
        folder.folder_names_and_paths = {"latent_upscale_models": ()}
        name = fixtures.PACKAGE + ".selflift_runtime._upscaler_memory_test"
        spec = importlib.util.spec_from_file_location(name, fixtures.ROOT / "selflift_runtime/h3_upscaler.py")
        self.upscaler = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"comfy.ldm.minimax.vae": stub, "folder_paths": folder}):
            spec.loader.exec_module(self.upscaler)

    def test_cleanup_after_successful_return_not_during_inference(self):
        patcher, output = object(), torch.ones(1)
        self.upscaler._model_cache[("test", "cpu")] = patcher
        events = []
        def infer(*args):
            events.append("inference returned")
            return output
        with patch.object(self.upscaler, "_learned_latent_lift", side_effect=infer), \
                patch.object(memory, "release_stage_models", side_effect=lambda *a, **k: events.append("cleanup")) as release:
            result = self.upscaler.learned_latent_lift(None, (8, 12), "test", device="cpu", cleanup_after=True)
        self.assertIs(result, output)
        self.assertEqual(events, ["inference returned", "cleanup"])
        release.assert_called_once_with([patcher], stage="lift-to-high")

    def test_default_off_and_failed_lift_never_cleanup(self):
        self.upscaler._model_cache[("test", "cpu")] = object()
        with patch.object(self.upscaler, "_learned_latent_lift") as infer, \
                patch.object(memory, "release_stage_models") as release:
            self.upscaler.learned_latent_lift(None, (8, 12), "test", device="cpu")
            for error in (Interrupted(), RuntimeError("OOM")):
                infer.side_effect = error
                with self.assertRaises(type(error)):
                    self.upscaler.learned_latent_lift(None, (8, 12), "test", device="cpu", cleanup_after=True)
            release.assert_not_called()


if __name__ == "__main__":
    unittest.main()

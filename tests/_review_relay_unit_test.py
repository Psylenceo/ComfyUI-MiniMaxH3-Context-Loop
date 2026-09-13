"""Live relay: original ownership/branch, no disk discovery, exact decisions."""
import asyncio
import copy
import json
import tempfile
import time
from unittest.mock import patch

from _review_gate_unit_test import chain


class Request:
    def __init__(self, body=None, **query):
        self.body = body
        self.query = query
        self.headers = {}

    async def json(self):
        return self.body


def parsed(response):
    return json.loads(response.text)


async def main():
    loop = asyncio.get_running_loop()
    with tempfile.TemporaryDirectory(prefix="h3-relay-test-") as root:
        chain._output_root = lambda: root
        chain._PENDING_REVIEWS.clear()
        chain._ACTIVE_CANDIDATE_BATCHES.clear()
        owner = "relay-original-workflow-owner"
        lock = chain.claim_project_ownership(root, "film", owner, "Original workflow")
        proof = {"owner_id": owner, "epoch": lock["epoch"]}
        branch = "b" * 32
        plan = {"run_name": "film", "_branch_id": branch, "_project_ownership": proof,
                "shots": [{"prompt": "Must stay unchanged", "seed": 77}]}
        candidates = [{"segment": {"revision": f"{n:032x}", "seed": 2**64 - n,
            "raw_frames": 73, "scene_prompt": f"Saved prompt {n}"},
            "video": {"filename": f"take_{n}.mp4", "type": "output", "subfolder": "film"},
            "has_audio": True} for n in range(1, 11)]
        public = dict(run_name="film", _branch_id=branch, node_id="99:10", clip_index=2,
            shot_id="scene_two", candidate_count=10, candidate_generation_complete=True,
            pending_decision=True, candidates=chain._review_public_candidates(candidates))

        def install(token="live", **updates):
            entry = dict(public=dict(public), plan=plan, future=loop.create_future(),
                         loop=loop, candidates=candidates, current_seed=77, current_length=73)
            entry.update(updates)
            chain._PENDING_REVIEWS[token] = entry
            return entry

        def decision(token="live", **updates):
            body = dict(token=token, run_name="film", branch_id=branch, clip_index=2,
                        action="approve", candidate_revision=candidates[4]["segment"]["revision"])
            body.update(updates)
            return Request(body)

        entry = install()
        before = copy.deepcopy(plan)
        with patch.object(chain.os, "listdir", side_effect=AssertionError("No directory scans")), \
                patch.object(chain, "_load_checkpoint_revision", side_effect=AssertionError("No checkpoint IO on GET")):
            response = await chain._list_review_relay(Request())
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
            listing = parsed(response)
            assert listing["selected"] is None
            assert listing["reviews"][0]["generated_count"] == 10
            assert listing["reviews"][0]["branch_id"] == branch
            assert "scene_prompt" not in response.text and owner not in response.text
            selected = parsed(await chain._list_review_relay(Request(
                token="live", candidate_revision=candidates[4]["segment"]["revision"])))['selected']
            assert selected["candidate"]["number"] == 5
            assert selected["candidate"]["seed"] == str(2**64 - 5)
            assert selected["candidate"]["scene_prompt"] == "Saved prompt 5"
            assert "scene_prompt" not in selected["candidates"][0]
            assert owner not in json.dumps(selected)

        for malformed in (None, [], "bad"):
            assert (await chain._submit_review_relay(Request(malformed))).status == 400
        for changes, code in (({"run_name": "another"}, 409), ({"branch_id": "main"}, 409),
                ({"clip_index": 1}, 409), ({"action": "reroll"}, 400),
                ({"candidate_revision": ""}, 400), ({"candidate_revision": "missing"}, 400),
                ({"candidate_revisions": ["missing"]}, 400)):
            # Valid selections reach the shared checkpoint loader; malformed
            # identity/actions must be rejected before any checkpoint access.
            with patch.object(chain, "_load_checkpoint_revision", return_value=(
                    {"segment": candidates[4]["segment"]}, "test")):
                result = await chain._submit_review_relay(decision(**changes))
            assert result.status == code, (changes, result.status, result.text)
            assert not entry["future"].done() and not entry.get("decision_submitted")

        # Another canvas has no ownership proof: the normal route stays denied.
        assert (await chain._submit_review_decision(decision())).status == 423
        queued = []
        class DelayedLoop:
            def call_soon_threadsafe(self, callback): queued.append(callback)
        entry["loop"] = DelayedLoop()
        loaded = []
        def checkpoint(run, scene, revision):
            loaded.append((run, scene, chain.current_branch(run), revision))
            return {"segment": candidates[4]["segment"]}, "test"
        with patch.object(chain, "_load_checkpoint_revision", side_effect=checkpoint):
            result = await chain._submit_review_relay(decision(
                scene_prompt="MUST NOT REPLACE", seed="1", _project_ownership={"owner_id": "intruder"}))
            assert result.status == 200, result.text
            assert parsed(result)["candidate_number"] == 5
            assert parsed(result)["kept_candidate_count"] == 10, "Omitting keep marks preserves all"
            assert loaded[0][2] == branch, "The pending job owns the branch scope"
            assert len(queued) == 1 and not entry["future"].done()
            assert (await chain._submit_review_relay(decision(action="stop"))).status == 409
            request = decision(action="stop")
            request.headers = {"X-H3-Workflow-Owner": owner, "X-H3-Ownership-Epoch": str(proof["epoch"])}
            assert (await chain._submit_review_decision(request)).status == 409
        queued.pop()()
        assert entry["future"].result()["action"] == "approve"
        assert "scene_prompt" not in entry["future"].result()
        assert plan == before
        assert chain.ownership_status(root, "film", owner)["owned_by_requester"]

        # Keep only selected take, using the real Gate validation.
        entry = install("stop")
        with patch.object(chain, "_load_checkpoint_revision", side_effect=checkpoint):
            result = await chain._submit_review_relay(decision("stop", action="stop", candidate_revisions=[]))
        assert result.status == 200 and parsed(result)["kept_candidate_count"] == 1
        await asyncio.sleep(0)
        assert entry["future"].result()["action"] == "stop"

        # Review-each-candidate advances through the same live executor. The
        # relay cannot substitute the prompt from its unrelated canvas.
        entry = install("next")
        entry["public"].update(candidate_count=11, review_each_candidate=True,
                               scene_prompt="Original job prompt")
        with patch.object(chain, "_plan_with_review_revision", return_value=plan):
            result = await chain._submit_review_relay(decision(
                "next", action="next_candidate", scene_prompt="Unrelated canvas prompt"))
        assert result.status == 200, result.text
        await asyncio.sleep(0)
        next_decision = entry["future"].result()
        assert next_decision["action"] == "next_candidate"
        assert next_decision["scene_prompt"] == "Original job prompt"
        assert next_decision["candidate_batch"]["target"] == 11

        # Scheduling failure is reported, not mistaken for an approval. The
        # reservation is cleared so a recovered live execution can be retried.
        class ClosedLoop:
            def call_soon_threadsafe(self, callback): raise RuntimeError("Loop closed")
        entry = install("closed", loop=ClosedLoop())
        with patch.object(chain, "_load_checkpoint_revision", side_effect=checkpoint):
            result = await chain._submit_review_relay(decision("closed"))
        assert result.status == 409 and not entry.get("decision_submitted")
        entry["future"].cancel()

        # A takeover fences the original job too. The relay cannot bypass it.
        entry = install("fenced")
        chain.claim_project_ownership(root, "film", "relay-new-workflow-owner", "Other workflow", force=True)
        assert (await chain._submit_review_relay(decision("fenced"))).status == 423
        assert not entry["future"].done()
        entry["future"].cancel()
        assert (await chain._submit_review_relay(decision("fenced"))).status == 409
        chain._PENDING_REVIEWS.clear()
        assert (await chain._submit_review_relay(decision())).status == 409

        # Growing batches are visible but never mistaken for a waiting future.
        chain._ACTIVE_CANDIDATE_BATCHES["growing"] = {
            "updated": time.monotonic(), "public": dict(public, candidate_batch_active=True)}
        listing = parsed(await chain._list_review_relay(Request(token="growing")))
        assert len(listing["reviews"]) == 1 and not listing["selected"]["actionable"]
        assert (await chain._submit_review_relay(decision("growing"))).status == 409
        chain._ACTIVE_CANDIDATE_BATCHES["growing"]["updated"] -= 22000
        assert parsed(await chain._list_review_relay(Request()))["reviews"] == []
        chain._ACTIVE_CANDIDATE_BATCHES.clear()

    cls = chain.CHAIN_NODE_CLASS_MAPPINGS["MiniMaxH3ReviewRelay"]
    assert cls.INPUT_TYPES() == {"required": {}} and cls.RETURN_TYPES == ()
    assert not getattr(cls, "OUTPUT_NODE", False) and cls().noop() == ()
    print("Review relay: memory-only discovery, 10 candidates, uint64 seeds, original ownership/branch, double decisions and ended jobs pass")


asyncio.run(main())

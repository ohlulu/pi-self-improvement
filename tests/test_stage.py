import json
import tempfile
import unittest
from pathlib import Path

from pi_self_improvement import detect, stage, state
from pi_self_improvement.model import Evidence, ParseCounts
from pi_self_improvement.redact import Redactor
from pi_self_improvement.route import Proposal


def evidence(ref="sessions/s1.jsonl", line=4, excerpt="boom", timestamp="2026-01-05T09:00:00Z"):
    return Evidence(
        source=detect.FAILURE,
        path=ref,
        line=line,
        excerpt=excerpt,
        timestamp=timestamp,
        session_id="s1",
    )


def signal(line=4, timestamp="2026-01-05T09:00:00Z"):
    return detect.Signal(
        kind=detect.FAILURE,
        subject="demo-cli",
        evidence=evidence(line=line, timestamp=timestamp),
        detail={},
    )


def staged(target="demo-cli", *, regression=False, previous_runs=0, lines=(4,), summary="it failed"):
    proposal = Proposal(
        route="tool", target=target, signals=[signal(line=n) for n in lines], summary=summary
    )
    return state.Staged(
        proposal=proposal,
        id=state.proposal_id(proposal),
        regression=regression,
        previous_runs=previous_runs,
    )


class StageTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "output"
        self.addCleanup(self._tmp.cleanup)


class TestStagingOutputs(StageTestCase):
    """AC-027."""

    def test_all_three_outputs_exist(self):
        result = stage.write_run(self.root, [staged()], run_id="R1", counts=ParseCounts(files=1))

        self.assertTrue((self.root / "runs" / "R1.json").is_file())
        self.assertTrue((self.root / "review-packets" / "R1.md").is_file())
        self.assertEqual(len(list((self.root / "proposals" / "R1").glob("*.json"))), 1)
        self.assertTrue(all(path.is_file() for path in result.paths))

    def test_a_proposal_file_is_named_by_its_id(self):
        item = staged()

        stage.write_run(self.root, [item], run_id="R1")

        self.assertTrue((self.root / "proposals" / "R1" / f"{item.id}.json").is_file())

    def test_the_proposal_json_carries_evidence_and_target(self):
        stage.write_run(self.root, [staged(lines=(4, 9))], run_id="R1")
        payload = json.loads(
            next((self.root / "proposals" / "R1").glob("*.json")).read_text(encoding="utf-8")
        )

        self.assertEqual(payload["route"], "tool")
        self.assertEqual(payload["target"], "demo-cli")
        self.assertEqual(len(payload["evidence"]), 2)
        self.assertEqual(payload["evidence"][0]["line"], 4)

    def test_the_run_metadata_lists_every_proposal_and_the_counts(self):
        stage.write_run(
            self.root,
            [staged("a"), staged("b")],
            run_id="R1",
            counts=ParseCounts(files=7, root_sessions=5, subagent_sessions=2),
        )
        payload = json.loads((self.root / "runs" / "R1.json").read_text(encoding="utf-8"))

        self.assertEqual(len(payload["proposals"]), 2)
        self.assertEqual(payload["counts"]["files"], 7)
        self.assertEqual(payload["run_id"], "R1")

    def test_a_run_with_no_proposals_still_writes_metadata_and_a_packet(self):
        stage.write_run(self.root, [], run_id="R1", counts=ParseCounts())

        self.assertTrue((self.root / "runs" / "R1.json").is_file())
        self.assertTrue((self.root / "review-packets" / "R1.md").is_file())


class TestOutputRootConfinement(StageTestCase):
    def test_nothing_is_written_outside_the_output_root(self):
        stage.write_run(self.root, [staged()], run_id="R1")
        outside = [
            path
            for path in Path(self._tmp.name).rglob("*")
            if path.is_file() and self.root not in path.parents
        ]

        self.assertEqual(outside, [])

    def test_a_run_id_that_escapes_the_root_is_refused(self):
        with self.assertRaises(stage.OutputRootEscape):
            stage.write_run(self.root, [], run_id="../../escape")

    def test_traversal_that_stays_inside_the_root_is_allowed_and_normalized(self):
        """One `..` from `runs/` lands back inside the root, so it is not an escape.

        Pinned because the first version of this test asserted an exception here
        and passed for the wrong reason would have hidden a real hole."""
        stage.write_run(self.root, [], run_id="../inside")

        self.assertTrue((self.root / "inside.json").is_file())
        self.assertTrue(self.root.resolve() in (self.root / "inside.json").resolve().parents)


class TestPacketOrdering(StageTestCase):
    """AC-028: recurring before new, regressions before everything."""

    def test_recurring_proposals_come_before_new_ones(self):
        packet = stage.render_packet(
            [staged("old", previous_runs=3), staged("new")], "R1", None, False
        )

        self.assertLess(packet.index("## Recurring"), packet.index("## New"))
        self.assertLess(packet.index("`tool:old`"), packet.index("`tool:new`"))

    def test_regressions_come_first(self):
        packet = stage.render_packet(
            [staged("back", regression=True), staged("old", previous_runs=2), staged("new")],
            "R1",
            None,
            False,
        )

        self.assertLess(packet.index("## Regressions"), packet.index("## Recurring"))

    def test_a_recurring_proposal_says_how_many_previous_runs(self):
        """AC-030."""
        packet = stage.render_packet([staged("old", previous_runs=3)], "R1", None, False)

        self.assertIn("also flagged in 3 previous run(s)", packet)

    def test_a_first_regression_is_not_described_as_recurring(self):
        """AC-049: a first regression must not read as accumulated recurrence."""
        packet = stage.render_packet([staged("back", regression=True)], "R1", None, False)

        self.assertIn("regressed after being resolved", packet)
        self.assertNotIn("previous run(s)", packet)

    def test_a_section_with_no_members_is_omitted(self):
        packet = stage.render_packet([staged("new")], "R1", None, False)

        self.assertNotIn("## Regressions", packet)
        self.assertNotIn("## Recurring", packet)

    def test_evidence_is_listed_and_long_lists_are_summarized(self):
        packet = stage.render_packet([staged(lines=tuple(range(1, 9)))], "R1", None, False)

        self.assertIn("Evidence (8):", packet)
        self.assertIn("…and 3 more", packet)


class TestLocalOnlyPropagation(StageTestCase):
    """AC-006 — the deferred half owed by T018.

    Asserting the flag on the Redactor is not enough: what matters is that a
    packet produced with --full announces it, or someone shares one.
    """

    def test_local_only_reaches_all_three_outputs(self):
        stage.write_run(self.root, [staged()], run_id="R1", redactor=Redactor(full=True))

        run = json.loads((self.root / "runs" / "R1.json").read_text(encoding="utf-8"))
        proposal = json.loads(
            next((self.root / "proposals" / "R1").glob("*.json")).read_text(encoding="utf-8")
        )
        packet = (self.root / "review-packets" / "R1.md").read_text(encoding="utf-8")

        self.assertTrue(run["local_only"])
        self.assertTrue(proposal["local_only"])
        self.assertIn("LOCAL ONLY", packet)

    def test_a_default_run_is_not_marked_local_only(self):
        stage.write_run(self.root, [staged()], run_id="R1", redactor=Redactor())

        run = json.loads((self.root / "runs" / "R1.json").read_text(encoding="utf-8"))
        packet = (self.root / "review-packets" / "R1.md").read_text(encoding="utf-8")

        self.assertFalse(run["local_only"])
        self.assertNotIn("LOCAL ONLY", packet)


class TestWarningsAndCounts(StageTestCase):
    def test_a_warning_reaches_run_metadata_and_the_packet(self):
        """Half of AC-033; stderr is the CLI's half."""
        stage.write_run(self.root, [], run_id="R1", warnings=["no tool calls parsed"])

        run = json.loads((self.root / "runs" / "R1.json").read_text(encoding="utf-8"))
        packet = (self.root / "review-packets" / "R1.md").read_text(encoding="utf-8")

        self.assertEqual(run["warnings"], ["no tool calls parsed"])
        self.assertIn("no tool calls parsed", packet)

    def test_the_counts_block_is_rendered_in_the_packet(self):
        counts = ParseCounts(files=3, root_sessions=2, subagent_sessions=1, branch_points=4)
        counts.skip("label", 5)

        packet = stage.render_packet([], "R1", counts, False)

        self.assertIn("## Parser self-check", packet)
        self.assertIn("root_sessions: 2", packet)
        self.assertIn("label=5", packet)


if __name__ == "__main__":
    unittest.main()

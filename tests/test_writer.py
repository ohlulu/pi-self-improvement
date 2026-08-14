import io
import json
import tempfile
import unittest
from pathlib import Path

from pi_self_improvement import cli, writer


def triage_payload(*entries, notes=""):
    return {"entries": list(entries), "notes": notes}


def entry(key="tool:demo-cli", verdict="act", reason="fails a lot", fix="pin the version"):
    return {"id": "abc123", "key": key, "verdict": verdict, "reason": reason, "suggested_fix": fix}


class WriterTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "output"
        self.addCleanup(self._tmp.cleanup)

    def write_triage(self, payload, **kwargs):
        return writer.write_triage(self.root, writer.parse_triage(payload), **kwargs)

    def queue_text(self):
        return (self.root / writer.QUEUE_FILE).read_text(encoding="utf-8")

    def decision(self, key):
        path = self.root / writer.DECISIONS_DIR / f"{writer.logical_id(key)}.json"
        return json.loads(path.read_text(encoding="utf-8"))


class TestLogicalId(unittest.TestCase):
    """AC-051 — the same incident must be one file on any machine."""

    def test_the_same_key_yields_the_same_id(self):
        self.assertEqual(writer.logical_id("tool:demo-cli"), writer.logical_id("tool:demo-cli"))

    def test_different_keys_yield_different_ids(self):
        self.assertNotEqual(writer.logical_id("tool:a"), writer.logical_id("tool:b"))

    def test_the_id_is_filename_safe(self):
        generated = writer.logical_id("memory_context:~/Developer/some/repo")

        self.assertNotIn("/", generated)
        self.assertNotIn(":", generated)
        self.assertNotIn("~", generated)

    def test_keys_that_slugify_alike_stay_distinct(self):
        """The digest is what stops `a/b` and `a-b` colliding into one file."""
        self.assertNotEqual(
            writer.logical_id("memory_context:~/a/b"), writer.logical_id("memory_context:~/a-b")
        )

    def test_the_id_carries_no_machine_name(self):
        generated = writer.logical_id("tool:demo-cli")

        self.assertNotIn("machine", generated)
        self.assertEqual(generated, writer.logical_id("tool:demo-cli"))


class TestDecisionFiles(WriterTestCase):
    def test_a_decision_file_is_written_per_incident(self):
        self.write_triage(triage_payload(entry("tool:a"), entry("tool:b")))

        files = list((self.root / writer.DECISIONS_DIR).glob("*.json"))

        self.assertEqual(len(files), 2)

    def test_two_machines_converge_on_one_file(self):
        """AC-051 — the failure this prevents is one incident, two outcomes."""
        self.write_triage(triage_payload(entry()), machine="laptop")
        self.write_triage(triage_payload(entry(verdict="investigate")), machine="desktop")

        files = list((self.root / writer.DECISIONS_DIR).glob("*.json"))
        payload = self.decision("tool:demo-cli")

        self.assertEqual(len(files), 1)
        self.assertEqual([item["machine"] for item in payload["entries"]], ["laptop", "desktop"])

    def test_the_machine_lives_at_entry_level(self):
        self.write_triage(triage_payload(entry()), machine="laptop")

        payload = self.decision("tool:demo-cli")

        self.assertNotIn("machine", payload)
        self.assertEqual(payload["entries"][0]["machine"], "laptop")

    def test_a_decision_records_the_verdict_and_reasoning(self):
        self.write_triage(triage_payload(entry(reason="it times out", fix="raise the timeout")))

        item = self.decision("tool:demo-cli")["entries"][0]

        self.assertEqual(item["verdict"], "act")
        self.assertEqual(item["reason"], "it times out")
        self.assertEqual(item["suggested_fix"], "raise the timeout")
        self.assertTrue(item["at"])

    def test_dropped_entries_are_still_recorded(self):
        """A drop is a decision; losing it means re-triaging the same noise."""
        self.write_triage(triage_payload(entry(verdict="drop")))

        self.assertEqual(self.decision("tool:demo-cli")["entries"][0]["verdict"], "drop")

    def test_a_corrupt_decision_file_is_replaced_rather_than_crashing(self):
        path = self.root / writer.DECISIONS_DIR / f"{writer.logical_id('tool:demo-cli')}.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")

        self.write_triage(triage_payload(entry()))

        self.assertEqual(len(self.decision("tool:demo-cli")["entries"]), 1)


class TestQueue(WriterTestCase):
    def test_act_and_investigate_reach_the_queue(self):
        self.write_triage(
            triage_payload(entry("tool:a"), entry("tool:b", verdict="investigate"))
        )

        text = self.queue_text()

        self.assertIn("## Act", text)
        self.assertIn("## Investigate", text)
        self.assertIn("tool:a", text)
        self.assertIn("tool:b", text)

    def test_drops_do_not_reach_the_queue(self):
        self.write_triage(triage_payload(entry("tool:a"), entry("tool:noise", verdict="drop")))

        text = self.queue_text()

        self.assertIn("tool:a", text)
        self.assertNotIn("tool:noise", text)

    def test_an_all_drop_triage_writes_an_empty_queue(self):
        result = self.write_triage(triage_payload(entry(verdict="drop")))

        self.assertIn("Nothing to act on", self.queue_text())
        self.assertEqual(result.queued, 0)
        self.assertEqual(result.dropped, 1)

    def test_the_queue_says_nothing_is_automatic(self):
        self.write_triage(triage_payload(entry()))

        self.assertIn("Nothing is applied", self.queue_text())

    def test_the_queue_points_at_the_decision_file(self):
        self.write_triage(triage_payload(entry()))

        self.assertIn(writer.logical_id("tool:demo-cli"), self.queue_text())

    def test_notes_are_carried_through(self):
        self.write_triage(triage_payload(entry(), notes="mostly noise this week"))

        self.assertIn("mostly noise this week", self.queue_text())


class TestQueueIsDerivedNotOverwritten(WriterTestCase):
    """The queue is a view over the decision files, not a per-run snapshot.

    Writing only this run's verdicts deleted whatever the human had not got to
    yet: an empty packet, or one covering different targets, silently emptied
    the working list.
    """

    def test_an_empty_triage_does_not_clear_the_queue(self):
        self.write_triage(triage_payload(entry("tool:keep-me")))

        self.write_triage(triage_payload(notes="empty packet"))

        self.assertIn("tool:keep-me", self.queue_text())

    def test_a_triage_about_other_targets_does_not_clear_the_queue(self):
        self.write_triage(triage_payload(entry("tool:keep-me")))

        self.write_triage(triage_payload(entry("tool:something-else")))

        text = self.queue_text()
        self.assertIn("tool:keep-me", text)
        self.assertIn("tool:something-else", text)

    def test_re_triaging_as_drop_removes_the_entry(self):
        self.write_triage(triage_payload(entry("tool:noisy")))

        self.write_triage(triage_payload(entry("tool:noisy", verdict="drop")))

        self.assertNotIn("tool:noisy", self.queue_text())

    def test_the_latest_verdict_wins(self):
        self.write_triage(triage_payload(entry("tool:x", verdict="investigate")))

        self.write_triage(triage_payload(entry("tool:x", verdict="act", reason="now clear")))

        self.assertIn("now clear", self.queue_text())

    def test_a_resolved_target_leaves_the_queue(self):
        """Once resolved the miner stops staging it, so no later triage would
        ever come along to clear the entry."""
        self.write_triage(triage_payload(entry("tool:fixed-now")))

        self.write_triage(triage_payload(), resolved_keys={"tool:fixed-now"})

        self.assertNotIn("tool:fixed-now", self.queue_text())

    def test_the_queue_count_reflects_everything_open(self):
        self.write_triage(triage_payload(entry("tool:a")))

        result = self.write_triage(triage_payload(entry("tool:b")))

        self.assertEqual(result.queued, 2)


class TestOutputRootConfinement(WriterTestCase):
    """AC-052."""

    def test_nothing_is_written_outside_the_output_root(self):
        self.write_triage(triage_payload(entry("tool:a"), entry("tool:b", verdict="drop")))

        outside = [
            path
            for path in self.tmp.rglob("*")
            if path.is_file() and self.root not in path.parents
        ]

        self.assertEqual(outside, [])

    def test_a_traversing_key_cannot_escape(self):
        """The key comes from a model, so it is hostile input by definition."""
        self.write_triage(triage_payload(entry(key="../../../../etc/passwd")))

        outside = [
            path
            for path in self.tmp.rglob("*")
            if path.is_file() and self.root not in path.parents
        ]

        self.assertEqual(outside, [])

    def test_a_traversing_key_is_flattened_into_a_safe_name(self):
        self.write_triage(triage_payload(entry(key="../../etc/passwd")))

        files = list((self.root / writer.DECISIONS_DIR).glob("*.json"))

        self.assertEqual(len(files), 1)
        self.assertNotIn("..", files[0].name)

    def test_the_resolver_refuses_an_escaping_path(self):
        from pi_self_improvement import stage

        with self.assertRaises(writer.OutputRootEscape):
            stage.resolve_within(self.root, "../../escape.json")


class TestTriageParsing(WriterTestCase):
    def test_a_plain_json_object_parses(self):
        triage = writer.parse_triage(json.dumps(triage_payload(entry())))

        self.assertEqual(len(triage.entries), 1)

    def test_a_fenced_json_block_parses(self):
        """Models add fences under instruction not to."""
        text = "```json\n" + json.dumps(triage_payload(entry())) + "\n```"

        self.assertEqual(len(writer.parse_triage(text).entries), 1)

    def test_a_file_path_parses(self):
        path = self.tmp / "triage.json"
        path.write_text(json.dumps(triage_payload(entry())), encoding="utf-8")

        self.assertEqual(len(writer.parse_triage(path).entries), 1)

    def test_an_empty_entries_list_is_valid(self):
        triage = writer.parse_triage({"entries": [], "notes": "empty packet"})

        self.assertEqual(triage.entries, [])

    def test_malformed_json_is_refused(self):
        with self.assertRaises(writer.TriageError):
            writer.parse_triage("not json at all")

    def test_a_missing_entries_list_is_refused(self):
        with self.assertRaises(writer.TriageError):
            writer.parse_triage({"notes": "hello"})

    def test_an_unknown_verdict_is_refused(self):
        with self.assertRaises(writer.TriageError) as caught:
            writer.parse_triage(triage_payload(entry(verdict="maybe")))

        self.assertIn("maybe", str(caught.exception))

    def test_an_entry_without_a_key_is_refused(self):
        with self.assertRaises(writer.TriageError):
            writer.parse_triage({"entries": [{"verdict": "act"}]})

    def test_triage_strings_are_redacted(self):
        """Triage quotes packet excerpts, and a --full packet holds raw secrets."""
        secret = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"

        self.write_triage(triage_payload(entry(reason=f"token {secret} rejected")))

        self.assertNotIn(secret, self.queue_text())
        self.assertNotIn(secret, json.dumps(self.decision("tool:demo-cli")))


class TestCliIntegration(WriterTestCase):
    """The runner calls this exact surface, so it has to exist and work."""

    def run_cli(self, *argv, expect=cli.EXIT_OK):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(list(argv), stdout=out, stderr=err)
        self.assertEqual(code, expect, msg=err.getvalue())
        return out.getvalue(), err.getvalue()

    def triage_file(self, payload):
        path = self.tmp / "triage.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_write_queue_writes_the_queue_and_decisions(self):
        path = self.triage_file(triage_payload(entry()))

        stdout, _ = self.run_cli(
            "--write-queue", str(path), "--output-root", str(self.root), "--machine", "laptop"
        )

        self.assertTrue((self.root / writer.QUEUE_FILE).is_file())
        self.assertIn("queued 1", stdout)
        self.assertEqual(self.decision("tool:demo-cli")["entries"][0]["machine"], "laptop")

    def test_an_empty_packet_triage_succeeds(self):
        path = self.triage_file({"entries": [], "notes": "empty packet"})

        stdout, _ = self.run_cli("--write-queue", str(path), "--output-root", str(self.root))

        self.assertIn("queued 0", stdout)
        self.assertIn("Nothing to act on", self.queue_text())

    def test_malformed_triage_exits_with_an_error(self):
        path = self.tmp / "triage.json"
        path.write_text("garbage", encoding="utf-8")

        _, stderr = self.run_cli(
            "--write-queue", str(path), "--output-root", str(self.root), expect=cli.EXIT_ERROR
        )

        self.assertIn("error:", stderr)

    def test_write_queue_does_not_scan(self):
        """It must not need a sessions directory to exist."""
        path = self.triage_file(triage_payload(entry()))

        stdout, _ = self.run_cli("--write-queue", str(path), "--output-root", str(self.root))

        self.assertNotIn("transcript(s)", stdout)


if __name__ == "__main__":
    unittest.main()

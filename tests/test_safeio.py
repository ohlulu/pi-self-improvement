"""Confinement and atomicity of every write this package makes (AC-001, AC-052)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from pi_self_improvement import safeio, stage, state


class SafeIoTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "output"
        self.root.mkdir()
        self.addCleanup(self._tmp.cleanup)


class TestSymlinkConfinement(SafeIoTestCase):
    """A symlink planted at the destination makes an ordinary open() truncate a
    file outside the root. Resolving the directory does not catch it."""

    def test_a_symlinked_target_is_refused(self):
        victim = self.tmp / "OUTSIDE.txt"
        victim.write_text("precious", encoding="utf-8")
        (self.root / "state.json").symlink_to(victim)

        with self.assertRaises(safeio.OutputRootEscape):
            safeio.write_text(self.root / "state.json", "clobbered")

        self.assertEqual(victim.read_text(encoding="utf-8"), "precious")

    def test_a_symlinked_temporary_file_cannot_be_followed(self):
        victim = self.tmp / "OUTSIDE.txt"
        victim.write_text("precious", encoding="utf-8")
        (self.root / "state.json.tmp").symlink_to(victim)

        safeio.write_text(self.root / "state.json", "fine")

        self.assertEqual(victim.read_text(encoding="utf-8"), "precious")
        self.assertEqual((self.root / "state.json").read_text(encoding="utf-8"), "fine")

    def test_state_save_cannot_escape_through_its_temporary_file(self):
        victim = self.tmp / "OUTSIDE.txt"
        victim.write_text("precious", encoding="utf-8")
        (self.root / "state.json.tmp").symlink_to(victim)

        state.State().save(self.root / "state.json")

        self.assertEqual(victim.read_text(encoding="utf-8"), "precious")

    def test_resolve_within_refuses_an_escaping_path(self):
        with self.assertRaises(safeio.OutputRootEscape):
            safeio.resolve_within(self.root, "../../escape.json")

    def test_resolve_within_allows_a_path_that_normalizes_back_inside(self):
        resolved = safeio.resolve_within(self.root, "runs/../inside.json")

        self.assertEqual(resolved, (self.root / "inside.json").resolve())


class TestAtomicity(SafeIoTestCase):
    def test_no_temporary_file_survives_a_successful_write(self):
        safeio.write_json(self.root / "state.json", {"a": 1})

        self.assertEqual([p.name for p in self.root.iterdir()], ["state.json"])

    def test_a_failed_write_leaves_the_original_intact(self):
        target = self.root / "state.json"
        safeio.write_json(target, {"generation": 1})

        class Unserializable:
            pass

        with self.assertRaises(TypeError):
            safeio.write_json(target, {"bad": Unserializable()})

        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"generation": 1})

    def test_a_failed_write_leaves_no_temporary_file(self):
        target = self.root / "state.json"
        safeio.write_json(target, {"generation": 1})

        with self.assertRaises(TypeError):
            safeio.write_json(target, {"bad": object()})

        self.assertFalse((self.root / "state.json.tmp").exists())

    def test_read_json_treats_corruption_as_absent(self):
        path = self.root / "state.json"
        path.write_text("{not json", encoding="utf-8")

        self.assertIsNone(safeio.read_json(path))

    def test_the_file_is_flushed_to_disk(self):
        """fsync is what makes the rename meaningful after a hard kill."""
        target = self.root / "state.json"
        safeio.write_text(target, "content")

        self.assertEqual(os.stat(target).st_size, len("content"))


class TestRunIdentity(SafeIoTestCase):
    """Two scans in one second used to share a run id, so the second silently
    overwrote the first's metadata, packet and proposals."""

    def test_ids_within_the_same_second_are_distinct(self):
        self.assertNotEqual(stage.new_run_id(1000.0), stage.new_run_id(1000.5))

    def test_string_order_matches_chronological_order(self):
        early, middle, late = (stage.new_run_id(t) for t in (1000.0, 1000.5, 1001.0))

        self.assertEqual(sorted([late, early, middle]), [early, middle, late])

    def test_two_scans_in_the_same_second_do_not_share_a_directory(self):
        first = stage.write_run(self.root, [], counts=None)
        second = stage.write_run(self.root, [], counts=None)

        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(len(list((self.root / "runs").glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()

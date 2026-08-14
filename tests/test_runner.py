"""The fixloop runner's two load-bearing guarantees (AC-035, AC-036).

The runner is shell, so these tests drive the real script with a fake `pi` on
PATH and read back what it was actually invoked with. Asserting on the script's
source text instead would pass for a `--tools` line that never reaches pi.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).parent.parent / "templates"
RUNNER = TEMPLATES / "fixloop-run.sh"

FAKE_PI = """#!/bin/bash
printf '%s\\n' "$@" > "$ARGV_DUMP"
printf '{"entries": [], "notes": "empty packet"}\\n'
exit 0
"""

FAKE_PSI = """#!/bin/bash
printf '%s\\n' "$@" > "$PSI_ARGV_DUMP"
exit 0
"""


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.logs = self.tmp / "Logs"
        self.output_root = self.tmp / "output"
        self.argv_dump = self.tmp / "pi-argv.txt"
        self.psi_argv_dump = self.tmp / "psi-argv.txt"
        self._install("pi", FAKE_PI)
        self._install("pi-self-improvement", FAKE_PSI)
        self.addCleanup(self._tmp.cleanup)

    def _install(self, name, body):
        path = self.bin / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def with_packet(self):
        packets = self.output_root / "review-packets"
        packets.mkdir(parents=True)
        (packets / "20260101T000000Z.md").write_text("# Review packet\n", encoding="utf-8")

    def run_runner(self, **env):
        environ = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "PI_BIN": str(self.bin / "pi"),
            "PSI_BIN": str(self.bin / "pi-self-improvement"),
            "PSI_OUTPUT_ROOT": str(self.output_root),
            "FIXLOOP_LOG_DIR": str(self.logs),
            "FIXLOOP_FUSE": "30",
            "ARGV_DUMP": str(self.argv_dump),
            "PSI_ARGV_DUMP": str(self.psi_argv_dump),
            **env,
        }
        return subprocess.run(
            ["bash", str(RUNNER)], env=environ, capture_output=True, text=True, timeout=60
        )

    def log_text(self):
        path = self.logs / "pi-self-improvement-fixloop.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def pi_argv(self):
        return self.argv_dump.read_text(encoding="utf-8").splitlines()


class TestSyntax(unittest.TestCase):
    def test_the_runner_parses(self):
        result = subprocess.run(["bash", "-n", str(RUNNER)], capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_prompt_template_exists(self):
        self.assertTrue((TEMPLATES / "fixloop-prompt.md").is_file())


class TestToolAllowlist(RunnerTestCase):
    """AC-036 — the safety boundary, read off the real invocation."""

    def test_tools_is_exactly_the_read_only_set(self):
        self.with_packet()

        self.run_runner()
        argv = self.pi_argv()

        self.assertIn("--tools", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "read,grep,find,ls")

    def test_no_writing_tool_is_ever_enabled(self):
        self.with_packet()

        self.run_runner()
        allowlist = self.pi_argv()[self.pi_argv().index("--tools") + 1].split(",")

        for forbidden in ("bash", "write", "edit", "apply_patch", "subagent"):
            self.assertNotIn(forbidden, allowlist)

    def test_the_run_is_headless(self):
        self.with_packet()

        self.run_runner()

        self.assertIn("-p", self.pi_argv())


class TestLivenessLine(RunnerTestCase):
    """AC-035 — a scheduled job that fails silently looks like one that never fired."""

    def test_an_empty_queue_still_writes_a_run_line(self):
        result = self.run_runner()

        self.assertEqual(result.returncode, 0)
        self.assertIn("RUN ", self.log_text())
        self.assertIn("status=empty", self.log_text())

    def test_a_successful_run_writes_a_run_line(self):
        self.with_packet()

        self.run_runner()

        self.assertIn("status=ok", self.log_text())

    def test_a_failing_pi_still_writes_a_run_line(self):
        self.with_packet()
        self._install("pi", "#!/bin/bash\nexit 3\n")

        self.run_runner()

        self.assertIn("RUN ", self.log_text())
        self.assertIn("status=error", self.log_text())

    def test_a_failing_writer_still_writes_a_run_line(self):
        self.with_packet()
        self._install("pi-self-improvement", "#!/bin/bash\nexit 1\n")

        self.run_runner()

        self.assertIn("status=write-failed", self.log_text())

    def test_the_log_directory_is_created_when_missing(self):
        self.assertFalse(self.logs.exists())

        self.run_runner()

        self.assertTrue(self.logs.is_dir())

    def test_every_run_line_carries_a_utc_timestamp(self):
        self.run_runner()

        line = self.log_text().strip()

        self.assertRegex(line, r"^RUN \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z fixloop status=")

    def test_repeated_runs_append_rather_than_truncate(self):
        self.run_runner()
        self.run_runner()

        self.assertEqual(self.log_text().count("RUN "), 2)


class TestWallClockFuse(RunnerTestCase):
    """DEC-011: pi has no --max-turns, so the fuse is the only bound."""

    def test_a_hanging_pi_is_killed_and_reported(self):
        self.with_packet()
        self._install("pi", "#!/bin/bash\nsleep 60\n")

        result = self.run_runner(FIXLOOP_FUSE="1")

        self.assertEqual(result.returncode, 0)
        self.assertIn("status=fused", self.log_text())

    def test_the_fuse_does_not_fire_on_a_fast_run(self):
        self.with_packet()

        self.run_runner(FIXLOOP_FUSE="30")

        self.assertNotIn("status=fused", self.log_text())

    def test_the_fuse_does_not_outlive_the_run(self):
        """A leaked timer would signal whatever process inherits the pid."""
        self.with_packet()

        result = self.run_runner(FIXLOOP_FUSE="30")

        self.assertLess(result.returncode, 128)


class TestWriterHandoff(RunnerTestCase):
    def test_the_model_output_goes_to_the_deterministic_writer(self):
        self.with_packet()

        self.run_runner()
        argv = self.psi_argv_dump.read_text(encoding="utf-8").splitlines()

        self.assertIn("--write-queue", argv)
        self.assertIn("--output-root", argv)

    def test_the_writer_is_not_called_when_there_is_nothing_to_triage(self):
        self.run_runner()

        self.assertFalse(self.psi_argv_dump.exists())


if __name__ == "__main__":
    unittest.main()


class TestPacketIsNotReprocessed(RunnerTestCase):
    """The miner produces a packet twice a week; this runs daily."""

    def test_the_same_packet_is_triaged_once(self):
        self.with_packet()

        self.run_runner()
        self.run_runner()

        log = self.log_text()
        self.assertEqual(log.count("status=ok"), 1)
        self.assertIn("status=skipped", log)

    def test_a_new_packet_is_triaged_again(self):
        self.with_packet()
        self.run_runner()

        (self.output_root / "review-packets" / "20260202T000000Z.md").write_text(
            "# Review packet\n", encoding="utf-8"
        )
        self.run_runner()

        self.assertEqual(self.log_text().count("status=ok"), 2)

    def test_a_failed_write_is_retried_next_time(self):
        """Marking on failure would strand the packet permanently."""
        self.with_packet()
        self._install("pi-self-improvement", "#!/bin/bash\nexit 1\n")
        self.run_runner()

        self._install("pi-self-improvement", FAKE_PSI)
        self.run_runner()

        self.assertIn("status=ok", self.log_text())

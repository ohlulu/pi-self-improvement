"""Scaffold corpus (REQ-011, DEC-009).

Scaffold is text that was injected into a session rather than typed by the user.
Filtering it is not cosmetic: this loop mines pi sessions, runs inside pi, and
its own harness injects context into those sessions. Without the filter it
detects its own scaffolding as user friction and files proposals against itself.

The filter is structural first. Pi expresses injections as their own record
types, so `custom_message` never becomes a user message at all — which is a
stronger guarantee than any regex, and it is why the built-in marker list is two
entries long rather than six.
"""

import unittest

from pi_self_improvement import detect, parse
from pi_self_improvement.model import AssistantTurn, SessionSummary, UserMessage
from pi_self_improvement.redact import Redactor

from tests import support

SEPARATOR = "\u2500" * 24

#: Injected blocks that reach the user role as body text.
SCAFFOLD = [
    (f"{SEPARATOR}\nMid-run steering\n{SEPARATOR}\nUse the packaging service instead.", "steering block"),
    ("Task: audit the guide for stale commands. Report instead of fixing.", "subagent task seed"),
    ("   Task: review the workspace and report back.", "task seed with leading space"),
    (f"Some preamble\n{SEPARATOR}\nthen more text", "separator anywhere in the body"),
]

#: Real user messages that mention scaffolding without being it.
NOT_SCAFFOLD = [
    ("The [Project docs index] block keeps showing up above my messages.", "AC-045"),
    ("Can you make the Mid-run steering banner shorter?", "discusses steering"),
    ("My task: rewrite the guide.", "Task: not at the start"),
    ("Use a --- separator instead of a box rule.", "short rule, not the marker"),
    ("不對，我是說要用 release 子指令。", "an ordinary correction"),
    ("Cymbal suggests: is a phrase I keep seeing, can we mute it?", "discusses an injection"),
]


class TestStructuralExclusion(unittest.TestCase):
    """AC-020: the data model does the filtering, before any marker matching."""

    def setUp(self):
        self.summary = parse.parse_transcript(
            support.FIXTURE_SESSIONS
            / "--tmp-pi-fixtures-beta--"
            / "2026-01-07T11-00-00-000Z_00000000-0000-7000-8000-00000000b001.jsonl"
        )

    def test_a_custom_message_injection_never_becomes_a_user_message(self):
        joined = " ".join(message.text for message in self.summary.user_messages)

        self.assertNotIn("Listing project docs", joined)
        self.assertNotIn("[Project docs index]\nListing", joined)

    def test_the_injection_is_counted_rather_than_dropped(self):
        self.assertEqual(self.summary.counts.skipped_records.get("custom_message"), 1)

    def test_no_marker_matching_was_needed_for_it(self):
        """The injected text contains "instead" and would have been a weak-cue hit."""
        self.assertFalse(detect.is_scaffold("[Project docs index]\nListing project docs:"))


class TestMarkers(unittest.TestCase):
    def test_injected_blocks_are_scaffold(self):
        for text, why in SCAFFOLD:
            with self.subTest(why=why):
                self.assertTrue(detect.is_scaffold(text), f"missed scaffold ({why})")

    def test_real_messages_are_not_scaffold(self):
        """AC-045 and the false positives a longer marker list would create."""
        for text, why in NOT_SCAFFOLD:
            with self.subTest(why=why):
                self.assertFalse(detect.is_scaffold(text), f"false positive ({why}): {text}")

    def test_a_shorter_rule_is_not_the_separator(self):
        self.assertFalse(detect.is_scaffold("\u2500" * 9))
        self.assertTrue(detect.is_scaffold("\u2500" * 10))

    def test_config_can_add_markers(self):
        config = detect.DetectConfig(extra_scaffold_markers=("<<injected>>",))

        self.assertTrue(detect.is_scaffold("<<injected>> do the thing", config))
        self.assertFalse(detect.is_scaffold("<<injected>> do the thing"))

    def test_an_empty_marker_never_matches_everything(self):
        config = detect.DetectConfig(extra_scaffold_markers=("",))

        self.assertFalse(detect.is_scaffold("an ordinary message", config))


class TestScaffoldSuppressesCorrections(unittest.TestCase):
    """AC-019: the point of the filter."""

    def setUp(self):
        self.redactor = Redactor()

    def corrections(self, text, config=None):
        summary = SessionSummary(
            path="sessions/--tmp-beta--/s1.jsonl",
            session_id="s1",
            cwd="/tmp/pi-fixtures/beta",
            user_messages=[UserMessage(text=text, line=5)],
            assistant_turns=[AssistantTurn(text="Done.", line=2)],
        )
        return [
            signal
            for signal in detect.detect_session(summary, redactor=self.redactor, config=config)
            if signal.kind == detect.CORRECTION
        ]

    def test_a_steering_block_with_cues_produces_no_correction(self):
        text = f"{SEPARATOR}\nMid-run steering\n{SEPARATOR}\nUse the packaging service instead, don't do it by hand."

        self.assertEqual(self.corrections(text), [])

    def test_a_task_seed_with_cues_produces_no_correction(self):
        text = "Task: audit the guide. Do not edit anything, report what should be changed instead."

        self.assertEqual(self.corrections(text), [])

    def test_an_equivalent_real_message_still_produces_one(self):
        """The filter must not be doing its job by suppressing everything."""
        self.assertEqual(len(self.corrections("Use the packaging service instead.")), 1)

    def test_the_fixture_session_yields_only_genuine_corrections(self):
        summary = parse.parse_transcript(
            support.FIXTURE_SESSIONS
            / "--tmp-pi-fixtures-beta--"
            / "2026-01-07T11-00-00-000Z_00000000-0000-7000-8000-00000000b001.jsonl"
        )

        signals = [
            signal
            for signal in detect.detect_session(summary, redactor=self.redactor)
            if signal.kind == detect.CORRECTION
        ]
        lines = sorted(signal.evidence.line for signal in signals)

        # Line 5 is the zh correction, line 11 the English one. The 沒錯 agreement,
        # the pasted document, the steering block, the task seed and the message
        # discussing the docs index are all correctly left out.
        self.assertEqual(lines, [5, 11])


if __name__ == "__main__":
    unittest.main()

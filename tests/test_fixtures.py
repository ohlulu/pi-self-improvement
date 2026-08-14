"""Fixture hygiene: valid JSONL, synthetic content only, and the coverage T003 promises."""

import json
import re
import unittest
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SESSIONS = FIXTURES / "sessions"


def transcripts() -> list[Path]:
    return sorted(SESSIONS.rglob("*.jsonl"))


def records(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


class TestFixturesAreValidJsonl(unittest.TestCase):
    def test_every_line_of_every_transcript_parses(self):
        found = transcripts()
        self.assertTrue(found, "no fixture transcripts found")
        for path in found:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                with self.subTest(path=path.name, line=number):
                    json.loads(line)

    def test_no_transcript_has_a_trailing_blank_record(self):
        for path in transcripts():
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("\n"), f"{path.name} must end with a newline")
            self.assertFalse(text.endswith("\n\n"), f"{path.name} has a trailing blank line")


class TestFixturesAreSynthetic(unittest.TestCase):
    """Guards the privacy rule: nothing here may point at a real machine."""

    FORBIDDEN = (
        "/Users/",
        "/home/",
        ".pi/agent/sessions",
        "ohlulu",
    )

    def test_no_real_paths_or_identities(self):
        for path in transcripts():
            text = path.read_text(encoding="utf-8")
            for needle in self.FORBIDDEN:
                with self.subTest(path=path.name, needle=needle):
                    self.assertNotIn(needle, text)

    def test_every_cwd_is_under_the_fixture_workspace(self):
        for path in transcripts():
            for record in records(path):
                cwd = record.get("cwd")
                if cwd:
                    self.assertTrue(
                        cwd.startswith("/tmp/pi-fixtures/"),
                        f"{path.name}: unexpected cwd {cwd!r}",
                    )


class TestFixtureCoverage(unittest.TestCase):
    """T003's checklist, asserted rather than trusted."""

    @classmethod
    def setUpClass(cls):
        cls.by_name = {p.parent.name if p.name == "session.jsonl" else p.stem[-4:]: p for p in transcripts()}
        cls.all_records = {p: records(p) for p in transcripts()}

    def messages(self, role: str) -> list[dict]:
        out = []
        for recs in self.all_records.values():
            for record in recs:
                if record.get("type") == "message" and record.get("message", {}).get("role") == role:
                    out.append(record["message"])
        return out

    def tool_calls(self) -> list[dict]:
        out = []
        for message in self.messages("assistant"):
            for block in message.get("content", []):
                if block.get("type") == "toolCall":
                    out.append(block)
        return out

    def test_four_roles_are_present(self):
        for role in ("user", "assistant", "toolResult", "bashExecution"):
            with self.subTest(role=role):
                self.assertTrue(self.messages(role), f"no {role} record in fixtures")

    def test_is_error_true_and_false_both_appear(self):
        flags = {message.get("isError") for message in self.messages("toolResult")}
        self.assertIn(True, flags)
        self.assertIn(False, flags)

    def test_hang_shaped_output_exists(self):
        texts = [
            block.get("text", "")
            for message in self.messages("toolResult")
            for block in message.get("content", [])
        ]
        self.assertTrue(any("timed out" in text for text in texts))

    def test_structurally_empty_result_exists(self):
        texts = [
            block.get("text", "")
            for message in self.messages("toolResult")
            for block in message.get("content", [])
        ]
        self.assertIn("[]", texts)

    def test_skill_md_read_exists(self):
        paths = [call["arguments"].get("path", "") for call in self.tool_calls() if call["name"] == "read"]
        self.assertTrue(any(path.endswith("SKILL.md") for path in paths))

    def test_skill_loaded_custom_entry_exists(self):
        types = {
            record.get("customType")
            for recs in self.all_records.values()
            for record in recs
            if record.get("type") == "custom"
        }
        self.assertIn("context:skill_loaded", types)

    def test_custom_message_injection_exists(self):
        kinds = [
            record
            for recs in self.all_records.values()
            for record in recs
            if record.get("type") == "custom_message"
        ]
        self.assertTrue(kinds)
        self.assertIn("[Project docs index]", kinds[0]["content"])

    def test_bilingual_corrections_exist(self):
        texts = [
            block.get("text", "")
            for message in self.messages("user")
            for block in message.get("content", [])
        ]
        self.assertTrue(any("不對" in text or "你搞錯" in text for text in texts))
        self.assertTrue(any("That's wrong" in text for text in texts))
        self.assertTrue(any("沒錯" in text for text in texts), "negative guard case missing")

    def test_scaffold_shapes_exist(self):
        texts = [
            block.get("text", "")
            for message in self.messages("user")
            for block in message.get("content", [])
        ]
        self.assertTrue(any(re.search("\u2500{10,}", text) for text in texts))
        self.assertTrue(any(text.startswith("Task:") for text in texts))

    def test_bash_execution_carries_a_non_zero_exit(self):
        codes = [message.get("exitCode") for message in self.messages("bashExecution")]
        self.assertIn(127, codes)

    def test_aborted_and_error_stop_reasons_exist(self):
        reasons = {message.get("stopReason") for message in self.messages("assistant")}
        self.assertIn("aborted", reasons)
        self.assertIn("error", reasons)

    def test_a_dangling_tool_call_exists(self):
        # Pairing is per transcript, so this must be checked per file: a call id
        # reused in another transcript is not a match.
        for path, recs in self.all_records.items():
            call_ids, result_ids = set(), set()
            for record in recs:
                message = record.get("message") or {}
                if message.get("role") == "assistant":
                    call_ids |= {
                        block["id"] for block in message.get("content", []) if block.get("type") == "toolCall"
                    }
                elif message.get("role") == "toolResult":
                    result_ids.add(message.get("toolCallId"))
            if call_ids - result_ids:
                return
        self.fail("no dangling toolCall in any fixture transcript")

    def test_a_sibling_branch_point_exists(self):
        for recs in self.all_records.values():
            parents = [r.get("parentId") for r in recs if r.get("type") == "message" and r.get("parentId")]
            if len(parents) != len(set(parents)):
                return
        self.fail("no sibling parentId branch point in fixtures")

    def test_a_subagent_transcript_exists(self):
        shape = re.compile(r"/[^/]+/run-\d+/session\.jsonl$")
        self.assertTrue(any(shape.search(str(p)) for p in transcripts()))

    def test_a_non_canonical_schema_file_exists(self):
        for path, recs in self.all_records.items():
            if not any(record.get("type") == "session" for record in recs):
                return
        self.fail("no non-canonical transcript (all have a session header)")


if __name__ == "__main__":
    unittest.main()

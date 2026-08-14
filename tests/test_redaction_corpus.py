"""Redaction corpus (REQ-004).

The rule this file enforces: no secret-shaped string survives into anything the
miner writes. Every case is a canary — a distinctive fake value that must never
appear verbatim in any output, on any surface.

AC-006 and AC-042 are only half-covered here, because their remaining half needs
files that do not exist yet:

- `local_only` is asserted on the redactor. Asserting it lands in run metadata,
  proposal JSON and the review packet needs `stage.py` (T018) and `cli.py` (T023).
- The canary scan runs over a temporary tree standing in for the output root.
  Running it over a real written output root needs the same two modules.

Both halves are appended to this file when those tasks land.
"""

import json
import tempfile
import unittest
from pathlib import Path

from pi_self_improvement import redact
from pi_self_improvement.model import DEFAULT_EXCERPT_LIMIT

from . import support

# Two canaries are assembled from fragments rather than written literally.
# They are invented like the rest, but their shapes are convincing enough that
# GitHub's push protection blocks any push containing them — which is the corpus
# doing its job in a place that is inconvenient. Splitting the literal keeps this
# repo pushable and forkable without weakening the test: the assembled values are
# byte-identical, so the redaction patterns see exactly what they saw before.
#
# Do not inline these back. The scanner will block the push, and the tempting
# next move — bypassing push protection on a public repo — is worse.
_SLACK_CANARY = "-".join(["xoxb", "123456789012", "1234567890123", "AbCdEfGhIjKlMnOpQrStUvWx"])
_STRIPE_CANARY = "_".join(["sk", "live", "N0tReal4Key9Value8For7Testing"])

#: Every value here is invented. If one of these strings ever appears in an
#: output file, redaction has a hole.
CANARIES = {
    "email": "alice.wonder@example-corp.test",
    "email_in_git": "Committed by bob.builder@example-corp.test yesterday",
    "phone_intl": "+1 (415) 555-0137",
    "phone_local": "0912-345-678",
    "auth_header": "Authorization: Bearer nOtAr3alT0k3nV4lu3ForTestingOnly99",
    "auth_header_curl": '-H "Authorization: Basic YWxpY2U6bm90YXJlYWxwYXNzd29yZA=="',
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "aws_secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "gcp_api_key": "AIzaSyD1234567890abcdefghijklmnopqrstuv",
    "github_token": "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "slack_token": _SLACK_CANARY,
    "anthropic_key": "sk-ant-api03-N0tReal4Key9Value8For7Testing6Only5Xyz",
    "openai_key": "sk-N0tReal4Key9Value8For7Testing6Only5AbcDef",
    "stripe_key": _STRIPE_CANARY,
    "jwt": (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiJmaXh0dXJlIiwibmFtZSI6InRlc3QifQ"
        ".Dbjft3eZ4CVPmB92K27uhbUJU1p1rXwW1gFWFOEjZk"
    ),
    "private_key": (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAn0tArEaLkEyF1xTur3fixtureonlyvalue1234567890abcd\n"
        "efghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789fake==\n"
        "-----END RSA PRIVATE KEY-----"
    ),
    "long_token": "Tk9UQVJFQUxUT0tFTjEyMzQ1Njc4OTBBQmNEZUZnSGlK",
    "url_credentials": "https://deploy:hunter2NotARealPass@git.example-corp.test/repo.git",
    "env_assignment": "DEPLOY_TOKEN=n0treal-deploy-token-value-9876543210",
    "password_assignment": "password: hunter2NotARealPass",
    "api_key_assignment": "api_key = 'n0tarealapikeyvalue1234567890'",
}

#: Strings that must survive untouched. Over-masking makes a review packet
#: useless, which is its own kind of failure.
MUST_SURVIVE = {
    "git_sha": "9f2c1ab4d7e6058c3b21f4a0d8e7c6b5a4938271",
    "short_sha": "fd4744b",
    "session_uuid": "00000000-0000-7000-8000-00000000a001",
    "file_path": "src/pi_self_improvement/parse.py",
    "command": "demo-cli sync --retry 3 --verbose",
    "error_text": "demo-cli: error: unknown flag --force",
    "prose_zh": "不對，我是說要用 demo-cli 的 release 子指令。",
    "version": "Python 3.10.14",
    "timestamp": "2026-01-05T09:00:00.000Z",
    "exit_code": "exited with code 127",
}


class TestSecretShapesNeverSurvive(unittest.TestCase):
    """AC-005: on every transcript-derived surface."""

    def setUp(self):
        self.redactor = redact.Redactor()

    def assert_masked(self, produced: str, secret: str, label: str):
        self.assertNotIn(secret, produced, f"{label}: secret survived redaction")
        self.assertIn(redact.MASK, produced, f"{label}: nothing was masked")

    def test_masked_in_free_text(self):
        for label, secret in CANARIES.items():
            with self.subTest(surface="text", case=label):
                self.assert_masked(self.redactor.text(secret), secret, label)

    def test_masked_when_embedded_in_a_sentence(self):
        for label, secret in CANARIES.items():
            with self.subTest(surface="embedded", case=label):
                carrier = f"the tool printed {secret} and then stopped"
                self.assert_masked(self.redactor.text(carrier), secret, label)

    def test_masked_in_an_excerpt(self):
        for label, secret in CANARIES.items():
            with self.subTest(surface="excerpt", case=label):
                self.assert_masked(self.redactor.excerpt(secret), secret, label)

    def test_masked_in_a_command(self):
        for label, secret in CANARIES.items():
            with self.subTest(surface="command", case=label):
                command = f"demo-cli push --token {secret}"
                self.assert_masked(self.redactor.command(command), secret, label)

    def test_masked_in_arguments(self):
        for label, secret in CANARIES.items():
            with self.subTest(surface="arguments", case=label):
                arguments = {"command": secret, "nested": {"items": [secret]}}
                produced = json.dumps(self.redactor.arguments(arguments), ensure_ascii=False)
                self.assert_masked(produced, secret, label)

    def test_masked_in_a_displayed_path(self):
        for label, secret in CANARIES.items():
            if "\n" in secret:
                continue
            with self.subTest(surface="path", case=label):
                produced = self.redactor.path(f"/tmp/pi-fixtures/{secret}/notes.md")
                self.assertNotIn(secret, produced, f"{label}: secret survived in a path")

    def test_masked_in_a_generated_summary(self):
        for label, secret in CANARIES.items():
            with self.subTest(surface="summary", case=label):
                summary = f"`demo-cli` failed 3 times; last output was {secret}"
                self.assert_masked(self.redactor.text(summary), secret, label)


class TestPrecision(unittest.TestCase):
    """Over-masking is a failure too: an unreadable packet gets ignored."""

    def setUp(self):
        self.redactor = redact.Redactor()

    def test_ordinary_strings_survive(self):
        for label, value in MUST_SURVIVE.items():
            with self.subTest(case=label):
                self.assertEqual(self.redactor.text(value), value)

    def test_a_realistic_error_line_survives(self):
        line = "demo-cli: error: unknown flag --force\nRun 'demo-cli sync --help' for usage."
        self.assertEqual(self.redactor.text(line), line)

    def test_a_transcript_path_survives(self):
        path = "sessions/--tmp-pi-fixtures-alpha--/2026-01-05T09-00-00-000Z_00000000-0000-7000-8000-00000000a001.jsonl"
        self.assertNotIn(redact.MASK, self.redactor.path(path))


class TestPrecisionRegressions(unittest.TestCase):
    """Shapes that over-masked when the pattern set was first measured on a real
    corpus. Each one is reproduced synthetically — same structure, invented names.

    A masked file path or type annotation is not a harmless extra: evidence whose
    path is `[REDACTED]` cannot be acted on, which quietly defeats the whole tool.
    """

    def setUp(self):
        self.redactor = redact.Redactor()

    def assert_survives(self, value: str):
        self.assertEqual(self.redactor.text(value), value)

    def test_a_forty_character_path_substring_survives(self):
        # Exactly the AWS secret access key length, but word-structured.
        self.assert_survives("Sources/Toolkit/Persistence/Migration1BaseSchema.swift")

    def test_a_slash_anchored_path_run_survives(self):
        self.assert_survives("Sources/Tests/SampleKitiOSTests/L10nKeyAuditTests.swift")

    def test_a_git_sha_after_an_uppercase_key_survives(self):
        # `=` mid-token used to glue HEAD to the sha, making the run look random.
        self.assert_survives("HEAD=865e3becb5d42131d2334409774bbb317b120125")

    def test_concatenated_identifiers_survive(self):
        self.assert_survives("AZ09AFafmulti_linedot_matches_new_lineswap_greedunicodeFlags")

    def test_type_annotations_are_not_credentials(self):
        for value in ("token: string", "id-token: write", "apiKey: number", "secret: boolean"):
            with self.subTest(value=value):
                self.assert_survives(value)

    def test_a_variable_reference_is_not_a_credential(self):
        self.assert_survives("apiToken: ${NPM_TOKEN}")

    def test_a_real_looking_assignment_is_still_masked(self):
        produced = self.redactor.text('apiKey: "svc_sample_2f81b6c7a94f20e04b2e36141b9262fc"')
        self.assertNotIn("svc_sample_2f81b6c7a94f20e04b2e36141b9262fc", produced)
        self.assertIn(redact.MASK, produced)

    def test_a_header_style_api_key_is_still_masked(self):
        produced = self.redactor.text("x-api-key: svc_sample_2f81b6c7a94f20e04b2e36141b9262")
        self.assertNotIn("svc_sample_2f81b6c7a94f20e04b2e36141b9262", produced)


class TestExcerpts(unittest.TestCase):
    def test_excerpt_respects_the_default_limit(self):
        """AC-004."""
        produced = redact.Redactor().excerpt("x" * 2000)
        self.assertLessEqual(len(produced), DEFAULT_EXCERPT_LIMIT)

    def test_excerpt_respects_a_configured_limit(self):
        produced = redact.Redactor(excerpt_limit=40).excerpt("y" * 500)
        self.assertLessEqual(len(produced), 40)

    def test_excerpt_marks_that_it_was_cut(self):
        produced = redact.Redactor(excerpt_limit=40).excerpt("z" * 500)
        self.assertTrue(produced.endswith("…"))

    def test_excerpt_collapses_whitespace(self):
        self.assertEqual(redact.Redactor().excerpt("a\n\n  b\tc  "), "a b c")

    def test_short_text_is_untouched(self):
        self.assertEqual(redact.Redactor().excerpt("all good"), "all good")


class TestFullBypass(unittest.TestCase):
    """AC-006: `--full` keeps the original text and marks the run local-only."""

    def test_default_is_not_local_only(self):
        self.assertFalse(redact.Redactor().local_only)

    def test_full_is_local_only(self):
        self.assertTrue(redact.Redactor(full=True).local_only)

    def test_full_keeps_the_original_text(self):
        secret = CANARIES["github_token"]
        self.assertEqual(redact.Redactor(full=True).text(secret), secret)

    def test_full_does_not_shorten_excerpts(self):
        long_text = "q" * 2000
        self.assertEqual(redact.Redactor(full=True).excerpt(long_text), long_text)

    def test_full_keeps_arguments_intact(self):
        arguments = {"command": CANARIES["aws_access_key"]}
        self.assertEqual(redact.Redactor(full=True).arguments(arguments), arguments)


class TestExtraPatterns(unittest.TestCase):
    """REQ-019: config may extend the pattern set."""

    def test_a_configured_pattern_is_applied(self):
        redactor = redact.Redactor(extra_patterns=[r"INTERNAL-[0-9]{4}"])
        self.assertNotIn("INTERNAL-4711", redactor.text("ticket INTERNAL-4711 is blocked"))

    def test_an_invalid_pattern_is_rejected_loudly(self):
        with self.assertRaises(ValueError):
            redact.Redactor(extra_patterns=["("])


class TestCanaryScan(unittest.TestCase):
    """AC-042, over a temporary tree standing in for the output root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_scan_finds_a_planted_canary(self):
        (self.root / "proposals").mkdir()
        (self.root / "proposals" / "p1.json").write_text(
            json.dumps({"excerpt": CANARIES["github_token"]}), encoding="utf-8"
        )

        hits = redact.scan_for_canaries(self.root, CANARIES.values())

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path.name, "p1.json")
        self.assertEqual(hits[0].canary, CANARIES["github_token"])

    def test_scan_is_clean_once_everything_passed_the_boundary(self):
        redactor = redact.Redactor()
        (self.root / "proposals").mkdir()
        (self.root / "runs").mkdir()
        (self.root / "proposals" / "p1.json").write_text(
            json.dumps(
                {
                    "excerpt": redactor.excerpt(CANARIES["jwt"]),
                    "command": redactor.command(f"demo-cli auth {CANARIES['github_token']}"),
                    "arguments": redactor.arguments({"key": CANARIES["aws_secret_key"]}),
                    "path": redactor.path(f"/tmp/{CANARIES['email']}/x.jsonl"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.root / "runs" / "r1.json").write_text(
            redactor.text(f"summary: {CANARIES['url_credentials']}"), encoding="utf-8"
        )
        (self.root / "packet.md").write_text(
            redactor.text(f"- {CANARIES['private_key']}"), encoding="utf-8"
        )

        self.assertEqual(redact.scan_for_canaries(self.root, CANARIES.values()), [])

    def test_scan_walks_nested_directories(self):
        deep = self.root / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "leak.md").write_text(CANARIES["slack_token"], encoding="utf-8")

        self.assertEqual(len(redact.scan_for_canaries(self.root, CANARIES.values())), 1)

    def test_scan_tolerates_binary_files(self):
        (self.root / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
        self.assertEqual(redact.scan_for_canaries(self.root, CANARIES.values()), [])

    def test_scan_of_a_missing_root_is_empty(self):
        self.assertEqual(redact.scan_for_canaries(self.root / "nope", ["x"]), [])


class TestWrittenOutputRoot(unittest.TestCase):
    """AC-042 and AC-006 over a real scan — the halves deferred from T007/T018.

    Every other test in this file checks a function's return value. This one
    checks the artefact: it runs the CLI over transcripts stuffed with canaries
    and then greps the output root, without caring which module wrote what or
    whether that module remembered the boundary.
    """

    def setUp(self):
        import io
        import shutil

        from pi_self_improvement import cli

        self._cli = cli
        self._io = io
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.output_root = self.home / ".pi-self-improvement"
        sessions = self.home / ".pi" / "agent" / "sessions"
        sessions.mkdir(parents=True)
        self._shutil = shutil
        self._seed(sessions)
        self.addCleanup(self._tmp.cleanup)

    def _seed(self, sessions: Path) -> None:
        """Two sessions whose commands and outputs are made of canaries."""
        for index, (name, value) in enumerate(sorted(CANARIES.items())):
            slug = f"--tmp-canary-{index % 2}--"
            path = sessions / slug / f"2026-02-0{1 + index % 8}T10-00-00-000Z_canary-{index}.jsonl"
            support.write_jsonl(
                path,
                [
                    support.session_record(f"canary-{index}", cwd="/tmp/canary-repo"),
                    support.user_record(f"這樣不對，應該用 {value}"),
                    support.tool_call_record(
                        f"c{index}", "bash", {"command": f"demoext-cli send --token {value}"}
                    ),
                    support.tool_result_record(
                        f"c{index}", "bash", f"failed: rejected {value}", is_error=True
                    ),
                    support.tool_call_record(
                        f"x{index}", "demoext_search", {"query": value}
                    ),
                    support.tool_result_record(
                        f"x{index}", "demoext_search", f"error: {value} not found", is_error=True
                    ),
                ],
            )

    def scan(self, *argv):
        out, err = self._io.StringIO(), self._io.StringIO()
        code = self._cli.main(
            ["--home", str(self.home), "--all", *argv], stdout=out, stderr=err
        )
        self.assertEqual(code, 0, msg=err.getvalue())
        return out.getvalue()

    def written_files(self):
        return [path for path in self.output_root.rglob("*") if path.is_file()]

    def test_no_canary_survives_anywhere_under_the_output_root(self):
        self.scan()

        self.assertTrue(self.written_files(), "the scan wrote nothing to check")
        hits = redact.scan_for_canaries(self.output_root, CANARIES.values())

        self.assertEqual(
            hits, [], f"secret canaries reached disk: {[hit.canary for hit in hits]}"
        )

    def test_the_scan_really_did_stage_evidence_from_those_transcripts(self):
        """Guards the test above: a scan that staged nothing would pass it."""
        self.scan()

        proposals = list((self.output_root / "proposals").rglob("*.json"))
        evidence = [
            item
            for path in proposals
            for item in json.loads(path.read_text(encoding="utf-8"))["evidence"]
        ]

        self.assertTrue(proposals)
        self.assertTrue(evidence)

    def test_full_marks_local_only_in_all_three_outputs(self):
        """AC-006 through the CLI."""
        self.scan("--full")

        run = json.loads(
            next((self.output_root / "runs").glob("*.json")).read_text(encoding="utf-8")
        )
        proposal = json.loads(
            next((self.output_root / "proposals").rglob("*.json")).read_text(encoding="utf-8")
        )
        packet = next((self.output_root / "review-packets").glob("*.md")).read_text("utf-8")

        self.assertTrue(run["local_only"])
        self.assertTrue(proposal["local_only"])
        self.assertIn("LOCAL ONLY", packet)

    def test_full_is_the_only_way_a_canary_reaches_disk(self):
        """--full is documented as bypassing the boundary. If this ever stops
        finding canaries, --full silently stopped doing what it claims."""
        self.scan("--full")

        hits = redact.scan_for_canaries(self.output_root, CANARIES.values())

        self.assertTrue(hits, "--full should preserve the raw text it warns about")

    def test_a_default_run_is_not_marked_local_only(self):
        self.scan()

        run = json.loads(
            next((self.output_root / "runs").glob("*.json")).read_text(encoding="utf-8")
        )

        self.assertFalse(run["local_only"])


if __name__ == "__main__":
    unittest.main()

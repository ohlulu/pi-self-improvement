import tempfile
import unittest
from pathlib import Path

from pi_self_improvement import detect, state
from pi_self_improvement.model import Evidence, ParseCounts
from pi_self_improvement.route import Proposal

BEFORE = "2026-01-05T09:00:00Z"
WATERMARK = "2026-02-01T00:00:00Z"
AFTER = "2026-03-09T09:00:00Z"


def signal(line=4, timestamp=BEFORE, session="s1"):
    return detect.Signal(
        kind=detect.FAILURE,
        subject="demo-cli",
        evidence=Evidence(
            source=detect.FAILURE,
            path=f"sessions/{session}.jsonl",
            line=line,
            excerpt="boom",
            timestamp=timestamp,
            session_id=session,
        ),
        detail={},
    )


def proposal(target="demo-cli", route="tool", *, signals=None):
    return Proposal(
        route=route, target=target, signals=list(signals or [signal()]), summary="it failed"
    )


def resolution(key="tool:demo-cli", decision=state.FIXED, at=WATERMARK):
    return state.Resolutions({key: state.Resolution(key=key, decision=decision, resolved_at=at)})


class TestProposalIdentity(unittest.TestCase):
    """REQ-016: deterministic ids from route, target and evidence references."""

    def test_the_same_proposal_yields_the_same_id(self):
        self.assertEqual(state.proposal_id(proposal()), state.proposal_id(proposal()))

    def test_evidence_order_does_not_change_the_id(self):
        a = proposal(signals=[signal(line=4), signal(line=9)])
        b = proposal(signals=[signal(line=9), signal(line=4)])

        self.assertEqual(state.proposal_id(a), state.proposal_id(b))

    def test_a_different_target_yields_a_different_id(self):
        self.assertNotEqual(state.proposal_id(proposal("a")), state.proposal_id(proposal("b")))

    def test_a_different_route_yields_a_different_id(self):
        self.assertNotEqual(
            state.proposal_id(proposal(route="tool")), state.proposal_id(proposal(route="backlog"))
        )

    def test_different_evidence_yields_a_different_id(self):
        self.assertNotEqual(
            state.proposal_id(proposal(signals=[signal(line=4)])),
            state.proposal_id(proposal(signals=[signal(line=9)])),
        )


class TestSeenFilter(unittest.TestCase):
    """AC-029."""

    def test_the_same_evidence_is_not_staged_twice(self):
        blank = state.State()
        first = state.run_pipeline([proposal()], state=blank)
        blank.record(first.staged, "R1")

        second = state.run_pipeline([proposal()], state=blank)

        self.assertEqual(len(first.staged), 1)
        self.assertEqual(second.staged, [])
        self.assertEqual(second.suppressed_seen, 1)

    def test_new_evidence_on_a_known_target_is_staged_again(self):
        blank = state.State()
        blank.record(state.run_pipeline([proposal()], state=blank).staged, "R1")

        grown = proposal(signals=[signal(line=4), signal(line=9)])
        result = state.run_pipeline([grown], state=blank)

        self.assertEqual(len(result.staged), 1)

    def test_include_seen_overrides_the_filter(self):
        blank = state.State()
        blank.record(state.run_pipeline([proposal()], state=blank).staged, "R1")

        result = state.run_pipeline([proposal()], state=blank, include_seen=True)

        self.assertEqual(len(result.staged), 1)


class TestRecurrence(unittest.TestCase):
    """AC-030."""

    def test_a_target_seen_in_a_previous_run_is_annotated(self):
        store = state.State()
        store.record(state.run_pipeline([proposal()], state=store).staged, "R1")

        result = state.run_pipeline([proposal(signals=[signal(line=9)])], state=store)

        self.assertEqual(result.staged[0].previous_runs, 1)
        self.assertTrue(result.staged[0].recurring)

    def test_a_first_sighting_is_not_recurring(self):
        result = state.run_pipeline([proposal()], state=state.State())

        self.assertEqual(result.staged[0].previous_runs, 0)
        self.assertFalse(result.staged[0].recurring)

    def test_recurrence_counts_runs_not_evidence(self):
        store = state.State()
        for run, line in (("R1", 4), ("R2", 9), ("R3", 12)):
            store.record(
                state.run_pipeline([proposal(signals=[signal(line=line)])], state=store).staged, run
            )

        result = state.run_pipeline([proposal(signals=[signal(line=20)])], state=store)

        self.assertEqual(result.staged[0].previous_runs, 3)

    def test_recording_the_same_run_twice_does_not_inflate_the_count(self):
        store = state.State()
        staged = state.run_pipeline([proposal()], state=store).staged
        store.record(staged, "R1")
        store.record(staged, "R1")

        self.assertEqual(store.previous_runs("tool:demo-cli"), 1)


class TestResolutionFilter(unittest.TestCase):
    """AC-031, AC-049."""

    def test_evidence_before_the_watermark_is_suppressed(self):
        result = state.run_pipeline(
            [proposal(signals=[signal(timestamp=BEFORE)])],
            state=state.State(),
            resolutions=resolution(),
        )

        self.assertEqual(result.staged, [])
        self.assertEqual(result.suppressed_resolved, 1)

    def test_evidence_after_the_watermark_returns_as_a_regression(self):
        result = state.run_pipeline(
            [proposal(signals=[signal(timestamp=AFTER)])],
            state=state.State(),
            resolutions=resolution(),
        )

        self.assertEqual(len(result.staged), 1)
        self.assertTrue(result.staged[0].regression)

    def test_only_the_post_watermark_evidence_survives(self):
        mixed = proposal(signals=[signal(line=4, timestamp=BEFORE), signal(line=9, timestamp=AFTER)])

        result = state.run_pipeline([mixed], state=state.State(), resolutions=resolution())

        self.assertEqual([e.line for e in result.staged[0].proposal.evidence], [9])

    def test_wontfix_suppresses_even_new_evidence(self):
        result = state.run_pipeline(
            [proposal(signals=[signal(timestamp=AFTER)])],
            state=state.State(),
            resolutions=resolution(decision=state.WONTFIX),
        )

        self.assertEqual(result.staged, [])

    def test_ignored_suppresses_even_new_evidence(self):
        result = state.run_pipeline(
            [proposal(signals=[signal(timestamp=AFTER)])],
            state=state.State(),
            resolutions=resolution(decision=state.IGNORED),
        )

        self.assertEqual(result.staged, [])

    def test_an_unresolved_target_passes_through_untouched(self):
        result = state.run_pipeline(
            [proposal("other")], state=state.State(), resolutions=resolution()
        )

        self.assertEqual(len(result.staged), 1)
        self.assertFalse(result.staged[0].regression)

    def test_include_resolved_overrides_the_filter(self):
        result = state.run_pipeline(
            [proposal(signals=[signal(timestamp=BEFORE)])],
            state=state.State(),
            resolutions=resolution(decision=state.WONTFIX),
            include_resolved=True,
        )

        self.assertEqual(len(result.staged), 1)

    def test_undated_evidence_cannot_reopen_a_resolution(self):
        """A resolution is an explicit human decision; undated evidence cannot
        prove it is newer than the watermark."""
        result = state.run_pipeline(
            [proposal(signals=[signal(timestamp=None)])],
            state=state.State(),
            resolutions=resolution(),
        )

        self.assertEqual(result.staged, [])


class TestPipelineOrder(unittest.TestCase):
    """AC-047 — the reason DEC-017 fixes the order.

    Seen-filtering first would swallow the regression before the resolution
    filter ever ran, so a `fixed` target could never come back.
    """

    def test_a_regression_survives_a_seen_key_covering_the_same_evidence(self):
        """The discriminating case for DEC-017.

        The whole evidence set was staged in an earlier run, so its id is already
        in `seen`. The user then resolved the target with a backdated watermark
        (`--resolved-at`, a supported flow) that falls between the two evidence
        items. Filtering seen keys first would match that stored id and drop the
        proposal before the resolution filter could turn it into a regression.

        A weaker version of this test — staging only the pre-watermark evidence —
        passes under either order and proves nothing.
        """
        store = state.State()
        both = [signal(line=4, timestamp=BEFORE), signal(line=9, timestamp=AFTER)]
        store.record(state.run_pipeline([proposal(signals=both)], state=store).staged, "R1")

        rescanned = proposal(signals=both)
        unfiltered_id = state.proposal_id(rescanned)
        result = state.run_pipeline([rescanned], state=store, resolutions=resolution())

        # The precondition that makes the order observable.
        self.assertTrue(store.has_seen(unfiltered_id))
        self.assertEqual(len(result.staged), 1)
        self.assertTrue(result.staged[0].regression)
        self.assertNotEqual(result.staged[0].id, unfiltered_id)
        self.assertEqual([e.line for e in result.staged[0].proposal.evidence], [9])

    def test_the_resolution_filter_runs_before_the_seen_filter(self):
        """A suppressed proposal is counted as resolved, never as seen."""
        store = state.State()
        store.record(state.run_pipeline([proposal()], state=store).staged, "R1")

        result = state.run_pipeline([proposal()], state=store, resolutions=resolution())

        self.assertEqual(result.suppressed_resolved, 1)
        self.assertEqual(result.suppressed_seen, 0)

    def test_regressions_are_ordered_ahead_of_recurring_and_new(self):
        store = state.State()
        store.record(state.run_pipeline([proposal("known")], state=store).staged, "R1")

        result = state.run_pipeline(
            [
                proposal("fresh"),
                proposal("known", signals=[signal(line=9)]),
                proposal(signals=[signal(timestamp=AFTER)]),
            ],
            state=store,
            resolutions=resolution(),
        )

        self.assertEqual(
            [item.target for item in result.staged], ["demo-cli", "known", "fresh"]
        )


class TestResolveAndTrim(unittest.TestCase):
    """AC-049 second half, driven through the real flow.

    An earlier version of this assertion lived in test_stage.py and hand-built a
    Staged with previous_runs=0, so it passed while the actual pipeline reported
    "also flagged in 5 previous run(s)" on a first regression. Build the history
    with run_pipeline/record or the test proves nothing.
    """

    def accumulate(self, runs=5):
        store = state.State()
        for index in range(1, runs + 1):
            item = proposal(signals=[signal(line=index, timestamp=f"2026-01-0{index}T09:00:00Z")])
            store.record(
                state.run_pipeline([item], state=store).staged,
                f"R{index}",
                at=f"2026-01-0{index}T09:00:00Z",
            )
        return store

    def test_a_first_regression_reports_no_previous_runs(self):
        store = self.accumulate()
        registry = state.Resolutions()
        registry.resolve("tool:demo-cli", state.FIXED, resolved_at=WATERMARK, state=store)

        result = state.run_pipeline(
            [proposal(signals=[signal(line=99, timestamp=AFTER)])],
            state=store,
            resolutions=registry,
        )

        self.assertTrue(result.staged[0].regression)
        self.assertEqual(result.staged[0].previous_runs, 0)
        self.assertFalse(result.staged[0].recurring)

    def test_resolving_drops_history_from_before_the_watermark(self):
        store = self.accumulate()
        self.assertEqual(store.previous_runs("tool:demo-cli"), 5)

        state.Resolutions().resolve(
            "tool:demo-cli", state.FIXED, resolved_at=WATERMARK, state=store
        )

        self.assertEqual(store.previous_runs("tool:demo-cli"), 0)

    def test_history_after_the_watermark_is_kept(self):
        store = self.accumulate(runs=2)
        store.record(
            state.run_pipeline([proposal(signals=[signal(line=50)])], state=store).staged,
            "R9",
            at=AFTER,
        )

        state.Resolutions().resolve(
            "tool:demo-cli", state.FIXED, resolved_at=WATERMARK, state=store
        )

        self.assertEqual(store.previous_runs("tool:demo-cli"), 1)

    def test_a_second_regression_does_report_recurrence(self):
        """Trimming forgets the pre-fix past, not the post-fix present."""
        store = self.accumulate()
        registry = state.Resolutions()
        registry.resolve("tool:demo-cli", state.FIXED, resolved_at=WATERMARK, state=store)
        first = state.run_pipeline(
            [proposal(signals=[signal(line=99, timestamp=AFTER)])],
            state=store,
            resolutions=registry,
        )
        store.record(first.staged, "R6", at=AFTER)

        second = state.run_pipeline(
            [proposal(signals=[signal(line=101, timestamp=AFTER)])],
            state=store,
            resolutions=registry,
        )

        self.assertEqual(second.staged[0].previous_runs, 1)

    def test_another_targets_history_is_untouched(self):
        store = self.accumulate()
        store.record(state.run_pipeline([proposal("other")], state=store).staged, "R1", at=BEFORE)

        state.Resolutions().resolve(
            "tool:demo-cli", state.FIXED, resolved_at=WATERMARK, state=store
        )

        self.assertEqual(store.previous_runs("tool:other"), 1)

    def test_an_unknown_decision_is_refused(self):
        with self.assertRaises(ValueError):
            state.Resolutions().resolve("tool:x", "maybe")

    def test_unresolve_removes_the_entry(self):
        registry = resolution()

        self.assertTrue(registry.unresolve("tool:demo-cli"))
        self.assertIsNone(registry.get("tool:demo-cli"))
        self.assertFalse(registry.unresolve("tool:demo-cli"))

    def test_an_unresolved_target_is_scanned_again(self):
        registry = resolution()
        registry.unresolve("tool:demo-cli")

        result = state.run_pipeline(
            [proposal(signals=[signal(timestamp=BEFORE)])],
            state=state.State(),
            resolutions=registry,
        )

        self.assertEqual(len(result.staged), 1)


class TestDecisionsImport(unittest.TestCase):
    """AC-032."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, payload):
        import json

        path = self.root / "decisions.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_decisions_are_imported_into_the_registry(self):
        path = self.write(
            {
                "decisions": [
                    {"key": "tool:demo-cli", "decision": "fixed", "resolved_at": WATERMARK},
                    {"key": "backlog:someprog", "decision": "wontfix"},
                ]
            }
        )
        registry = state.Resolutions()

        imported = registry.import_decisions(path)

        self.assertEqual(sorted(imported), ["backlog:someprog", "tool:demo-cli"])
        self.assertEqual(registry.get("tool:demo-cli").decision, state.FIXED)
        self.assertEqual(registry.get("backlog:someprog").decision, state.WONTFIX)

    def test_open_and_deferred_rows_are_not_imported(self):
        """Importing an undecided row would suppress a proposal still under
        discussion."""
        path = self.write(
            {
                "decisions": [
                    {"key": "tool:a", "decision": "open"},
                    {"key": "tool:b", "decision": "deferred"},
                    {"key": "tool:c", "decision": "fixed"},
                ]
            }
        )
        registry = state.Resolutions()

        imported = registry.import_decisions(path)

        self.assertEqual(imported, ["tool:c"])
        self.assertIsNone(registry.get("tool:a"))
        self.assertIsNone(registry.get("tool:b"))

    def test_import_preserves_metadata(self):
        path = self.write(
            {
                "decisions": [
                    {
                        "key": "tool:demo-cli",
                        "decision": "fixed",
                        "resolved_at": WATERMARK,
                        "pr": "#42",
                        "note": "pinned the version",
                        "by": "reviewer",
                    }
                ]
            }
        )
        registry = state.Resolutions()

        registry.import_decisions(path)
        entry = registry.get("tool:demo-cli")

        self.assertEqual(entry.pr, "#42")
        self.assertEqual(entry.note, "pinned the version")
        self.assertEqual(entry.by, "reviewer")

    def test_import_trims_recurrence_history_too(self):
        store = state.State()
        store.record(state.run_pipeline([proposal()], state=store).staged, "R1", at=BEFORE)
        path = self.write(
            {"decisions": [{"key": "tool:demo-cli", "decision": "fixed", "resolved_at": WATERMARK}]}
        )

        state.Resolutions().import_decisions(path, state=store)

        self.assertEqual(store.previous_runs("tool:demo-cli"), 0)

    def test_a_bare_list_is_accepted(self):
        path = self.write([{"key": "tool:demo-cli", "decision": "fixed"}])

        self.assertEqual(state.Resolutions().import_decisions(path), ["tool:demo-cli"])

    def test_malformed_rows_are_skipped_rather_than_fatal(self):
        path = self.write({"decisions": ["nonsense", {}, {"decision": "fixed"}, {"key": "tool:x"}]})

        self.assertEqual(state.Resolutions().import_decisions(path), [])

    def test_a_missing_file_imports_nothing(self):
        self.assertEqual(state.Resolutions().import_decisions(self.root / "nope.json"), [])


class TestSelfCheck(unittest.TestCase):
    """AC-033 / REQ-018."""

    def test_zero_tool_calls_from_parsed_transcripts_warns(self):
        warnings = state.self_check(ParseCounts(files=12, tool_calls=0))

        self.assertEqual(len(warnings), 1)
        self.assertIn("0 tool calls", warnings[0])

    def test_a_healthy_scan_warns_about_nothing(self):
        self.assertEqual(state.self_check(ParseCounts(files=12, tool_calls=340)), [])

    def test_no_transcripts_at_all_is_not_a_parser_warning(self):
        """An empty source directory is a usage question, not a broken parser."""
        self.assertEqual(state.self_check(ParseCounts(files=0, tool_calls=0)), [])

    def test_parse_errors_are_surfaced(self):
        warnings = state.self_check(ParseCounts(files=3, tool_calls=9, parse_errors=4))

        self.assertTrue(any("failed to parse" in warning for warning in warnings))

    def test_non_canonical_files_are_surfaced(self):
        warnings = state.self_check(ParseCounts(files=3, tool_calls=9, non_canonical_files=2))

        self.assertTrue(any("non-canonical" in warning for warning in warnings))

    def test_the_counts_block_accounts_for_every_transcript(self):
        """AC-050: root + subagent equals the number of files parsed."""
        counts = ParseCounts(files=9, root_sessions=6, subagent_sessions=3, branch_points=2)
        counts.skip("label", 4)
        payload = counts.to_dict()

        self.assertEqual(payload["root_sessions"] + payload["subagent_sessions"], payload["files"])
        for field in ("branch_points", "aborted_turns", "error_turns", "dangling_tool_calls"):
            self.assertIn(field, payload)
        self.assertEqual(payload["skipped_records"]["label"], 4)


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_state_survives_a_round_trip(self):
        store = state.State()
        store.record(state.run_pipeline([proposal()], state=store).staged, "R1")
        path = self.root / "state.json"

        store.save(path)
        reloaded = state.State.load(path)

        self.assertEqual(reloaded.previous_runs("tool:demo-cli"), 1)
        self.assertEqual(reloaded.seen, store.seen)

    def test_resolutions_survive_a_round_trip(self):
        path = self.root / "resolutions.json"

        resolution().save(path)
        reloaded = state.Resolutions.load(path)

        self.assertEqual(reloaded.get("tool:demo-cli").decision, state.FIXED)

    def test_a_missing_file_loads_as_empty(self):
        self.assertEqual(state.State.load(self.root / "nope.json").seen, {})
        self.assertEqual(state.Resolutions.load(self.root / "nope.json").entries, {})

    def test_a_corrupt_file_loads_as_empty_rather_than_crashing(self):
        path = self.root / "state.json"
        path.write_text("{not json", encoding="utf-8")

        self.assertEqual(state.State.load(path).seen, {})


class TestTimestamps(unittest.TestCase):
    def test_a_trailing_z_parses_on_every_supported_python(self):
        self.assertIsNotNone(state.parse_timestamp("2026-01-05T09:00:00Z"))

    def test_fractional_seconds_parse(self):
        self.assertIsNotNone(state.parse_timestamp("2026-01-05T09:00:00.123Z"))

    def test_an_offset_parses(self):
        self.assertIsNotNone(state.parse_timestamp("2026-01-05T09:00:00+08:00"))

    def test_garbage_is_none_rather_than_an_exception(self):
        self.assertIsNone(state.parse_timestamp("not a time"))
        self.assertIsNone(state.parse_timestamp(None))

    def test_a_naive_timestamp_is_comparable_with_an_aware_one(self):
        naive = state.parse_timestamp("2026-03-09T09:00:00")
        aware = state.parse_timestamp(WATERMARK)

        self.assertGreater(naive, aware)


if __name__ == "__main__":
    unittest.main()

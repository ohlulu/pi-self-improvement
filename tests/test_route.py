import tempfile
import unittest
from pathlib import Path

from pi_self_improvement import detect, route
from pi_self_improvement.model import Evidence
from pi_self_improvement.redact import Redactor


def signal(kind, subject, *, session="s1", line=10, cwd=None, **detail):
    payload = {"tool": detail.pop("tool", subject), "tracked": detail.pop("tracked", False)}
    payload.setdefault("backlog_eligible", detail.pop("backlog_eligible", True))
    if cwd is not None:
        payload["cwd"] = cwd
    payload.update(detail)
    return detect.Signal(
        kind=kind,
        subject=subject,
        evidence=Evidence(
            source=kind,
            path=f"sessions/{session}.jsonl",
            line=line,
            excerpt="…",
            session_id=session,
        ),
        detail=payload,
    )


class RouteTestCase(unittest.TestCase):
    config = route.RouteConfig()

    def build(self, *signals, config=None):
        return route.build_proposals(signals, config=config or self.config, redactor=Redactor())

    def keys(self, *signals, config=None):
        return sorted(proposal.key for proposal in self.build(*signals, config=config))


class TestFourRoutes(RouteTestCase):
    def test_a_correction_after_a_skill_becomes_a_skill_improvement(self):
        """AC-022."""
        keys = self.keys(
            signal(detect.SKILL, "sample-skill", line=4),
            signal(detect.CORRECTION, "/tmp/x", line=9, cwd="/tmp/x"),
        )

        self.assertEqual(keys, ["skill_improvement:sample-skill"])

    def test_a_skill_loaded_after_the_correction_does_not_count(self):
        """The skill has to have been loaded before the user complained."""
        proposals = self.build(
            signal(detect.SKILL, "sample-skill", line=20),
            signal(detect.CORRECTION, "/tmp/x", line=9, cwd="/tmp/x"),
        )

        self.assertEqual([p.route for p in proposals], [route.ROUTE_MEMORY])

    def test_a_correction_with_no_skill_becomes_memory_context(self):
        """AC-023."""
        with tempfile.TemporaryDirectory() as tmp:
            proposals = self.build(signal(detect.CORRECTION, tmp, line=9, cwd=tmp))

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].route, route.ROUTE_MEMORY)

    def test_a_tracked_cli_failure_becomes_a_tool_proposal(self):
        """AC-011 through routing."""
        keys = self.keys(signal(detect.FAILURE, "demo-cli", tool="bash", tracked=True))

        self.assertEqual(keys, ["tool:demo-cli"])

    def test_an_untracked_failure_across_two_sessions_becomes_backlog(self):
        """AC-024: the backlog is for what recurs."""
        keys = self.keys(
            signal(detect.FAILURE, "someprog", tool="bash", session="s1"),
            signal(detect.FAILURE, "someprog", tool="bash", session="s2"),
        )

        self.assertEqual(keys, ["backlog:someprog"])

    def test_a_one_off_untracked_failure_is_discarded(self):
        """REQ-013: an unclassifiable one-off is dropped, not staged."""
        self.assertEqual(self.keys(signal(detect.FAILURE, "someprog", tool="bash")), [])

    def test_repeats_within_one_session_are_still_one_session(self):
        keys = self.keys(
            signal(detect.FAILURE, "someprog", tool="bash", session="s1", line=3),
            signal(detect.FAILURE, "someprog", tool="bash", session="s1", line=9),
        )

        self.assertEqual(keys, [])

    def test_a_skill_signal_alone_produces_no_proposal(self):
        self.assertEqual(self.keys(signal(detect.SKILL, "sample-skill", line=4)), [])

    def test_a_subagent_failure_never_reaches_the_backlog(self):
        """AC-021, enforced at the routing boundary."""
        keys = self.keys(
            signal(detect.FAILURE, "someprog", tool="bash", session="s1", backlog_eligible=False),
            signal(detect.FAILURE, "someprog", tool="bash", session="s2", backlog_eligible=False),
        )

        self.assertEqual(keys, [])

    def test_a_subagent_failure_on_a_tracked_cli_still_routes_to_tool(self):
        """REQ-012 excludes subagents from the backlog route only."""
        keys = self.keys(
            signal(detect.FAILURE, "demo-cli", tool="bash", tracked=True, backlog_eligible=False)
        )

        self.assertEqual(keys, ["tool:demo-cli"])

    def test_shell_noise_is_never_a_backlog_entry(self):
        keys = self.keys(
            signal(detect.FAILURE, "cd", tool="bash", session="s1"),
            signal(detect.FAILURE, "cd", tool="bash", session="s2"),
        )

        self.assertEqual(keys, [])

    def test_config_can_extend_the_backlog_ignore_list(self):
        config = route.RouteConfig(extra_backlog_ignore=("someprog",))
        keys = self.keys(
            signal(detect.FAILURE, "someprog", tool="bash", session="s1"),
            signal(detect.FAILURE, "someprog", tool="bash", session="s2"),
            config=config,
        )

        self.assertEqual(keys, [])

    def test_proposals_are_ordered_by_route_then_target(self):
        proposals = self.build(
            signal(detect.FAILURE, "someprog", tool="bash", session="s1"),
            signal(detect.FAILURE, "someprog", tool="bash", session="s2"),
            signal(detect.FAILURE, "demo-cli", tool="bash", tracked=True),
        )

        self.assertEqual([p.route for p in proposals], [route.ROUTE_TOOL, route.ROUTE_BACKLOG])


class TestCorrectionCap(RouteTestCase):
    """AC-023: `MAX_CORRECTIONS_PER_SESSION`."""

    def test_a_session_contributes_at_most_three_corrections(self):
        signals = [
            signal(detect.CORRECTION, "/tmp/x", line=line, cwd="/tmp/x") for line in (3, 5, 7, 9, 11)
        ]

        proposals = self.build(*signals)

        self.assertEqual(sum(len(p.signals) for p in proposals), 3)

    def test_the_cap_is_per_session_not_global(self):
        signals = [
            signal(detect.CORRECTION, "/tmp/x", line=line, cwd="/tmp/x", session=session)
            for session in ("s1", "s2")
            for line in (3, 5, 7, 9)
        ]

        proposals = self.build(*signals)

        self.assertEqual(sum(len(p.signals) for p in proposals), 6)

    def test_the_cap_is_configurable(self):
        signals = [
            signal(detect.CORRECTION, "/tmp/x", line=line, cwd="/tmp/x") for line in (3, 5, 7, 9)
        ]

        proposals = self.build(*signals, config=route.RouteConfig(max_corrections_per_session=1))

        self.assertEqual(sum(len(p.signals) for p in proposals), 1)

    def test_the_earliest_corrections_are_the_ones_kept(self):
        signals = [
            signal(detect.CORRECTION, "/tmp/x", line=line, cwd="/tmp/x") for line in (11, 3, 7, 5)
        ]

        proposals = self.build(*signals, config=route.RouteConfig(max_corrections_per_session=2))
        lines = sorted(s.evidence.line for p in proposals for s in p.signals)

        self.assertEqual(lines, [3, 5])


class TestRepositoryRoot(unittest.TestCase):
    """ADR-0005 normalization."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def test_a_subdirectory_normalizes_to_the_repo_root(self):
        repo = self.tmp / "project"
        (repo / ".git").mkdir(parents=True)
        deep = repo / "src" / "module"
        deep.mkdir(parents=True)

        self.assertEqual(route.repository_root(str(deep), home=None), str(repo))

    def test_a_repo_and_its_worktree_share_one_target(self):
        """AC-046 — the case ADR-0005 exists for.

        A worktree lives at an unrelated path, so unnormalized it becomes a second
        target for the same codebase and neither half ever recurs.
        """
        main = self.tmp / "project"
        (main / ".git" / "worktrees" / "feature").mkdir(parents=True)
        worktree = self.tmp / "project-feature"
        worktree.mkdir()
        (worktree / ".git").write_text(
            f"gitdir: {main / '.git' / 'worktrees' / 'feature'}\n", encoding="utf-8"
        )

        self.assertEqual(
            route.repository_root(str(worktree), home=None),
            route.repository_root(str(main), home=None),
        )

    def test_a_symlink_resolves_to_its_target(self):
        repo = self.tmp / "project"
        (repo / ".git").mkdir(parents=True)
        link = self.tmp / "link"
        link.symlink_to(repo)

        self.assertEqual(route.repository_root(str(link), home=None), str(repo))

    def test_a_directory_outside_a_repo_is_its_own_root(self):
        plain = self.tmp / "notes"
        plain.mkdir()

        self.assertEqual(route.repository_root(str(plain), home=None), str(plain))

    def test_no_path_at_all_is_unknown(self):
        self.assertEqual(route.repository_root(None), route.UNKNOWN_TARGET)
        self.assertEqual(route.repository_root(""), route.UNKNOWN_TARGET)

    def test_the_home_prefix_is_shortened(self):
        repo = self.tmp / "project"
        (repo / ".git").mkdir(parents=True)

        self.assertEqual(
            route.repository_root(str(repo), home=str(self.tmp)), "~/project"
        )

    def test_case_is_preserved(self):
        repo = self.tmp / "MyProject"
        (repo / ".git").mkdir(parents=True)

        self.assertIn("MyProject", route.repository_root(str(repo), home=None))

    def test_two_corrections_from_a_repo_and_its_worktree_group_together(self):
        main = self.tmp / "project"
        (main / ".git" / "worktrees" / "feature").mkdir(parents=True)
        worktree = self.tmp / "project-feature"
        worktree.mkdir()
        (worktree / ".git").write_text(
            f"gitdir: {main / '.git' / 'worktrees' / 'feature'}\n", encoding="utf-8"
        )

        proposals = route.build_proposals(
            [
                signal(detect.CORRECTION, "x", session="s1", line=5, cwd=str(main)),
                signal(detect.CORRECTION, "x", session="s2", line=5, cwd=str(worktree)),
            ],
            config=route.RouteConfig(home=None),
            redactor=Redactor(),
        )

        self.assertEqual(len(proposals), 1)
        self.assertEqual(len(proposals[0].signals), 2)


class TestExtensionFamilies(RouteTestCase):
    """REQ-014 / DEC-007."""

    def test_tools_from_one_extension_merge_into_one_target(self):
        """AC-025."""
        keys = self.keys(
            signal(detect.FAILURE, "jira_search_issues", tool="jira_search_issues"),
            signal(detect.FAILURE, "jira_get_issue", tool="jira_get_issue"),
        )

        self.assertEqual(keys, ["tool:ext:jira"])

    def test_a_builtin_never_becomes_a_family(self):
        """AC-026."""
        self.assertIsNone(route.ext_family("read"))
        self.assertEqual(self.keys(signal(detect.FAILURE, "read", tool="read")), [])

    def test_a_single_word_extension_tool_is_not_a_family(self):
        self.assertIsNone(route.ext_family("mysterytool"))

    def test_the_config_map_overrides_a_tool(self):
        config = route.RouteConfig(ext_family_map={"weird_name_here": "jira"})

        self.assertEqual(route.ext_family("weird_name_here", config), "jira")

    def test_the_config_map_overrides_a_family(self):
        config = route.RouteConfig(ext_family_map={"jira": "atlassian"})

        self.assertEqual(route.ext_family("jira_get_issue", config), "atlassian")

    def test_a_builtin_can_be_added_by_config(self):
        config = route.RouteConfig(builtin_tools=route.DEFAULT_BUILTIN_TOOLS | {"house_tool"})

        self.assertIsNone(route.ext_family("house_tool", config))

    def test_extension_failures_bypass_the_backlog_recurrence_rule(self):
        """An extension family is an identified target, not an unclassified one."""
        keys = self.keys(signal(detect.FAILURE, "jira_get_issue", tool="jira_get_issue"))

        self.assertEqual(keys, ["tool:ext:jira"])


class TestSummaries(RouteTestCase):
    def test_a_tool_summary_carries_counts_and_session_span(self):
        proposals = self.build(
            signal(detect.FAILURE, "demo-cli", tool="bash", tracked=True, session="s1"),
            signal(detect.FAILURE, "demo-cli", tool="bash", tracked=True, session="s2"),
        )

        self.assertIn("failed 2 time(s)", proposals[0].summary)
        self.assertIn("2 session(s)", proposals[0].summary)

    def test_a_retry_is_noted_in_the_summary(self):
        """AC-012."""
        proposals = self.build(
            signal(detect.FAILURE, "demo-cli", tool="bash", tracked=True),
            signal(
                detect.RETRY,
                "demo-cli",
                tool="bash",
                tracked=True,
                line=12,
                subcommand="sync",
                attempts=3,
                flag_combinations=3,
            ),
        )

        self.assertIn("retried 3 time(s)", proposals[0].summary)
        self.assertIn("sync", proposals[0].summary)

    def test_a_skill_summary_names_the_skill(self):
        proposals = self.build(
            signal(detect.SKILL, "sample-skill", line=4),
            signal(detect.CORRECTION, "/tmp/x", line=9, cwd="/tmp/x"),
        )

        self.assertIn("`sample-skill`", proposals[0].summary)

    def test_summaries_are_redacted(self):
        secret = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        proposals = self.build(
            signal(detect.FAILURE, secret, tool="bash", tracked=True, session="s1"),
        )

        self.assertNotIn(secret, proposals[0].summary)

    def test_the_target_itself_is_redacted(self):
        """The target is written to disk as the proposal key, so masking the
        summary alone is not enough."""
        secret = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        proposals = self.build(
            signal(detect.FAILURE, secret, tool="bash", tracked=True, session="s1"),
        )

        self.assertNotIn(secret, proposals[0].target)
        self.assertNotIn(secret, proposals[0].key)

    def test_target_spelling_does_not_depend_on_the_run_redaction_mode(self):
        """`--full` must not change identity: recurrence and resolutions key on it."""
        args = signal(detect.FAILURE, "demo-cli", tool="bash", tracked=True, session="s1")

        default = route.build_proposals([args], config=self.config, redactor=Redactor())
        full = route.build_proposals([args], config=self.config, redactor=Redactor(full=True))

        self.assertEqual(default[0].key, full[0].key)


if __name__ == "__main__":
    unittest.main()

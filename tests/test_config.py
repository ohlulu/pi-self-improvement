import json
import tempfile
import unittest
from pathlib import Path

from pi_self_improvement import cues, detect
from pi_self_improvement.config import CONFIG_FILE, Config, ConfigError


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, payload, name=CONFIG_FILE):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path


class TestOverridesAndDefaults(ConfigTestCase):
    """AC-034: the override takes effect, everything else keeps its default."""

    def test_an_override_takes_effect(self):
        config = Config.from_dict({"tracked_clis": ["demo-cli", "other-cli"]})

        self.assertEqual(config.detect.tracked_clis, ("demo-cli", "other-cli"))

    def test_unset_keys_keep_their_defaults(self):
        defaults = detect.DetectConfig()

        config = Config.from_dict({"tracked_clis": ["demo-cli"]})

        self.assertEqual(config.detect.tracked_cli_suffix, defaults.tracked_cli_suffix)
        self.assertEqual(config.detect.detect_silent_empty, defaults.detect_silent_empty)
        self.assertEqual(config.detect.silent_empty_ignore, defaults.silent_empty_ignore)
        self.assertEqual(config.detect.include_subagent_failures, False)

    def test_an_empty_config_equals_the_defaults(self):
        config = Config.from_dict({})
        defaults = detect.DetectConfig()

        self.assertEqual(config.detect.tracked_clis, defaults.tracked_clis)
        self.assertEqual(config.detect.cue_packs, defaults.cue_packs)

    def test_a_boolean_override_takes_effect(self):
        config = Config.from_dict({"include_subagent_failures": True})

        self.assertTrue(config.detect.include_subagent_failures)

    def test_disabling_silent_empty_takes_effect(self):
        config = Config.from_dict({"detect_silent_empty": False})

        self.assertFalse(config.detect.detect_silent_empty)

    def test_an_override_reaches_the_detector(self):
        """The knob has to change behaviour, not just the dataclass field.

        Deliberately not a `-cli` name: the default `tracked_cli_suffix` already
        tracks those, so `demo-cli` would pass here with or without the
        override and prove nothing about the explicit list.
        """
        config = Config.from_dict({"tracked_clis": ["demotool"]})

        self.assertTrue(detect.is_tracked("demotool", config.detect))
        self.assertFalse(detect.is_tracked("demotool", detect.DetectConfig()))

    def test_the_default_suffix_rule_still_applies_alongside_an_override(self):
        config = Config.from_dict({"tracked_clis": ["demotool"]})

        self.assertTrue(detect.is_tracked("anything-cli", config.detect))


class TestRouteAndRedactionOverrides(ConfigTestCase):
    def test_ext_family_map_reaches_route_config(self):
        config = Config.from_dict({"ext_family_map": {"weird_tool": "jira"}})

        self.assertEqual(config.route.ext_family_map, {"weird_tool": "jira"})

    def test_extra_backlog_ignore_reaches_route_config(self):
        config = Config.from_dict({"extra_backlog_ignore": ["someprog"]})

        self.assertEqual(config.route.extra_backlog_ignore, ("someprog",))

    def test_extra_redaction_patterns_reach_the_redactor(self):
        config = Config.from_dict({"extra_redaction_patterns": [r"CUSTOMSECRET-[0-9]+"]})

        masked = config.redactor().text("token CUSTOMSECRET-99123 here")

        self.assertNotIn("CUSTOMSECRET-99123", masked)

    def test_the_redactor_still_masks_the_builtin_shapes(self):
        config = Config.from_dict({"extra_redaction_patterns": [r"CUSTOMSECRET-[0-9]+"]})

        masked = config.redactor().text("ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")

        self.assertNotIn("ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", masked)

    def test_the_full_flag_reaches_the_redactor(self):
        config = Config.from_dict({})

        self.assertTrue(config.redactor(full=True).local_only)
        self.assertFalse(config.redactor().local_only)


class TestCuePackOverrides(ConfigTestCase):
    def test_a_pack_can_be_disabled(self):
        config = Config.from_dict({"cue_packs": {"zh-Hant": {"enabled": False}}})

        self.assertNotIn("zh-Hant", [pack.name for pack in config.detect.cue_packs])

    def test_a_pack_can_be_extended(self):
        config = Config.from_dict({"cue_packs": {"en": {"strong": ["that is backwards"]}}})
        pack = next(pack for pack in config.detect.cue_packs if pack.name == "en")

        self.assertIn("that is backwards", pack.strong)

    def test_extending_keeps_the_builtin_cues(self):
        builtin = next(pack for pack in cues.BUILTIN_PACKS if pack.name == "en")

        config = Config.from_dict({"cue_packs": {"en": {"strong": ["that is backwards"]}}})
        pack = next(pack for pack in config.detect.cue_packs if pack.name == "en")

        for cue in builtin.strong:
            self.assertIn(cue, pack.strong)

    def test_an_unknown_pack_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            Config.from_dict({"cue_packs": {"klingon": {"strong": ["nope"]}}})

        self.assertIn("klingon", str(caught.exception))


class TestValidation(ConfigTestCase):
    def test_an_unknown_key_is_refused(self):
        with self.assertRaises(ConfigError) as caught:
            Config.from_dict({"tracked_cli": ["demo-cli"]})

        self.assertIn("tracked_cli", str(caught.exception))

    def test_a_near_miss_key_gets_a_suggestion(self):
        """A silently ignored typo looks identical to a setting that had no
        effect, and the user cannot tell which."""
        with self.assertRaises(ConfigError) as caught:
            Config.from_dict({"tracked_clis_": []})

        self.assertIn("did you mean", str(caught.exception))

    def test_a_bare_string_where_a_list_belongs_is_refused(self):
        """`tuple("demo-cli")` is ('d','e','m','o',…) and matches nothing."""
        with self.assertRaises(ConfigError) as caught:
            Config.from_dict({"tracked_clis": "demo-cli"})

        self.assertIn("list of strings", str(caught.exception))

    def test_a_non_string_list_item_is_refused(self):
        with self.assertRaises(ConfigError):
            Config.from_dict({"tracked_clis": ["demo-cli", 7]})

    def test_a_non_boolean_flag_is_refused(self):
        with self.assertRaises(ConfigError):
            Config.from_dict({"include_subagent_failures": "yes"})

    def test_a_broken_regex_is_refused_with_the_pattern_named(self):
        with self.assertRaises(ConfigError) as caught:
            Config.from_dict({"extra_redaction_patterns": ["[unclosed"]})

        self.assertIn("[unclosed", str(caught.exception))

    def test_a_non_object_ext_family_map_is_refused(self):
        with self.assertRaises(ConfigError):
            Config.from_dict({"ext_family_map": ["jira"]})

    def test_a_non_object_config_is_refused(self):
        with self.assertRaises(ConfigError):
            Config.from_dict(["tracked_clis"])


class TestLoading(ConfigTestCase):
    def test_a_config_file_is_read_from_the_output_root(self):
        self.write({"tracked_clis": ["demo-cli"]})

        config = Config.load(output_root=self.root)

        self.assertEqual(config.detect.tracked_clis, ("demo-cli",))

    def test_a_missing_default_config_is_not_an_error(self):
        config = Config.load(output_root=self.root / "nothing-here")

        self.assertEqual(config.detect.tracked_clis, ())

    def test_an_explicit_path_that_is_missing_is_an_error(self):
        """Passing --config and getting silent defaults would hide a typo."""
        with self.assertRaises(ConfigError):
            Config.load(self.root / "nope.json")

    def test_an_explicit_path_is_read(self):
        path = self.write({"tracked_clis": ["demo-cli"]}, name="custom.json")

        self.assertEqual(Config.load(path).detect.tracked_clis, ("demo-cli",))

    def test_malformed_json_names_the_file(self):
        path = self.root / CONFIG_FILE
        path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(ConfigError) as caught:
            Config.load(output_root=self.root)

        self.assertIn(CONFIG_FILE, str(caught.exception))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("keyboard_wizard", ROOT / "src/backend.py")
assert SPEC and SPEC.loader
wizard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wizard
SPEC.loader.exec_module(wizard)


class BackendTests(unittest.TestCase):
    def normal_modifier_captures(self):
        return {
            step.key: {"keycode": step.evdev_code + 8, "key": 0}
            for step in wizard.KEY_STEPS
        }

    def normal_character_captures(self):
        return {
            step.key: {
                "actual": step.character,
                "keycode": 0,
                "key": 0,
                "modifiers": 0,
            }
            for step in wizard.CHARACTER_STEPS
        }

    def normal_payload(self):
        return {
            "device_name": "example-keyboard",
            "primary_layout": "de",
            "primary_variant": "nodeadkeys",
            "secondary_layout": "us",
            "switch_option": "grp:win_space_toggle",
            "captures": {
                "modifiers": self.normal_modifier_captures(),
                "characters": self.normal_character_captures(),
            },
        }

    def add_override(
        self,
        payload,
        key="at",
        actual="€",
        keycode=26,
        modifiers=wizard.QT_GROUP_SWITCH_MODIFIER,
        held_keycodes=None,
    ):
        payload["captures"]["characters"][key] = {
            "actual": actual,
            "override": True,
            "keycode": keycode,
            "key": 0,
            "modifiers": modifiers,
            "held_keycodes": held_keycodes or [108, keycode],
        }

    def test_parses_layouts_and_variants(self):
        fixture = """\
! layout
  us              English (US)
  de              German
! variant
  intl            us: English (US, intl.)
  nodeadkeys      de: German (no dead keys)
! option
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evdev.lst"
            path.write_text(fixture, encoding="utf-8")
            layouts, variants = wizard.parse_xkb_rules(path)

        self.assertEqual(layouts, [("us", "English (US)"), ("de", "German")])
        self.assertEqual(variants["de"], [("nodeadkeys", "German (no dead keys)")])

    def test_normal_mapping_needs_no_correction(self):
        options, warnings, repaired = wizard.analyze_captures(
            self.normal_modifier_captures()
        )
        self.assertEqual(options, [])
        self.assertEqual(warnings, [])
        self.assertEqual(repaired, set())

    def test_detects_left_alt_super_swap(self):
        captures = self.normal_modifier_captures()
        captures["left_alt"] = {
            "keycode": wizard.EXPECTED_CODES["left_super"] + 8,
            "keyval": "Super_L",
        }
        captures["left_super"] = {
            "keycode": wizard.EXPECTED_CODES["left_alt"] + 8,
            "keyval": "Alt_L",
        }

        options, warnings, repaired = wizard.analyze_captures(captures)
        self.assertEqual(options, ["altwin:swap_lalt_lwin"])
        self.assertEqual(warnings, [])
        self.assertEqual(repaired, {"left_alt", "left_super"})

    def test_requested_characters_are_all_present_in_order(self):
        self.assertEqual(
            [step.character for step in wizard.CHARACTER_STEPS],
            ["@", "{", "}", "[", "]", ">", "<", "|", "~", "\\"],
        )

    def test_requested_characters_are_recognized(self):
        results, warnings = wizard.analyze_character_captures(
            self.normal_character_captures()
        )
        self.assertEqual(warnings, [])
        self.assertTrue(all(result["status"] == "correct" for result in results))

    def test_character_mismatch_and_skip_are_reported(self):
        captures = self.normal_character_captures()
        captures["pipe"] = {"actual": "¦"}
        captures["backslash"] = {"skipped": True, "actual": ""}

        results, warnings = wizard.analyze_character_captures(captures)

        by_key = {result["key"]: result for result in results}
        self.assertEqual(by_key["pipe"]["status"], "needs attention")
        self.assertEqual(by_key["backslash"]["status"], "skipped")
        self.assertEqual(len(warnings), 2)

    def test_review_counts_all_requested_characters(self):
        review = wizard.build_review({
            "captures": {
                "modifiers": self.normal_modifier_captures(),
                "characters": self.normal_character_captures(),
            }
        })
        self.assertEqual(review["character_pass_count"], 10)
        self.assertEqual(review["character_override_count"], 0)
        self.assertEqual(review["character_total"], 10)
        self.assertEqual(review["warnings"], [])

    def test_character_conflict_can_be_accepted_as_override(self):
        payload = self.normal_payload()
        self.add_override(payload)

        review = wizard.build_review(payload)

        by_key = {result["key"]: result for result in review["character_results"]}
        self.assertTrue(review["ok"])
        self.assertEqual(review["warnings"], [])
        self.assertEqual(by_key["at"]["status"], "will be overridden")
        self.assertEqual(review["character_pass_count"], 9)
        self.assertEqual(review["character_override_count"], 1)
        self.assertEqual(review["character_overrides"][0]["level"], 3)
        self.assertEqual(review["character_overrides"][0]["keysym"], "at")

    def test_no_text_conflict_can_be_accepted_as_override(self):
        payload = self.normal_payload()
        self.add_override(payload, actual="")

        review = wizard.build_review(payload)

        self.assertTrue(review["ok"])
        self.assertEqual(review["character_override_count"], 1)
        self.assertEqual(review["character_overrides"][0]["actual"], "")

    def test_override_level_uses_captured_shift_and_altgr_keys(self):
        modifiers = self.normal_modifier_captures()
        capture = {
            "modifiers": 0,
            "held_keycodes": [50, 108, 26],
        }
        self.assertEqual(wizard.override_level(capture, modifiers), 4)

    def test_physical_left_ctrl_and_alt_are_not_treated_as_altgr(self):
        modifiers = self.normal_modifier_captures()
        capture = {
            "modifiers": wizard.QT_CONTROL_MODIFIER | wizard.QT_ALT_MODIFIER,
            "held_keycodes": [37, 64, 26],
        }

        with self.assertRaisesRegex(ValueError, "Ctrl"):
            wizard.override_level(capture, modifiers)

    def test_duplicate_override_chord_is_rejected(self):
        payload = self.normal_payload()
        self.add_override(payload)
        self.add_override(
            payload,
            key="left_brace",
            actual="€",
            keycode=26,
        )

        review = wizard.build_review(payload)

        self.assertFalse(review["ok"])
        self.assertTrue(any("same key chord" in warning for warning in review["warnings"]))

    def test_symbol_override_patches_primary_group_only(self):
        fixture = """\
xkb_keymap {
  xkb_keycodes "test" {
    <AD03> = 26;
  };
  xkb_symbols "test" {
    key <AD03> {
      symbols[1]= [ e, E, EuroSign, EuroSign ],
      symbols[2]= [ e, E ]
    };
  };
};
"""
        updated = wizard.apply_symbol_overrides(fixture, [{
            "keycode": 26,
            "level": 3,
            "keysym": "at",
        }])
        self.assertIn("symbols[1]= [ e, E, at, EuroSign ]", updated)
        self.assertIn("symbols[2]= [ e, E ]", updated)

    def test_custom_keymap_block_uses_kb_file(self):
        _begin, _end, block = wizard.render_device_block(
            "example-keyboard",
            "de",
            "nodeadkeys",
            "us",
            "grp:win_space_toggle",
            [],
            Path("/tmp/example-keyboard.xkb"),
        )
        self.assertIn('kb_file = "/tmp/example-keyboard.xkb"', block)
        self.assertNotIn("kb_layout", block)
        self.assertNotIn("kb_options", block)

    def test_failed_hyprland_validation_rolls_back_keymap_and_config(self):
        payload = self.normal_payload()
        self.add_override(payload)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "input.lua"
            keymap = Path(directory) / "keyboard.xkb"
            config.write_text("-- original\n", encoding="utf-8")
            with (
                mock.patch.object(
                    wizard, "generate_custom_keymap", return_value="xkb_keymap {};\n"
                ),
                mock.patch.object(
                    wizard, "custom_keymap_path", return_value=keymap
                ),
                mock.patch.object(
                    wizard,
                    "validate_hyprland",
                    side_effect=[("errors", "bad config"), ("ok", "restored")],
                ),
            ):
                result = wizard.apply_payload(payload, config)

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "rolled-back")
            self.assertEqual(config.read_text(encoding="utf-8"), "-- original\n")
            self.assertFalse(keymap.exists())

    def test_generated_block_is_replaced_not_duplicated(self):
        begin, end, block = wizard.render_device_block(
            "example-keyboard",
            "de",
            "nodeadkeys",
            "us",
            "grp:win_space_toggle",
            ["altwin:swap_lalt_lwin"],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.lua"
            path.write_text("-- existing setting\n", encoding="utf-8")
            wizard.update_generated_block(path, begin, end, block)
            wizard.update_generated_block(path, begin, end, block.replace("de,us", "us,de"))
            result = path.read_text(encoding="utf-8")

        self.assertEqual(result.count(begin), 1)
        self.assertIn("-- existing setting", result)
        self.assertIn('kb_layout = "us,de"', result)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


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
        self.assertEqual(review["character_total"], 10)
        self.assertEqual(review["warnings"], [])

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

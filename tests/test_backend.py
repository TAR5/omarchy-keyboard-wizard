#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("keyboard_wizard", ROOT / "src/wizard.py")
assert SPEC and SPEC.loader
wizard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wizard
SPEC.loader.exec_module(wizard)


class BackendTests(unittest.TestCase):
    def normal_captures(self):
        return {
            step.key: {"keycode": step.evdev_code + 8, "keyval": "test"}
            for step in wizard.KEY_STEPS
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
        options, warnings, repaired = wizard.analyze_captures(self.normal_captures())
        self.assertEqual(options, [])
        self.assertEqual(warnings, [])
        self.assertEqual(repaired, set())

    def test_detects_left_alt_super_swap(self):
        captures = self.normal_captures()
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

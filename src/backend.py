#!/usr/bin/env python3
"""JSON backend for the Omarchy Keyboard Wizard Quickshell panel."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


INPUT_CONFIG = Path.home() / ".config/hypr/input.lua"
XKB_RULES = Path("/usr/share/X11/xkb/rules/evdev.lst")
DEFAULT_OPTIONS = ["compose:caps", "shift:both_capslock_cancel"]

SWITCH_OPTIONS = [
    {"label": "Alt + Shift", "value": "grp:alt_shift_toggle"},
    {"label": "Super + Space", "value": "grp:win_space_toggle"},
    {"label": "Both Alt keys", "value": "grp:alts_toggle"},
    {"label": "Ctrl + Space", "value": "grp:ctrl_space_toggle"},
]


@dataclass(frozen=True)
class KeyStep:
    key: str
    label: str
    hint: str
    evdev_code: int


@dataclass(frozen=True)
class CharacterStep:
    key: str
    character: str
    label: str
    hint: str


# Qt's nativeScanCode on Wayland normally uses XKB keycodes (evdev + 8).
# normalize_keycode accepts either form.
KEY_STEPS = [
    KeyStep("escape", "Escape", "Press the key labeled Esc", 1),
    KeyStep("caps", "Caps Lock", "Press the key labeled Caps Lock", 58),
    KeyStep("left_shift", "Left Shift", "Press the Shift key on the left", 42),
    KeyStep("left_ctrl", "Left Control", "Press the Ctrl key on the left", 29),
    KeyStep("left_super", "Left Super / Command", "Press the left Windows, Super, or Command key", 125),
    KeyStep("left_alt", "Left Alt / Option", "Press the left Alt or Option key", 56),
    KeyStep("space", "Space", "Press the space bar", 57),
    KeyStep("right_alt", "Right Alt / AltGr", "Press the right Alt, AltGr, or Option key", 100),
    KeyStep("right_super", "Right Super / Command", "Press the right Windows, Super, or Command key", 126),
    KeyStep("right_ctrl", "Right Control", "Press the Ctrl key on the right", 97),
    KeyStep("right_shift", "Right Shift", "Press the Shift key on the right", 54),
]

CHARACTER_STEPS = [
    CharacterStep("at", "@", "@", "Type the at sign"),
    CharacterStep("left_brace", "{", "{", "Type a left curly brace"),
    CharacterStep("right_brace", "}", "}", "Type a right curly brace"),
    CharacterStep("left_bracket", "[", "[", "Type a left square bracket"),
    CharacterStep("right_bracket", "]", "]", "Type a right square bracket"),
    CharacterStep("greater_than", ">", ">", "Type the greater-than sign"),
    CharacterStep("less_than", "<", "<", "Type the less-than sign"),
    CharacterStep("pipe", "|", "|", "Type the vertical bar"),
    CharacterStep("tilde", "~", "~", "Type a tilde; dead-key layouts may require Space afterward"),
    CharacterStep("backslash", "\\", "Backslash (\\)", "Type a backslash"),
]

EXPECTED_CODES = {step.key: step.evdev_code for step in KEY_STEPS}
EXPECTED_LABELS = {step.key: step.label for step in KEY_STEPS}

SWAP_OPTIONS = [
    ("caps", "escape", "caps:swapescape"),
    ("caps", "left_ctrl", "ctrl:swapcaps"),
    ("left_alt", "left_super", "altwin:swap_lalt_lwin"),
    ("right_alt", "right_super", "altwin:swap_ralt_rwin"),
    ("left_alt", "left_ctrl", "ctrl:swap_lalt_lctl"),
    ("right_alt", "right_ctrl", "ctrl:swap_ralt_rctl"),
    ("left_super", "left_ctrl", "ctrl:swap_lwin_lctl"),
    ("right_super", "right_ctrl", "ctrl:swap_rwin_rctl"),
]


def run(command: list[str], timeout: float = 4) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_xkb_rules(
    path: Path = XKB_RULES,
) -> tuple[list[tuple[str, str]], dict[str, list[tuple[str, str]]]]:
    layouts: list[tuple[str, str]] = []
    variants: dict[str, list[tuple[str, str]]] = {}
    section = ""

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [("us", "English (US)"), ("de", "German")], {}

    for line in lines:
        if line.startswith("!"):
            fields = line[1:].strip().split()
            section = fields[0] if fields else ""
            continue
        if not line.strip():
            continue

        if section == "layout":
            match = re.match(r"^\s*(\S+)\s+(.+?)\s*$", line)
            if match:
                layouts.append((match.group(1), match.group(2)))
        elif section == "variant":
            match = re.match(r"^\s*(\S+)\s+(\S+):\s+(.+?)\s*$", line)
            if match:
                variant, layout, label = match.groups()
                variants.setdefault(layout, []).append((variant, label))

    return layouts or [("us", "English (US)"), ("de", "German")], variants


def current_layout() -> str:
    try:
        text = Path("/etc/vconsole.conf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "us"
    match = re.search(r'^\s*XKBLAYOUT\s*=\s*["\']?([^"\'\s#]+)', text, re.MULTILINE)
    return match.group(1).split(",", 1)[0] if match else "us"


def kernel_keyboard_names() -> set[str]:
    """Return Hyprland-style names for devices with normal typing keys."""
    try:
        text = Path("/proc/bus/input/devices").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()

    names: set[str] = set()
    for block in text.split("\n\n"):
        name_match = re.search(r'^N: Name="(.+)"$', block, re.MULTILINE)
        key_match = re.search(r"^B: KEY=(.+)$", block, re.MULTILINE)
        if not name_match or not key_match:
            continue
        try:
            words = key_match.group(1).split()
            key_bitmap = int("".join(word.zfill(16) for word in words), 16)
        except ValueError:
            continue
        required_codes = (28, 30, 44, 57)  # Enter, A, Z, Space
        if not all(key_bitmap & (1 << code) for code in required_codes):
            continue
        names.add(name_match.group(1).lower().replace(" ", "-"))
    return names


def keyboard_devices() -> list[dict[str, Any]]:
    try:
        result = run(["hyprctl", "-j", "devices"])
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        payload = {}

    kernel_names = kernel_keyboard_names()
    ignored = (
        "power-button",
        "sleep-button",
        "video-bus",
        "consumer-control",
        "system-control",
        "touch-bar",
        "headset",
        "surround-sound",
        "wtype",
        "ydotool",
        "virtual",
    )
    devices: list[dict[str, Any]] = []
    for item in payload.get("keyboards", []):
        name = str(item.get("name", "")).strip()
        if not name or any(fragment in name.lower() for fragment in ignored):
            continue
        if kernel_names and name not in kernel_names:
            continue
        devices.append({
            "name": name,
            "description": str(item.get("description", "")).strip() or name,
            "main": bool(item.get("main", False)),
        })

    names = {device["name"] for device in devices}
    devices = [
        device
        for device in devices
        if not (
            device["name"].endswith("-keyboard-keyboard")
            and device["name"][:-9] in names
        )
    ]
    devices.sort(key=lambda device: (not device["main"], device["description"].lower()))
    return devices


def normalize_keycode(keycode: int) -> int:
    evdev_codes = set(EXPECTED_CODES.values())
    if keycode in evdev_codes:
        return keycode
    if keycode - 8 in evdev_codes:
        return keycode - 8
    return keycode


def analyze_modifier_captures(
    captures: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str], set[str]]:
    normalized: dict[str, int] = {}
    for key, value in captures.items():
        if not isinstance(value, dict) or value.get("skipped"):
            continue
        try:
            normalized[key] = normalize_keycode(int(value["keycode"]))
        except (KeyError, TypeError, ValueError):
            continue

    options: list[str] = []
    warnings: list[str] = []
    repaired: set[str] = set()

    for first, second, option in SWAP_OPTIONS:
        if first in repaired or second in repaired:
            continue
        if (
            normalized.get(first) == EXPECTED_CODES[second]
            and normalized.get(second) == EXPECTED_CODES[first]
        ):
            options.append(option)
            repaired.update((first, second))

    for key, expected in EXPECTED_CODES.items():
        actual = normalized.get(key)
        if actual is None:
            warnings.append(f"{EXPECTED_LABELS[key]} was not verified.")
        elif actual != expected and key not in repaired:
            warnings.append(
                f"{EXPECTED_LABELS[key]} reports unsupported code {actual}; "
                "the layout can still be applied, but this key needs a custom remap."
            )

    if "altwin:swap_lalt_lwin" in options and "altwin:swap_ralt_rwin" in options:
        options = [
            option
            for option in options
            if option not in {"altwin:swap_lalt_lwin", "altwin:swap_ralt_rwin"}
        ]
        options.append("altwin:swap_alt_win")

    return options, warnings, repaired


analyze_captures = analyze_modifier_captures


def analyze_character_captures(
    captures: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
    results: list[dict[str, str]] = []
    warnings: list[str] = []
    for step in CHARACTER_STEPS:
        capture = captures.get(step.key, {})
        actual = str(capture.get("actual", "")) if isinstance(capture, dict) else ""
        skipped = not isinstance(capture, dict) or bool(capture.get("skipped"))
        if skipped:
            status = "skipped"
            warnings.append(f"Character {step.label} was not verified.")
        elif step.character in actual:
            status = "correct"
        else:
            status = "needs attention"
            shown = actual if actual else "no text"
            warnings.append(f"Expected {step.label}, but the layout produced {shown!r}.")
        results.append({
            "key": step.key,
            "label": step.label,
            "expected": step.character,
            "actual": actual,
            "status": status,
        })
    return results, warnings


def build_review(payload: dict[str, Any]) -> dict[str, Any]:
    all_captures = payload.get("captures", {})
    if not isinstance(all_captures, dict):
        all_captures = {}
    modifiers = all_captures.get("modifiers", {})
    characters = all_captures.get("characters", {})
    if not isinstance(modifiers, dict):
        modifiers = {}
    if not isinstance(characters, dict):
        characters = {}

    options, modifier_warnings, repaired = analyze_modifier_captures(modifiers)
    character_results, character_warnings = analyze_character_captures(characters)

    modifier_results: list[dict[str, Any]] = []
    for step in KEY_STEPS:
        capture = modifiers.get(step.key, {})
        actual: int | None = None
        if isinstance(capture, dict) and not capture.get("skipped"):
            try:
                actual = normalize_keycode(int(capture["keycode"]))
            except (KeyError, TypeError, ValueError):
                actual = None
        if actual is None:
            status = "skipped"
        elif actual == step.evdev_code:
            status = "correct"
        elif step.key in repaired:
            status = "will be corrected"
        else:
            status = "needs attention"
        modifier_results.append({
            "key": step.key,
            "label": step.label,
            "code": actual,
            "status": status,
        })

    return {
        "ok": True,
        "correction_options": options,
        "warnings": modifier_warnings + character_warnings,
        "modifier_results": modifier_results,
        "character_results": character_results,
        "character_pass_count": sum(result["status"] == "correct" for result in character_results),
        "character_total": len(CHARACTER_STEPS),
    }


def state_payload() -> dict[str, Any]:
    layouts, variants = parse_xkb_rules()
    return {
        "ok": True,
        "current_layout": current_layout(),
        "devices": keyboard_devices(),
        "layouts": [
            {"value": code, "label": f"{label} — {code}"}
            for code, label in layouts
        ],
        "variants": {
            layout: [
                {"value": code, "label": f"{label} — {code}"}
                for code, label in choices
            ]
            for layout, choices in variants.items()
        },
        "switch_options": SWITCH_OPTIONS,
        "modifier_steps": [asdict(step) for step in KEY_STEPS],
        "character_steps": [asdict(step) for step in CHARACTER_STEPS],
        "input_config": str(INPUT_CONFIG),
    }


def lua_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def render_device_block(
    device_name: str,
    primary_layout: str,
    primary_variant: str,
    secondary_layout: str,
    switch_option: str,
    correction_options: list[str],
) -> tuple[str, str, str]:
    digest = hashlib.sha256(device_name.encode("utf-8")).hexdigest()[:12]
    begin = f"-- BEGIN OMARCHY KEYBOARD WIZARD {digest}"
    end = f"-- END OMARCHY KEYBOARD WIZARD {digest}"
    layouts = primary_layout
    variants = primary_variant
    options = list(DEFAULT_OPTIONS) + correction_options
    if secondary_layout:
        layouts += f",{secondary_layout}"
        variants += ","
        options.append(switch_option)
    options = list(dict.fromkeys(option for option in options if option))
    block = "\n".join([
        begin,
        "-- Generated by Keyboard Setup. Re-run the wizard to update this block.",
        "hl.device({",
        f"  name = {lua_quote(device_name)},",
        f"  kb_layout = {lua_quote(layouts)},",
        f"  kb_variant = {lua_quote(variants)},",
        f"  kb_options = {lua_quote(','.join(options))},",
        "})",
        end,
    ])
    return begin, end, block


def update_generated_block(path: Path, begin: str, end: str, block: str) -> tuple[Path, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.keyboard-wizard.bak.{timestamp}")
    if path.exists():
        shutil.copy2(path, backup)
    else:
        backup.write_text("", encoding="utf-8")

    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    if pattern.search(original):
        updated = pattern.sub(block, original, count=1)
    else:
        separator = "" if not original else ("\n" if original.endswith("\n") else "\n\n")
        updated = original + separator + block + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(updated)
        temporary = Path(handle.name)
    temporary.chmod(path.stat().st_mode if path.exists() else 0o644)
    os.replace(temporary, path)
    return backup, original


def restore_config(path: Path, original: str) -> None:
    path.write_text(original, encoding="utf-8")


def validate_hyprland() -> tuple[str, str]:
    try:
        reload_result = run(["hyprctl", "reload"])
        if reload_result.returncode != 0:
            detail = (reload_result.stderr or reload_result.stdout).strip()
            return "unavailable", detail or "Hyprland did not accept the reload request."
        errors_result = run(["hyprctl", "configerrors"])
        if errors_result.returncode != 0:
            detail = (errors_result.stderr or errors_result.stdout).strip()
            return "unavailable", detail or "Could not query Hyprland configuration errors."
        errors = errors_result.stdout.strip()
        if errors.lower() in {"", "no errors", "no errors found"}:
            return "ok", "Hyprland reloaded with no configuration errors."
        return "errors", errors
    except (OSError, subprocess.TimeoutExpired) as error:
        return "unavailable", str(error)


def validate_settings(payload: dict[str, Any]) -> tuple[bool, str]:
    device_name = str(payload.get("device_name", "")).strip()
    primary = str(payload.get("primary_layout", "")).strip()
    variant = str(payload.get("primary_variant", "")).strip()
    secondary = str(payload.get("secondary_layout", "")).strip()
    switch = str(payload.get("switch_option", "")).strip()
    layouts, variants = parse_xkb_rules()
    layout_codes = {code for code, _label in layouts}
    if not device_name:
        return False, "A keyboard device is required."
    if primary not in layout_codes:
        return False, f"Unknown primary layout: {primary!r}."
    if secondary and secondary not in layout_codes:
        return False, f"Unknown secondary layout: {secondary!r}."
    if variant and variant not in {code for code, _label in variants.get(primary, [])}:
        return False, f"Unknown variant {variant!r} for layout {primary!r}."
    allowed_switches = {item["value"] for item in SWITCH_OPTIONS}
    if secondary and switch not in allowed_switches:
        return False, f"Unknown layout switching option: {switch!r}."
    return True, ""


def apply_payload(payload: dict[str, Any], path: Path = INPUT_CONFIG) -> dict[str, Any]:
    valid, error = validate_settings(payload)
    if not valid:
        return {"ok": False, "status": "invalid", "title": "Invalid configuration", "message": error}

    review = build_review(payload)
    begin, end, block = render_device_block(
        str(payload["device_name"]),
        str(payload["primary_layout"]),
        str(payload.get("primary_variant", "")),
        str(payload.get("secondary_layout", "")),
        str(payload.get("switch_option", "")),
        review["correction_options"],
    )
    try:
        backup, original = update_generated_block(path, begin, end, block)
    except OSError as exc:
        return {
            "ok": False,
            "status": "write-error",
            "title": "Could not save the configuration",
            "message": str(exc),
        }

    status, detail = validate_hyprland()
    if status == "errors":
        try:
            restore_config(path, original)
            validate_hyprland()
            message = f"Hyprland reported errors, so the previous file was restored.\n\n{detail}"
        except OSError as exc:
            message = f"Hyprland reported errors and restoring the previous file failed.\n\n{detail}\n\n{exc}"
        return {
            "ok": False,
            "status": "rolled-back",
            "title": "Configuration was not applied",
            "message": message,
            "backup": str(backup),
            "config": str(path),
        }
    if status == "unavailable":
        return {
            "ok": True,
            "status": "saved-unvalidated",
            "warning": True,
            "title": "Configuration saved",
            "message": (
                "The file and backup were written, but Hyprland could not be reached for validation. "
                "Run `hyprctl reload` and `hyprctl configerrors` from the desktop.\n\n" + detail
            ),
            "backup": str(backup),
            "config": str(path),
        }
    return {
        "ok": True,
        "status": "applied",
        "warning": False,
        "title": "Keyboard configured",
        "message": "Hyprland reloaded successfully with no configuration errors.",
        "backup": str(backup),
        "config": str(path),
    }


def parse_payload(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("payload must be a JSON object")
    return parsed


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def self_test() -> None:
    layouts, variants = parse_xkb_rules()
    assert any(code == "us" for code, _label in layouts)
    assert isinstance(variants, dict)
    assert normalize_keycode(133) == 125
    normal = {
        step.key: {"keycode": step.evdev_code + 8, "key": 0}
        for step in KEY_STEPS
    }
    options, warnings, repaired = analyze_modifier_captures(normal)
    assert options == [] and warnings == [] and repaired == set()
    characters = {
        step.key: {"actual": step.character, "keycode": 0, "modifiers": 0}
        for step in CHARACTER_STEPS
    }
    character_results, character_warnings = analyze_character_captures(characters)
    assert not character_warnings
    assert all(item["status"] == "correct" for item in character_results)
    begin, end, block = render_device_block(
        "test-keyboard", "de", "nodeadkeys", "us", "grp:win_space_toggle", options
    )
    assert 'kb_layout = "de,us"' in block
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.lua"
        path.write_text("-- existing\n", encoding="utf-8")
        update_generated_block(path, begin, end, block)
        update_generated_block(path, begin, end, block.replace("de,us", "us,de"))
        result = path.read_text(encoding="utf-8")
        assert result.count(begin) == 1 and "us,de" in result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        command = args[0] if args else "state"
        if command in {"self-test", "--self-test"}:
            self_test()
            emit({"ok": True, "message": "Keyboard Setup backend self-test passed"})
        elif command == "state":
            emit(state_payload())
        elif command == "review":
            if len(args) != 2:
                raise ValueError("review requires one JSON payload argument")
            emit(build_review(parse_payload(args[1])))
        elif command == "apply":
            if len(args) != 2:
                raise ValueError("apply requires one JSON payload argument")
            emit(apply_payload(parse_payload(args[1])))
        else:
            raise ValueError(f"unknown command: {command}")
        return 0
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        emit({"ok": False, "status": "error", "title": "Backend error", "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

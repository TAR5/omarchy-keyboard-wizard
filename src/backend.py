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
KEYMAP_DIR = Path.home() / ".config/hypr/keyboard-wizard"
XKB_RULES = Path("/usr/share/X11/xkb/rules/evdev.lst")
DEFAULT_OPTIONS = ["compose:caps", "shift:both_capslock_cancel"]

QT_SHIFT_MODIFIER = 0x02000000
QT_CONTROL_MODIFIER = 0x04000000
QT_ALT_MODIFIER = 0x08000000
QT_META_MODIFIER = 0x10000000
QT_GROUP_SWITCH_MODIFIER = 0x40000000

CHARACTER_KEYSYMS = {
    "@": "at",
    "{": "braceleft",
    "}": "braceright",
    "[": "bracketleft",
    "]": "bracketright",
    ">": "greater",
    "<": "less",
    "|": "bar",
    "~": "asciitilde",
    "\\": "backslash",
}

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
) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
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
        elif capture.get("override") and capture.get("keycode") is not None:
            status = "will be overridden"
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


def _captured_keycodes(
    captures: dict[str, dict[str, Any]], keys: tuple[str, ...]
) -> set[int]:
    codes: set[int] = set()
    for key in keys:
        capture = captures.get(key, {})
        if not isinstance(capture, dict) or capture.get("skipped"):
            continue
        try:
            codes.add(int(capture["keycode"]))
        except (KeyError, TypeError, ValueError):
            continue
    return codes


def override_level(
    capture: dict[str, Any], modifier_captures: dict[str, dict[str, Any]]
) -> int:
    """Return the XKB shift level (1-4) represented by a captured chord."""
    try:
        modifiers = int(capture.get("modifiers", 0))
    except (TypeError, ValueError):
        modifiers = 0

    held: set[int] = set()
    raw_held = capture.get("held_keycodes", [])
    if isinstance(raw_held, list):
        for value in raw_held:
            try:
                held.add(int(value))
            except (TypeError, ValueError):
                continue

    shift_codes = _captured_keycodes(
        modifier_captures, ("left_shift", "right_shift")
    )
    control_codes = _captured_keycodes(
        modifier_captures, ("left_ctrl", "right_ctrl")
    )
    left_alt_codes = _captured_keycodes(modifier_captures, ("left_alt",))
    level_three_codes = _captured_keycodes(modifier_captures, ("right_alt",))
    meta_codes = _captured_keycodes(
        modifier_captures, ("left_super", "right_super")
    )

    if held & meta_codes or modifiers & QT_META_MODIFIER:
        raise ValueError("Super/Meta chords cannot be used for character overrides.")
    if held & control_codes:
        raise ValueError("Ctrl chords cannot be used for character overrides.")
    if held & left_alt_codes:
        raise ValueError(
            "Left Alt chords cannot be used for character overrides; use Right Alt/AltGr."
        )

    shift = bool(held & shift_codes) or bool(modifiers & QT_SHIFT_MODIFIER)
    level_three = (
        bool(held & level_three_codes)
        or bool(modifiers & QT_GROUP_SWITCH_MODIFIER)
        or bool(
            modifiers & QT_CONTROL_MODIFIER
            and modifiers & QT_ALT_MODIFIER
        )
    )

    if modifiers & QT_CONTROL_MODIFIER and not level_three:
        raise ValueError("Ctrl chords cannot be used for character overrides.")
    if modifiers & QT_ALT_MODIFIER and not level_three:
        raise ValueError(
            "Left Alt chords cannot be used for character overrides; use Right Alt/AltGr."
        )

    if level_three:
        return 4 if shift else 3
    return 2 if shift else 1


def collect_character_overrides(
    character_captures: dict[str, dict[str, Any]],
    modifier_captures: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    overrides: list[dict[str, Any]] = []
    errors: list[str] = []
    occupied: dict[tuple[int, int], str] = {}

    for step in CHARACTER_STEPS:
        capture = character_captures.get(step.key, {})
        if not isinstance(capture, dict) or not capture.get("override"):
            continue
        try:
            keycode = int(capture["keycode"])
            if keycode <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            errors.append(f"Character {step.label} has no usable physical keycode.")
            continue
        try:
            level = override_level(capture, modifier_captures)
        except ValueError as exc:
            errors.append(f"Character {step.label}: {exc}")
            continue

        chord = (keycode, level)
        previous = occupied.get(chord)
        if previous and previous != step.character:
            errors.append(
                f"Characters {previous!r} and {step.character!r} were assigned to the same key chord."
            )
            continue
        occupied[chord] = step.character
        overrides.append({
            "key": step.key,
            "label": step.label,
            "character": step.character,
            "keysym": CHARACTER_KEYSYMS[step.character],
            "keycode": keycode,
            "level": level,
            "actual": str(capture.get("actual", "")),
        })

    return overrides, errors


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
    character_overrides, override_errors = collect_character_overrides(
        characters, modifiers
    )

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
        "ok": not override_errors,
        "correction_options": options,
        "override_errors": override_errors,
        "warnings": modifier_warnings + character_warnings + override_errors,
        "modifier_results": modifier_results,
        "character_results": character_results,
        "character_pass_count": sum(result["status"] == "correct" for result in character_results),
        "character_override_count": len(character_overrides),
        "character_total": len(CHARACTER_STEPS),
        "character_overrides": character_overrides,
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


def keyboard_values(
    primary_layout: str,
    primary_variant: str,
    secondary_layout: str,
    switch_option: str,
    correction_options: list[str],
) -> tuple[str, str, list[str]]:
    layouts = primary_layout
    variants = primary_variant
    options = list(DEFAULT_OPTIONS) + correction_options
    if secondary_layout:
        layouts += f",{secondary_layout}"
        variants += ","
        options.append(switch_option)
    return layouts, variants, list(dict.fromkeys(option for option in options if option))


def render_device_block(
    device_name: str,
    primary_layout: str,
    primary_variant: str,
    secondary_layout: str,
    switch_option: str,
    correction_options: list[str],
    keymap_path: Path | None = None,
) -> tuple[str, str, str]:
    digest = hashlib.sha256(device_name.encode("utf-8")).hexdigest()[:12]
    begin = f"-- BEGIN OMARCHY KEYBOARD WIZARD {digest}"
    end = f"-- END OMARCHY KEYBOARD WIZARD {digest}"
    layouts, variants, options = keyboard_values(
        primary_layout,
        primary_variant,
        secondary_layout,
        switch_option,
        correction_options,
    )
    lines = [
        begin,
        "-- Generated by Keyboard Setup. Re-run the wizard to update this block.",
        "hl.device({",
        f"  name = {lua_quote(device_name)},",
    ]
    if keymap_path is not None:
        lines.append(f"  kb_file = {lua_quote(str(keymap_path))},")
    else:
        lines.extend([
            f"  kb_layout = {lua_quote(layouts)},",
            f"  kb_variant = {lua_quote(variants)},",
            f"  kb_options = {lua_quote(','.join(options))},",
        ])
    lines.extend(["})", end])
    block = "\n".join(lines)
    return begin, end, block


def custom_keymap_path(device_name: str) -> Path:
    digest = hashlib.sha256(device_name.encode("utf-8")).hexdigest()[:12]
    return KEYMAP_DIR / f"{digest}.xkb"


def compile_base_keymap(
    primary_layout: str,
    primary_variant: str,
    secondary_layout: str,
    switch_option: str,
    correction_options: list[str],
) -> str:
    layouts, variants, options = keyboard_values(
        primary_layout,
        primary_variant,
        secondary_layout,
        switch_option,
        correction_options,
    )
    command = ["xkbcli", "compile-keymap", "--layout", layouts]
    if variants:
        command.extend(["--variant", variants])
    if options:
        command.extend(["--options", ",".join(options)])
    try:
        result = run(command, timeout=12)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Could not run xkbcli: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        detail = (result.stderr or result.stdout).strip()
        raise ValueError(detail or "xkbcli could not compile the selected layout.")
    return result.stdout


def _split_symbols(raw: str) -> list[str]:
    symbols: list[str] = []
    start = 0
    depth = 0
    quoted = False
    escaped = False
    for index, character in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quoted:
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif not quoted and character == "{":
            depth += 1
        elif not quoted and character == "}":
            depth = max(0, depth - 1)
        elif not quoted and character == "," and depth == 0:
            symbols.append(raw[start:index].strip())
            start = index + 1
    symbols.append(raw[start:].strip())
    return symbols


def apply_symbol_overrides(keymap: str, overrides: list[dict[str, Any]]) -> str:
    keycode_names = {
        int(value): name
        for name, value in re.findall(r"<([A-Za-z0-9_]+)>\s*=\s*(\d+)\s*;", keymap)
    }
    updated = keymap
    for override in overrides:
        keycode = int(override["keycode"])
        key_name = keycode_names.get(keycode)
        if not key_name:
            raise ValueError(f"XKB keycode {keycode} is not present in the compiled keymap.")

        key_pattern = re.compile(
            rf"(\bkey\s+<{re.escape(key_name)}>\s*\{{)(.*?)(\}}\s*;)",
            re.DOTALL,
        )
        key_match = key_pattern.search(updated)
        if not key_match:
            raise ValueError(f"Could not find the symbols for XKB key <{key_name}>.")
        body = key_match.group(2)
        group_pattern = re.compile(r"(symbols\s*\[\s*1\s*\]\s*=\s*\[)([^\]]*)(\])")
        symbols_match = group_pattern.search(body)
        if not symbols_match:
            group_pattern = re.compile(r"(\[)([^\]]*)(\])")
            symbols_match = group_pattern.search(body)
        if not symbols_match:
            raise ValueError(f"Could not read the symbols for XKB key <{key_name}>.")

        symbols = _split_symbols(symbols_match.group(2))
        level_index = int(override["level"]) - 1
        while len(symbols) <= level_index:
            symbols.append("NoSymbol")
        symbols[level_index] = str(override["keysym"])
        replacement = (
            symbols_match.group(1)
            + " "
            + ", ".join(symbols)
            + " "
            + symbols_match.group(3)
        )
        body = body[:symbols_match.start()] + replacement + body[symbols_match.end():]
        updated = updated[:key_match.start(2)] + body + updated[key_match.end(2):]
    return updated


def force_level_three_key(keymap: str, keycode: int) -> str:
    """Make one physical key an ISO Level3/AltGr selector in the compiled map."""
    keycode_names = {
        int(value): name
        for name, value in re.findall(r"<([A-Za-z0-9_]+)>\s*=\s*(\d+)\s*;", keymap)
    }
    key_name = keycode_names.get(int(keycode))
    if not key_name:
        raise ValueError(
            f"The captured Right Alt/AltGr keycode {keycode} is not present in the XKB keymap."
        )

    key_pattern = re.compile(
        rf"(?m)^(\s*)key\s+<{re.escape(key_name)}>\s*\{{.*?\}}\s*;",
        re.DOTALL,
    )
    key_match = key_pattern.search(keymap)
    if not key_match:
        raise ValueError(f"Could not configure XKB key <{key_name}> as AltGr.")
    indent = key_match.group(1)
    key_declaration = "\n".join([
        f"{indent}key <{key_name}>               {{",
        f'{indent}\ttype= "ONE_LEVEL",',
        f"{indent}\tsymbols[1]= [ ISO_Level3_Shift ]",
        f"{indent}}};",
    ])
    updated = (
        keymap[:key_match.start()]
        + key_declaration
        + keymap[key_match.end():]
    )

    modifier_pattern = re.compile(
        r"(?m)^(\s*)modifier_map\s+([A-Za-z0-9_]+)\s*\{([^}]*)\};"
    )
    found_mod5 = False
    token = f"<{key_name}>"

    def update_modifier(match: re.Match[str]) -> str:
        nonlocal found_mod5
        modifier = match.group(2)
        entries = re.findall(r"<[A-Za-z0-9_]+>", match.group(3))
        entries = [entry for entry in entries if entry != token]
        if modifier == "Mod5":
            found_mod5 = True
            entries.append(token)
        return (
            f"{match.group(1)}modifier_map {modifier} "
            f"{{ {', '.join(entries)} }};"
        )

    updated = modifier_pattern.sub(update_modifier, updated)
    if not found_mod5:
        raise ValueError("The compiled XKB keymap has no Mod5 modifier map for AltGr.")
    return updated


def validate_keymap_text(keymap: str) -> None:
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".xkb", delete=False
        ) as handle:
            handle.write(keymap)
            temporary = Path(handle.name)
        result = run(
            ["xkbcli", "compile-keymap", "--keymap", str(temporary), "--test"],
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Could not validate the generated XKB keymap: {exc}") from exc
    finally:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ValueError(detail or "The generated XKB keymap is invalid.")


def generate_custom_keymap(payload: dict[str, Any], review: dict[str, Any]) -> str:
    keymap = compile_base_keymap(
        str(payload["primary_layout"]),
        str(payload.get("primary_variant", "")),
        str(payload.get("secondary_layout", "")),
        str(payload.get("switch_option", "")),
        list(review["correction_options"]),
    )
    if any(int(item["level"]) >= 3 for item in review["character_overrides"]):
        captures = payload.get("captures", {})
        modifiers = captures.get("modifiers", {}) if isinstance(captures, dict) else {}
        right_alt = modifiers.get("right_alt", {}) if isinstance(modifiers, dict) else {}
        if not isinstance(right_alt, dict) or right_alt.get("skipped"):
            raise ValueError(
                "Right Alt/AltGr must be calibrated before applying AltGr character overrides."
            )
        try:
            right_alt_keycode = int(right_alt["keycode"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "The calibrated Right Alt/AltGr key has no usable physical keycode."
            ) from exc
        if right_alt_keycode in EXPECTED_CODES.values():
            right_alt_keycode += 8
        keymap = force_level_three_key(keymap, right_alt_keycode)
    keymap = apply_symbol_overrides(keymap, list(review["character_overrides"]))
    validate_keymap_text(keymap)
    return keymap


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


def write_generated_file(path: Path, content: str) -> tuple[Path | None, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else None
    backup: Path | None = None
    if original is not None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(f"{path.name}.keyboard-wizard.bak.{timestamp}")
        shutil.copy2(path, backup)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.chmod(path.stat().st_mode if path.exists() else 0o644)
    os.replace(temporary, path)
    return backup, original


def restore_config(path: Path, original: str) -> None:
    path.write_text(original, encoding="utf-8")


def restore_generated_file(path: Path, original: str | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
        return
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
    if not review["ok"]:
        return {
            "ok": False,
            "status": "invalid-overrides",
            "title": "Invalid character overrides",
            "message": "\n".join(review["override_errors"]),
        }

    keymap_path: Path | None = None
    keymap_text: str | None = None
    if review["character_overrides"]:
        keymap_path = custom_keymap_path(str(payload["device_name"]))
        try:
            keymap_text = generate_custom_keymap(payload, review)
        except ValueError as exc:
            return {
                "ok": False,
                "status": "keymap-error",
                "title": "Could not build character overrides",
                "message": str(exc),
            }

    begin, end, block = render_device_block(
        str(payload["device_name"]),
        str(payload["primary_layout"]),
        str(payload.get("primary_variant", "")),
        str(payload.get("secondary_layout", "")),
        str(payload.get("switch_option", "")),
        review["correction_options"],
        keymap_path,
    )

    keymap_backup: Path | None = None
    keymap_original: str | None = None
    try:
        if keymap_path is not None and keymap_text is not None:
            keymap_backup, keymap_original = write_generated_file(
                keymap_path, keymap_text
            )
        backup, original = update_generated_block(path, begin, end, block)
    except OSError as exc:
        rollback_error = ""
        if keymap_path is not None:
            try:
                restore_generated_file(keymap_path, keymap_original)
            except OSError as restore_exc:
                rollback_error = f"\n\nRestoring the previous keymap also failed: {restore_exc}"
        return {
            "ok": False,
            "status": "write-error",
            "title": "Could not save the configuration",
            "message": str(exc) + rollback_error,
        }

    status, detail = validate_hyprland()
    if status == "errors":
        try:
            restore_config(path, original)
            if keymap_path is not None:
                restore_generated_file(keymap_path, keymap_original)
            validate_hyprland()
            message = (
                "Hyprland reported errors, so the previous configuration was restored."
                f"\n\n{detail}"
            )
        except OSError as exc:
            message = (
                "Hyprland reported errors and restoring the previous configuration failed."
                f"\n\n{detail}\n\n{exc}"
            )
        return {
            "ok": False,
            "status": "rolled-back",
            "title": "Configuration was not applied",
            "message": message,
            "backup": str(backup),
            "config": str(path),
            "keymap": str(keymap_path) if keymap_path else "",
        }
    if status == "unavailable":
        response = {
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
        if keymap_path is not None:
            response["keymap"] = str(keymap_path)
            response["keymap_backup"] = str(keymap_backup) if keymap_backup else ""
        return response
    response = {
        "ok": True,
        "status": "applied",
        "warning": False,
        "title": "Keyboard configured",
        "message": "Hyprland reloaded successfully with no configuration errors.",
        "backup": str(backup),
        "config": str(path),
    }
    if keymap_path is not None:
        response["keymap"] = str(keymap_path)
        response["keymap_backup"] = str(keymap_backup) if keymap_backup else ""
    return response


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

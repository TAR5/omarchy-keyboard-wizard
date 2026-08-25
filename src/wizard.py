#!/usr/bin/env python3
"""A small Omarchy/Hyprland keyboard setup wizard."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402


APP_ID = "io.github.tar5.OmarchyKeyboardWizard"
APP_TITLE = "Keyboard Setup"
INPUT_CONFIG = Path.home() / ".config/hypr/input.lua"
XKB_RULES = Path("/usr/share/X11/xkb/rules/evdev.lst")

DEFAULT_OPTIONS = ["compose:caps", "shift:both_capslock_cancel"]

SWITCH_OPTIONS = [
    ("Alt + Shift", "grp:alt_shift_toggle"),
    ("Super + Space", "grp:win_space_toggle"),
    ("Both Alt keys", "grp:alts_toggle"),
    ("Ctrl + Space", "grp:ctrl_space_toggle"),
]


@dataclass(frozen=True)
class KeyStep:
    key: str
    label: str
    hint: str
    evdev_code: int


# GDK's Wayland keycode is normally the Linux evdev code plus eight. The
# normalizer below accepts either form so the app remains useful on XWayland
# and other GDK backends too.
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

EXPECTED_CODES = {step.key: step.evdev_code for step in KEY_STEPS}
EXPECTED_LABELS = {step.key: step.label for step in KEY_STEPS}

# Each entry describes a two-key firmware swap and the XKB option that repairs
# it. These options are supplied by xkeyboard-config rather than invented here.
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


def parse_xkb_rules(path: Path = XKB_RULES) -> tuple[list[tuple[str, str]], dict[str, list[tuple[str, str]]]]:
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

        # The kernel prints the highest 64-bit word first. Reassembling the
        # bitmap lets us require Enter, A, Z, and Space instead of treating
        # every media button as a full keyboard.
        try:
            words = key_match.group(1).split()
            key_bitmap = int("".join(word.zfill(16) for word in words), 16)
        except ValueError:
            continue
        required_codes = (28, 30, 44, 57)  # Enter, A, Z, Space
        if not all(key_bitmap & (1 << code) for code in required_codes):
            continue

        normalized = name_match.group(1).lower().replace(" ", "-")
        names.add(normalized)

    return names


def keyboard_devices() -> list[dict[str, str]]:
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
    devices: list[dict[str, str]] = []
    for item in payload.get("keyboards", []):
        name = str(item.get("name", "")).strip()
        if not name or any(fragment in name.lower() for fragment in ignored):
            continue
        if kernel_names and name not in kernel_names:
            continue
        description = str(item.get("description", "")).strip() or name
        devices.append({
            "name": name,
            "description": description,
            "main": bool(item.get("main", False)),
        })

    # Some USB keyboards expose an additional endpoint named by appending a
    # second "-keyboard". Prefer the shorter, primary endpoint in that case.
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


def analyze_captures(captures: dict[str, dict[str, object]]) -> tuple[list[str], list[str], set[str]]:
    normalized = {
        key: normalize_keycode(int(value["keycode"]))
        for key, value in captures.items()
    }
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
            warnings.append(f"{EXPECTED_LABELS[key]} was not captured.")
        elif actual != expected and key not in repaired:
            warnings.append(
                f"{EXPECTED_LABELS[key]} reports an unsupported code ({actual}); "
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
    options = list(DEFAULT_OPTIONS)
    options.extend(correction_options)

    if secondary_layout:
        layouts += f",{secondary_layout}"
        variants += ","
        options.append(switch_option)

    # Preserve order while removing duplicates.
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
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
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

    if path.exists():
        temporary.chmod(path.stat().st_mode)
    else:
        temporary.chmod(0o644)
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
        if errors:
            return "errors", errors
        return "ok", "Hyprland reloaded with no configuration errors."
    except (OSError, subprocess.TimeoutExpired) as error:
        return "unavailable", str(error)


def label(text: str, css_class: str | None = None, wrap: bool = True) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap)
    if css_class:
        widget.add_css_class(css_class)
    return widget


def clear_box(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        next_child = child.get_next_sibling()
        box.remove(child)
        child = next_child


class WizardWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application):
        super().__init__(application=application, title=APP_TITLE)
        self.set_default_size(720, 620)
        self.set_size_request(560, 480)

        self.layouts, self.variants = parse_xkb_rules()
        self.devices = keyboard_devices()
        self.captures: dict[str, dict[str, object]] = {}
        self.key_index = 0
        self.capturing = False
        self.press_pending = False
        self.pending_capture: dict[str, object] | None = None

        self.primary_layout = current_layout()
        self.primary_variant = ""
        self.secondary_layout = ""
        self.switch_option = SWITCH_OPTIONS[0][1]
        self.device_name = ""
        self.device_description = ""

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=APP_TITLE, subtitle="Omarchy"))
        toolbar.add_top_bar(header)

        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.page.set_margin_top(28)
        self.page.set_margin_bottom(28)
        self.page.set_margin_start(38)
        self.page.set_margin_end(38)
        self.scroller.set_child(self.page)
        toolbar.set_content(self.scroller)
        self.set_content(toolbar)

        self.key_controller = Gtk.EventControllerKey()
        self.key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.key_controller.connect("key-pressed", self.on_key_pressed)
        self.key_controller.connect("key-released", self.on_key_released)
        self.add_controller(self.key_controller)

        self.show_welcome()

    def page_heading(self, title: str, description: str) -> None:
        clear_box(self.page)
        self.page.append(label(title, "title-1"))
        self.page.append(label(description, "dim-label"))

    def button_row(
        self,
        primary_text: str,
        primary_callback,
        back_callback=None,
        secondary_text: str | None = None,
        secondary_callback=None,
    ) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.END)
        row.set_margin_top(10)
        if back_callback:
            back = Gtk.Button(label="Back")
            back.connect("clicked", lambda _button: back_callback())
            row.append(back)
        if secondary_text and secondary_callback:
            secondary = Gtk.Button(label=secondary_text)
            secondary.connect("clicked", lambda _button: secondary_callback())
            row.append(secondary)
        primary = Gtk.Button(label=primary_text)
        primary.add_css_class("suggested-action")
        primary.connect("clicked", lambda _button: primary_callback())
        row.append(primary)
        self.page.append(row)
        return row

    def show_welcome(self) -> None:
        self.capturing = False
        self.page_heading(
            "Set up your keyboard",
            "Choose the keyboard layout, then press each setup key when prompted. "
            "The wizard detects common firmware swaps and writes a device-specific Hyprland override.",
        )
        icon = Gtk.Image.new_from_icon_name("input-keyboard-symbolic")
        icon.set_pixel_size(112)
        icon.set_margin_top(28)
        icon.set_margin_bottom(18)
        self.page.append(icon)

        notes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        notes.append(label("• Your existing input.lua settings are preserved."))
        notes.append(label("• A timestamped backup is made before applying."))
        notes.append(label("• Hyprland is reloaded and checked for configuration errors."))
        self.page.append(notes)

        start = Gtk.Button(label="Start")
        start.add_css_class("suggested-action")
        start.add_css_class("pill")
        start.set_halign(Gtk.Align.CENTER)
        start.set_margin_top(18)
        start.connect("clicked", lambda _button: self.show_layout())
        self.page.append(start)

    def combo_row(self, title: str, subtitle: str, values: list[str], selected: int = 0) -> tuple[Gtk.Box, Gtk.DropDown]:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        row.set_margin_top(5)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(label(title, "heading"))
        text.append(label(subtitle, "dim-label"))
        row.append(text)
        dropdown = Gtk.DropDown.new_from_strings(values)
        dropdown.set_valign(Gtk.Align.CENTER)
        dropdown.set_selected(max(0, min(selected, len(values) - 1)))
        row.append(dropdown)
        return row, dropdown

    def show_layout(self) -> None:
        self.capturing = False
        self.page_heading(
            "Layout and keyboard",
            "Letters and symbols come from the selected XKB layout. Calibration handles the control and modifier keys.",
        )

        if not self.devices:
            warning = Adw.Banner(title="No physical keyboards were reported by Hyprland. Open this wizard inside your Omarchy desktop session.")
            warning.set_revealed(True)
            self.page.append(warning)
            self.button_row("Try again", self.reload_devices, self.show_welcome)
            return

        device_labels = [
            f"{device['description']}  ({device['name']})" for device in self.devices
        ]
        device_index = next(
            (index for index, device in enumerate(self.devices) if device["name"] == self.device_name),
            0,
        )
        row, self.device_dropdown = self.combo_row(
            "Keyboard",
            "Only press keys on this device during calibration",
            device_labels,
            device_index,
        )
        self.page.append(row)

        layout_labels = [f"{name} — {code}" for code, name in self.layouts]
        layout_index = next(
            (index for index, (code, _name) in enumerate(self.layouts) if code == self.primary_layout),
            0,
        )
        row, self.layout_dropdown = self.combo_row(
            "Primary layout",
            "Used for Omarchy shortcuts and normal typing",
            layout_labels,
            layout_index,
        )
        self.page.append(row)
        self.layout_dropdown.connect("notify::selected", self.on_layout_changed)

        self.variant_row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.page.append(self.variant_row_box)
        self.rebuild_variant_dropdown(self.layouts[layout_index][0])

        secondary_choices = [("", "None")] + self.layouts
        secondary_labels = [
            "None" if not code else f"{name} — {code}" for code, name in secondary_choices
        ]
        secondary_index = next(
            (index for index, (code, _name) in enumerate(secondary_choices) if code == self.secondary_layout),
            0,
        )
        row, self.secondary_dropdown = self.combo_row(
            "Secondary layout",
            "Optional layout that can be switched at any time",
            secondary_labels,
            secondary_index,
        )
        self.secondary_choices = secondary_choices
        self.page.append(row)
        self.secondary_dropdown.connect("notify::selected", self.on_secondary_changed)

        shortcut_labels = [item[0] for item in SWITCH_OPTIONS]
        shortcut_index = next(
            (index for index, item in enumerate(SWITCH_OPTIONS) if item[1] == self.switch_option),
            0,
        )
        self.shortcut_row, self.shortcut_dropdown = self.combo_row(
            "Switch layouts",
            "The Omarchy bar indicator can also be clicked",
            shortcut_labels,
            shortcut_index,
        )
        self.shortcut_row.set_visible(bool(self.secondary_layout))
        self.page.append(self.shortcut_row)

        self.button_row("Calibrate keys", self.save_layout_choices, self.show_welcome)

    def reload_devices(self) -> None:
        self.devices = keyboard_devices()
        self.show_layout()

    def on_layout_changed(self, dropdown: Gtk.DropDown, _property) -> None:
        index = dropdown.get_selected()
        if index < len(self.layouts):
            self.rebuild_variant_dropdown(self.layouts[index][0])

    def rebuild_variant_dropdown(self, layout_code: str) -> None:
        clear_box(self.variant_row_box)
        self.variant_choices = [("", "Default")] + self.variants.get(layout_code, [])
        names = ["Default" if not code else f"{name} — {code}" for code, name in self.variant_choices]
        selected = next(
            (index for index, (code, _name) in enumerate(self.variant_choices) if code == self.primary_variant),
            0,
        )
        row, self.variant_dropdown = self.combo_row(
            "Variant",
            "Optional variation of the primary layout",
            names,
            selected,
        )
        self.variant_row_box.append(row)

    def on_secondary_changed(self, dropdown: Gtk.DropDown, _property) -> None:
        selected = dropdown.get_selected()
        visible = selected < len(self.secondary_choices) and bool(self.secondary_choices[selected][0])
        self.shortcut_row.set_visible(visible)

    def save_layout_choices(self) -> None:
        device_index = self.device_dropdown.get_selected()
        layout_index = self.layout_dropdown.get_selected()
        variant_index = self.variant_dropdown.get_selected()
        secondary_index = self.secondary_dropdown.get_selected()
        shortcut_index = self.shortcut_dropdown.get_selected()

        device = self.devices[device_index]
        self.device_name = device["name"]
        self.device_description = device["description"]
        self.primary_layout = self.layouts[layout_index][0]
        self.primary_variant = self.variant_choices[variant_index][0]
        self.secondary_layout = self.secondary_choices[secondary_index][0]
        self.switch_option = SWITCH_OPTIONS[shortcut_index][1]
        self.start_calibration()

    def start_calibration(self) -> None:
        self.captures = {}
        self.key_index = 0
        self.press_pending = False
        self.pending_capture = None
        self.capturing = True
        self.show_key_step()

    def show_key_step(self) -> None:
        if self.key_index >= len(KEY_STEPS):
            self.capturing = False
            self.show_review()
            return

        step = KEY_STEPS[self.key_index]
        self.page_heading(
            f"Press {step.label} now",
            f"Use only “{self.device_description}”. Release the key to continue.",
        )

        progress = Gtk.ProgressBar(show_text=True)
        progress.set_fraction(self.key_index / len(KEY_STEPS))
        progress.set_text(f"Key {self.key_index + 1} of {len(KEY_STEPS)}")
        self.page.append(progress)

        keycap = Gtk.Label(label=step.label)
        keycap.add_css_class("title-1")
        keycap.add_css_class("card")
        keycap.set_margin_top(72)
        keycap.set_margin_bottom(24)
        keycap.set_halign(Gtk.Align.CENTER)
        self.page.append(keycap)

        prompt = label(step.hint, "title-3")
        prompt.set_xalign(0.5)
        prompt.set_justify(Gtk.Justification.CENTER)
        self.page.append(prompt)

        self.detected_label = label("Waiting for a key press…", "dim-label")
        self.detected_label.set_xalign(0.5)
        self.page.append(self.detected_label)

        self.button_row("Skip calibration", self.skip_calibration, self.show_layout)

    def on_key_pressed(self, _controller, keyval: int, keycode: int, _state) -> bool:
        if not self.capturing:
            return False
        if self.press_pending:
            return True

        name = Gdk.keyval_name(keyval) or f"keyval-{keyval}"
        self.press_pending = True
        self.pending_capture = {"keycode": int(keycode), "keyval": name}
        self.detected_label.set_text(f"Detected {name} · hardware code {keycode}. Release to continue…")
        return True

    def on_key_released(self, _controller, _keyval: int, _keycode: int, _state) -> None:
        if not self.capturing or not self.press_pending or self.pending_capture is None:
            return
        step = KEY_STEPS[self.key_index]
        self.captures[step.key] = self.pending_capture
        self.press_pending = False
        self.pending_capture = None
        self.key_index += 1
        GLib.timeout_add(220, self.advance_after_release)

    def advance_after_release(self) -> bool:
        if self.capturing:
            self.show_key_step()
        return GLib.SOURCE_REMOVE

    def skip_calibration(self) -> None:
        self.capturing = False
        self.captures = {
            step.key: {"keycode": step.evdev_code, "keyval": "not tested"}
            for step in KEY_STEPS
        }
        self.show_review()

    def show_review(self) -> None:
        self.capturing = False
        correction_options, warnings, repaired = analyze_captures(self.captures)
        self.correction_options = correction_options

        self.page_heading(
            "Review configuration",
            f"This configuration applies only to “{self.device_description}”.",
        )

        layout_text = self.primary_layout
        if self.primary_variant:
            layout_text += f" ({self.primary_variant})"
        if self.secondary_layout:
            layout_text += f"  +  {self.secondary_layout}"
        summary = Adw.ActionRow(title="Keyboard layout", subtitle=layout_text)
        summary.add_prefix(Gtk.Image.new_from_icon_name("preferences-desktop-keyboard-shortcuts-symbolic"))

        if correction_options:
            correction = Adw.ActionRow(
                title="Detected corrections",
                subtitle=", ".join(correction_options),
            )
        else:
            correction = Adw.ActionRow(
                title="Modifier mapping",
                subtitle="No supported firmware swaps detected",
            )
        correction.add_prefix(Gtk.Image.new_from_icon_name("emblem-ok-symbolic"))
        summary_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        summary_list.add_css_class("boxed-list")
        summary_list.append(summary)
        summary_list.append(correction)
        self.page.append(summary_list)

        if warnings:
            banner = Adw.Banner(title=f"{len(warnings)} key mapping issue(s) need manual attention")
            banner.set_revealed(True)
            self.page.append(banner)
            for warning in warnings:
                self.page.append(label(f"• {warning}", "dim-label"))

        results = Gtk.Expander(label="Captured keys")
        result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        result_box.set_margin_top(8)
        for step in KEY_STEPS:
            capture = self.captures.get(step.key, {})
            actual = normalize_keycode(int(capture.get("keycode", -1)))
            if actual == step.evdev_code:
                state = "correct"
            elif step.key in repaired:
                state = "will be corrected"
            else:
                state = "needs attention"
            result_box.append(label(
                f"{step.label}: {capture.get('keyval', 'missing')} · code {actual} · {state}",
                "dim-label",
            ))
        results.set_child(result_box)
        self.page.append(results)

        self.button_row(
            "Apply configuration",
            self.apply_configuration,
            self.show_layout,
            "Scan again",
            self.start_calibration,
        )

    def apply_configuration(self) -> None:
        begin, end, block = render_device_block(
            self.device_name,
            self.primary_layout,
            self.primary_variant,
            self.secondary_layout,
            self.switch_option,
            self.correction_options,
        )

        try:
            backup, original = update_generated_block(INPUT_CONFIG, begin, end, block)
        except OSError as error:
            self.show_result(False, "Could not save the configuration", str(error), None)
            return

        status, detail = validate_hyprland()
        if status == "errors":
            try:
                restore_config(INPUT_CONFIG, original)
                validate_hyprland()
                message = f"Hyprland reported errors, so the previous file was restored:\n\n{detail}"
            except OSError as error:
                message = f"Hyprland reported errors and restoring the previous file failed:\n\n{detail}\n\n{error}"
            self.show_result(False, "Configuration was not applied", message, backup)
            return

        if status == "unavailable":
            self.show_result(
                True,
                "Configuration saved",
                "The file and backup were written, but Hyprland could not be reached for validation. "
                f"Run `hyprctl reload` and `hyprctl configerrors` after returning to the desktop.\n\n{detail}",
                backup,
                warning=True,
            )
            return

        self.show_result(
            True,
            "Keyboard configured",
            "Hyprland reloaded successfully with no configuration errors.",
            backup,
        )

    def show_result(
        self,
        success: bool,
        title: str,
        description: str,
        backup: Path | None,
        warning: bool = False,
    ) -> None:
        self.capturing = False
        self.page_heading(title, description)
        icon_name = "dialog-warning-symbolic" if warning else ("emblem-ok-symbolic" if success else "dialog-error-symbolic")
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(96)
        icon.set_margin_top(42)
        icon.set_margin_bottom(22)
        self.page.append(icon)
        self.page.append(label(f"Configuration: {INPUT_CONFIG}", "dim-label"))
        if backup is not None:
            self.page.append(label(f"Backup: {backup}", "dim-label"))

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, halign=Gtk.Align.END)
        again = Gtk.Button(label="Set up another keyboard")
        again.connect("clicked", lambda _button: self.show_layout())
        row.append(again)
        close = Gtk.Button(label="Done")
        close.add_css_class("suggested-action")
        close.connect("clicked", lambda _button: self.close())
        row.append(close)
        self.page.append(row)


class KeyboardWizard(Adw.Application):
    def __init__(self, smoke_test: bool = False):
        super().__init__(application_id=APP_ID)
        self.smoke_test = smoke_test

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = WizardWindow(self)
        window.present()
        if self.smoke_test:
            GLib.idle_add(self.run_smoke_test, window)

    def run_smoke_test(self, window: WizardWindow) -> bool:
        window.show_layout()
        if window.devices:
            window.save_layout_choices()
            window.skip_calibration()
        GLib.timeout_add(700, self.quit)
        return GLib.SOURCE_REMOVE


def self_test() -> None:
    layouts, variants = parse_xkb_rules()
    assert any(code == "us" for code, _label in layouts)
    assert isinstance(variants, dict)
    assert normalize_keycode(133) == 125
    assert normalize_keycode(125) == 125

    normal = {
        step.key: {"keycode": step.evdev_code + 8, "keyval": "test"}
        for step in KEY_STEPS
    }
    options, warnings, repaired = analyze_captures(normal)
    assert options == [] and warnings == [] and repaired == set()

    swapped = dict(normal)
    swapped["left_alt"] = {"keycode": EXPECTED_CODES["left_super"] + 8, "keyval": "Super_L"}
    swapped["left_super"] = {"keycode": EXPECTED_CODES["left_alt"] + 8, "keyval": "Alt_L"}
    options, warnings, repaired = analyze_captures(swapped)
    assert options == ["altwin:swap_lalt_lwin"]
    assert warnings == []
    assert repaired == {"left_alt", "left_super"}

    begin, end, block = render_device_block("test-keyboard", "de", "nodeadkeys", "us", "grp:win_space_toggle", options)
    assert 'kb_layout = "de,us"' in block
    assert 'kb_variant = "nodeadkeys,"' in block
    assert "altwin:swap_lalt_lwin" in block

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.lua"
        path.write_text("-- existing\n", encoding="utf-8")
        update_generated_block(path, begin, end, block)
        update_generated_block(path, begin, end, block.replace("de,us", "us,de"))
        result = path.read_text(encoding="utf-8")
        assert result.count(begin) == 1
        assert "-- existing" in result
        assert "us,de" in result

    print("Keyboard Setup self-test passed")


def main() -> int:
    if "--self-test" in sys.argv:
        self_test()
        return 0
    smoke_test = "--smoke-test" in sys.argv
    arguments = [argument for argument in sys.argv if argument != "--smoke-test"]
    return KeyboardWizard(smoke_test=smoke_test).run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())

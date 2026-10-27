#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PyQt6.QtCore import QFile, QProcess, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon, QImageReader, QKeySequence, QMovie, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "Wallpaper Picker"
APP_ID = "wallpaper-picker"
WALLPAPER_ENGINE_APP_ID = "431960"

CONFIG_DIR = Path.home() / ".config" / APP_ID
CONFIG_FILE = CONFIG_DIR / "config.json"
SETUPS_FILE = CONFIG_DIR / "setups.json"   # named snapshots: which wallpaper on each monitor + the color style
STATE_DIR = Path.home() / ".local" / "state" / APP_ID
LOG_FILE = STATE_DIR / "linux-wallpaperengine.log"
WAYBAR_LOG_FILE = STATE_DIR / "waybar.log"

# How strongly the wallpaper's colors are applied; read by ~/bin/wallpaper-theme.
THEME_STYLE_FILE = Path.home() / ".config" / "wallpaper-theme" / "style"
THEME_STYLES = [
    ("Soft", "soft", "Toned-down accents from the wallpaper's dominant color (default)."),
    ("Balanced", "balanced", "Dominant color with Material's stock (brighter) accents."),
    ("Vivid", "vivid", "The most saturated patch in the image, bright accents."),
    ("Muted", "muted", "Nearly monochrome, very calm."),
]

# Dark or light backgrounds; read by ~/bin/wallpaper-theme the same way as the style.
THEME_MODE_FILE = Path.home() / ".config" / "wallpaper-theme" / "mode"
THEME_MODES = [
    ("Dark", "dark", "Dark backgrounds, light text (default)."),
    ("Light", "light", "Light backgrounds, dark text."),
]

DEFAULT_CONFIG = {
    "fps": 30,
    "scaling": "fill",
    "assignments": {},
    "properties": {},
    "skip_objects": {},
}


@dataclass(frozen=True)
class Wallpaper:
    workshop_id: str
    title: str
    folder: Path
    preview: Path | None
    problem: str | None = None


def load_config() -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            config.update({k: v for k, v in data.items() if k in config})
            if not isinstance(config.get("assignments"), dict):
                config["assignments"] = {}
            if not isinstance(config.get("properties"), dict):
                config["properties"] = {}
            if not isinstance(config.get("skip_objects"), dict):
                config["skip_objects"] = {}
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not read {CONFIG_FILE}: {exc}", file=sys.stderr)
    return config


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    temp.replace(CONFIG_FILE)


def load_setups() -> dict[str, dict]:
    """Saved setups: {name: {"assignments": {monitor: workshop_id}, "style": "muted"}}, in saved order."""
    try:
        data = json.loads(SETUPS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not read {SETUPS_FILE}: {exc}", file=sys.stderr)
        return {}
    setups: dict[str, dict] = {}
    if isinstance(data, dict):
        for name, entry in data.items():
            if isinstance(entry, dict) and isinstance(entry.get("assignments"), dict):
                setups[str(name)] = {
                    "assignments": {str(k): str(v) for k, v in entry["assignments"].items() if v},
                    "style": str(entry.get("style", "soft")),
                    "mode": str(entry.get("mode", "dark")),
                }
    return setups


def save_setups(setups: dict[str, dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temp = SETUPS_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(setups, indent=2) + "\n", encoding="utf-8")
    temp.replace(SETUPS_FILE)


def load_theme_style() -> str:
    try:
        value = THEME_STYLE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "soft"
    return value if value in {key for _, key, _ in THEME_STYLES} else "soft"


def save_theme_style(style: str) -> None:
    THEME_STYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    THEME_STYLE_FILE.write_text(style + "\n", encoding="utf-8")


def load_theme_mode() -> str:
    try:
        value = THEME_MODE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "dark"
    return value if value in {key for _, key, _ in THEME_MODES} else "dark"


def save_theme_mode(mode: str) -> None:
    THEME_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    THEME_MODE_FILE.write_text(mode + "\n", encoding="utf-8")


def move_to_trash(path: Path) -> tuple[bool, str]:
    """Move a folder to the freedesktop Trash (restorable). Returns (ok, error or trashed path)."""
    try:
        result = QFile.moveToTrash(str(path))
        ok, where = result if isinstance(result, tuple) else (bool(result), "")
        if ok:
            return True, where
    except Exception as exc:  # fall through to gio
        error = str(exc)
    else:
        error = "QFile.moveToTrash failed"
    try:
        gio = subprocess.run(["gio", "trash", str(path)], capture_output=True, text=True, timeout=120)
        if gio.returncode == 0:
            return True, ""
        error = gio.stderr.strip() or error
    except (OSError, subprocess.SubprocessError) as exc:
        error = f"{error}; gio: {exc}"
    return False, error


def _steam_library_paths() -> list[Path]:
    homes = [
        Path.home() / ".local" / "share" / "Steam",
        Path.home() / ".steam" / "steam",
        Path.home() / ".steam" / "root",
    ]
    libraries: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        try:
            normalized = path.expanduser().resolve()
        except OSError:
            normalized = path.expanduser()
        if normalized not in seen:
            seen.add(normalized)
            libraries.append(normalized)

    for home in homes:
        add(home)
        vdf = home / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in re.findall(r'"path"\s+"([^"]+)"', text):
            add(Path(raw.replace(r"\\", "\\")))

    return libraries


def workshop_roots() -> list[Path]:
    roots: list[Path] = []
    for library in _steam_library_paths():
        candidate = library / "steamapps" / "workshop" / "content" / WALLPAPER_ENGINE_APP_ID
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def wallpaper_engine_assets_dir() -> Path | None:
    """Locate the main Wallpaper Engine app's shared assets folder.

    Some workshop scenes reference stock models/materials (e.g.
    models/util/solidlayer.json) that aren't packed into the scene itself
    and are only shipped with the main app install. Without pointing
    linux-wallpaperengine at this folder via --assets-dir, those
    references silently fail and render as a blank/white placeholder.
    """
    for library in _steam_library_paths():
        candidate = library / "steamapps" / "common" / "wallpaper_engine" / "assets"
        if candidate.is_dir():
            return candidate
    return None


def _find_preview(folder: Path, project: dict) -> Path | None:
    configured = project.get("preview")
    if isinstance(configured, str) and configured.strip():
        path = folder / configured
        if path.is_file():
            return path

    exact_names = [
        "preview.jpg",
        "preview.jpeg",
        "preview.png",
        "preview.webp",
        "preview.gif",
        "preview.bmp",
    ]
    for name in exact_names:
        path = folder / name
        if path.is_file():
            return path

    for pattern in ("preview.*", "*preview*.jpg", "*preview*.png", "*preview*.webp", "*preview*.gif"):
        for path in sorted(folder.glob(pattern)):
            if path.is_file():
                return path
    return None


def discover_wallpapers() -> list[Wallpaper]:
    wallpapers: dict[str, Wallpaper] = {}

    for root in workshop_roots():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or not folder.name.isdigit():
                continue

            project: dict = {}
            project_file = folder / "project.json"
            try:
                project = json.loads(project_file.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                pass

            title = project.get("title") if isinstance(project.get("title"), str) else folder.name
            title = title.strip() or folder.name
            preview = _find_preview(folder, project)

            problems = []
            if not project:
                problems.append("project.json is missing or unreadable")
            if preview is None:
                problems.append("no preview image")
            main_file = project.get("file")
            if isinstance(main_file, str) and main_file.strip():
                if not (folder / main_file).exists() and not (folder / "scene.pkg").exists():
                    problems.append(f"missing {main_file}")

            wallpapers[folder.name] = Wallpaper(
                workshop_id=folder.name,
                title=title,
                folder=folder,
                preview=preview,
                problem="; ".join(problems) or None,
            )

    return sorted(wallpapers.values(), key=lambda w: (w.title.casefold(), w.workshop_id))


def connected_monitors(saved_assignments: dict[str, str] | None = None) -> list[str]:
    monitors: list[str] = []
    try:
        result = subprocess.run(
            ["hyprctl", "-j", "monitors"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        data = json.loads(result.stdout)
        for monitor in data:
            name = monitor.get("name")
            if isinstance(name, str) and name:
                monitors.append(name)
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        pass

    if not monitors and saved_assignments:
        monitors.extend(str(name) for name in saved_assignments if name)

    if not monitors:
        monitors = ["HDMI-A-1", "DP-3"]

    return list(dict.fromkeys(monitors))


def engine_pids() -> list[int]:
    try:
        # `linux-wallpaperengine` is longer than Linux's 15-character comm
        # field, so `pgrep -x` can miss it. Match the full command line instead.
        result = subprocess.run(
            # -u: only this user's engine; another logged-in user may run their own.
            ["pgrep", "-u", str(os.getuid()), "-f", r"(^|/)linux-wallpaperengine([[:space:]]|$)"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []

    pids = []
    for token in result.stdout.split():
        try:
            pids.append(int(token))
        except ValueError:
            continue
    return pids


def stop_engine(timeout: float = 3.0) -> None:
    pids = engine_pids()
    if not pids:
        return

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            subprocess.run(
                ["pkill", "-u", str(os.getuid()), "-TERM", "-f", r"(^|/)linux-wallpaperengine([[:space:]]|$)"],
                check=False,
            )
            break

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not engine_pids():
            return
        time.sleep(0.1)

    for pid in engine_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def build_engine_command(config: dict) -> list[str]:
    executable = shutil.which("linux-wallpaperengine")
    if not executable:
        raise RuntimeError("linux-wallpaperengine was not found in PATH.")

    assignments = config.get("assignments", {})
    if not isinstance(assignments, dict):
        raise RuntimeError("Saved monitor assignments are invalid.")

    command = [
        executable,
        "--silent",
        "--fps",
        str(int(config.get("fps", 30))),
    ]
    assets_dir = wallpaper_engine_assets_dir()
    if assets_dir is not None:
        command.extend(["--assets-dir", str(assets_dir)])
    scaling = str(config.get("scaling", "fill"))
    properties = config.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    skip_objects = config.get("skip_objects", {})
    if not isinstance(skip_objects, dict):
        skip_objects = {}

    used = 0
    seen_property_ids: set[str] = set()
    for monitor, workshop_id in assignments.items():
        monitor = str(monitor).strip()
        workshop_id = str(workshop_id).strip()
        if not monitor or not workshop_id:
            continue
        command.extend(
            [
                "--scaling",
                scaling,
                "--screen-root",
                monitor,
                "--bg",
                workshop_id,
            ]
        )
        # NOTE: linux-wallpaperengine's --set-property applies to a global
        # properties map keyed by property name, not scoped per background,
        # so this only matters if two assigned wallpapers happen to define
        # a property with the exact same key.
        if workshop_id not in seen_property_ids:
            overrides = properties.get(workshop_id, {})
            if isinstance(overrides, dict):
                for key, value in overrides.items():
                    command.extend(["--set-property", f"{key}={value}"])
            # NOTE: same global-not-per-background caveat as --set-property
            # above; object ids are scene-local so a collision across two
            # different assigned wallpapers is possible but unlikely.
            for object_id in skip_objects.get(workshop_id, []):
                command.extend(["--render-debug", f"skip-object={object_id}"])
            seen_property_ids.add(workshop_id)
        used += 1

    if used == 0:
        raise RuntimeError("No wallpapers have been assigned to a monitor.")
    return command


def launch_engine(config: dict) -> subprocess.Popen:
    command = build_engine_command(config)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("ab", buffering=0) as log:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


def restart_waybar() -> None:
    """Restart Waybar after Wallpaper Engine so the bar stays above the wallpaper."""
    executable = shutil.which("waybar")
    if not executable:
        return

    subprocess.run(["pkill", "-u", str(os.getuid()), "-TERM", "-x", "waybar"], check=False)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-x", "waybar"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            break
        time.sleep(0.1)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with WAYBAR_LOG_FILE.open("ab", buffering=0) as log:
        # `setsid -f` re-parents Waybar to the user manager, which reaps it when it is later
        # replaced; a direct child of this (long-lived) GUI would linger as a zombie.
        # ~/bin/waybar-start picks the active desktop theme's bar (~/bin/theme); plain Waybar without it.
        starter = Path.home() / "bin" / "waybar-start"
        command = [str(starter)] if os.access(starter, os.X_OK) else ["setsid", "-f", executable]
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
        ).wait()


def find_theme_script() -> str | None:
    """Locate ~/bin/wallpaper-theme.

    The systemd user manager and desktop launchers don't have ~/bin on PATH, so a PATH
    lookup alone silently fails there (login and the desktop icon); fall back to the
    known install location.
    """
    found = shutil.which("wallpaper-theme")
    if found:
        return found
    fallback = Path.home() / "bin" / "wallpaper-theme"
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    return None


def retheme_desktop() -> None:
    """Regenerate the wallpaper-driven color scheme (best effort; waits so callers can order it).

    ~/bin/wallpaper-theme reads the wallpaper assigned to DP-3 from this app's config.
    It must never slow down or break applying a wallpaper, so failures are ignored.
    """
    script = find_theme_script()
    if script is None:
        return
    try:
        subprocess.run(
            [script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            # apply_config() restarts Waybar itself right after this; don't do it twice.
            env={**os.environ, "WALLPAPER_THEME_SKIP_WAYBAR": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        pass


def apply_config(config: dict) -> subprocess.Popen:
    stop_engine()
    process = launch_engine(config)

    # Re-theme BEFORE restarting Waybar: it re-reads its imported colors file only on startup
    # or SIGUSR2, so a bar restarted first would come up with the previous wallpaper's colors.
    retheme_desktop()

    # Give the wallpaper layer a moment to register, then recreate Waybar above it.
    time.sleep(0.75)
    restart_waybar()
    return process


def load_pixmap(path: Path | None, size: QSize) -> QPixmap:
    if path is None or not path.is_file():
        placeholder = QPixmap(size)
        placeholder.fill(Qt.GlobalColor.transparent)
        return placeholder

    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    reader.setScaledSize(size)
    image = reader.read()
    if image.isNull():
        return QPixmap()
    return QPixmap.fromImage(image)


class MonitorCard(QGroupBox):
    def __init__(self, monitor: str, on_assign, on_clear):
        super().__init__(monitor)
        self.monitor = monitor
        self.on_assign = on_assign
        self.on_clear = on_clear

        layout = QVBoxLayout(self)
        self.preview = QLabel("No wallpaper assigned")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(300, 169)
        self.preview.setMaximumHeight(200)
        self.preview.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview.setScaledContents(False)

        self.title = QLabel("None")
        self.title.setWordWrap(True)
        self.identifier = QLabel("")
        self.identifier.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        buttons = QHBoxLayout()
        assign = QPushButton("Use selected")
        assign.clicked.connect(lambda: self.on_assign(self.monitor))
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.on_clear(self.monitor))
        buttons.addWidget(assign)
        buttons.addWidget(clear)

        layout.addWidget(self.preview)
        layout.addWidget(self.title)
        layout.addWidget(self.identifier)
        layout.addLayout(buttons)

    def set_wallpaper(self, wallpaper: Wallpaper | None) -> None:
        if wallpaper is None:
            self.preview.clear()
            self.preview.setText("No wallpaper assigned")
            self.title.setText("None")
            self.identifier.setText("")
            return

        pixmap = load_pixmap(wallpaper.preview, QSize(600, 338))
        if pixmap.isNull():
            self.preview.setText("Preview unavailable")
        else:
            shown = pixmap.scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview.setPixmap(shown)
        self.title.setText(wallpaper.title)
        self.identifier.setText(f"Workshop ID: {wallpaper.workshop_id}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1260, 780)

        self.config = load_config()
        self.wallpapers: list[Wallpaper] = []
        self.wallpaper_by_id: dict[str, Wallpaper] = {}
        self.cards: dict[str, MonitorCard] = {}
        self.preview_movie: QMovie | None = None
        self.theme_process: QProcess | None = None
        self._colors_rerun = False

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)

        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search downloaded wallpapers…")
        self.search.textChanged.connect(self.filter_wallpapers)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        open_button = QPushButton("Open Workshop Folder")
        open_button.clicked.connect(self.open_workshop_folder)

        # A global action, so it lives in the always-visible top row (the right column scrolls).
        self.theme_button = QPushButton("Update colors")
        self.theme_button.setToolTip(
            "Regenerate the desktop color scheme (terminal, bars, borders, lock screen, "
            "launcher, Sublime, Qt apps) from the wallpaper currently applied on DP-3."
        )
        self.theme_button.clicked.connect(self.update_colors)

        self.style_combo = QComboBox()
        for label, key, tip in THEME_STYLES:
            self.style_combo.addItem(label, key)
            self.style_combo.setItemData(self.style_combo.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self.style_combo.setCurrentIndex(max(0, self.style_combo.findData(load_theme_style())))
        self.style_combo.setToolTip(
            "How strongly the wallpaper's colors are applied. Changing it re-themes right away."
        )
        self.style_combo.activated.connect(self.style_changed)

        self.mode_combo = QComboBox()
        for label, key, tip in THEME_MODES:
            self.mode_combo.addItem(label, key)
            self.mode_combo.setItemData(self.mode_combo.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(load_theme_mode())))
        self.mode_combo.setToolTip("Dark or light backgrounds. Changing it re-themes right away.")
        self.mode_combo.activated.connect(self.mode_changed)

        # Saved setups: pick one to swap both monitors' wallpapers AND the color style in one go.
        self.setup_combo = QComboBox()
        self.setup_combo.setMinimumWidth(170)
        self.setup_combo.setPlaceholderText("Choose a setup…")
        self.setup_combo.setToolTip("Click a saved setup to switch to it (both wallpapers and the color style).")
        self.setup_combo.activated.connect(self.apply_setup)
        save_setup_button = QPushButton("Save setup…")
        save_setup_button.setToolTip("Remember the wallpapers assigned to each monitor and the color style, under a name.")
        save_setup_button.clicked.connect(self.save_setup)
        self.delete_setup_button = QPushButton("Delete setup")
        self.delete_setup_button.setToolTip("Forget the setup chosen in the list.")
        self.delete_setup_button.clicked.connect(self.delete_setup)

        controls.addWidget(self.search, 1)
        controls.addWidget(refresh_button)
        controls.addWidget(open_button)
        controls.addWidget(QLabel("Setup:"))
        controls.addWidget(self.setup_combo)
        controls.addWidget(save_setup_button)
        controls.addWidget(self.delete_setup_button)
        controls.addWidget(QLabel("Style:"))
        controls.addWidget(self.style_combo)
        controls.addWidget(QLabel("Mode:"))
        controls.addWidget(self.mode_combo)
        controls.addWidget(self.theme_button)
        outer.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.wallpaper_list = QListWidget()
        self.wallpaper_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.wallpaper_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.wallpaper_list.setMovement(QListWidget.Movement.Static)
        self.wallpaper_list.setIconSize(QSize(192, 108))
        self.wallpaper_list.setGridSize(QSize(220, 155))
        self.wallpaper_list.setSpacing(8)
        self.wallpaper_list.setWordWrap(True)
        self.wallpaper_list.itemSelectionChanged.connect(self.show_selected_preview)
        self.wallpaper_list.itemDoubleClicked.connect(self.assign_selected_to_first_monitor)
        delete_key = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.wallpaper_list)
        delete_key.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        delete_key.activated.connect(self.delete_selected)
        left_layout.addWidget(self.wallpaper_list)
        splitter.addWidget(left)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right = QWidget()
        self.right_layout = QVBoxLayout(right)

        selected_group = QGroupBox("Selected wallpaper")
        selected_layout = QVBoxLayout(selected_group)
        self.selected_preview = QLabel("Select a wallpaper")
        self.selected_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selected_preview.setMinimumSize(360, 202)
        self.selected_preview.setFrameShape(QFrame.Shape.StyledPanel)
        self.selected_title = QLabel("")
        self.selected_title.setWordWrap(True)
        self.selected_id = QLabel("")
        self.selected_id.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        selected_layout.addWidget(self.selected_preview)
        selected_layout.addWidget(self.selected_title)
        selected_layout.addWidget(self.selected_id)
        self.delete_button = QPushButton("Delete wallpaper…")
        self.delete_button.setToolTip("Move the selected wallpaper's folder to the Trash (Delete key).")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_selected)
        selected_layout.addWidget(self.delete_button)
        self.right_layout.addWidget(selected_group)

        assignments_label = QLabel("<b>Monitor assignments</b>")
        self.right_layout.addWidget(assignments_label)

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.addWidget(self.cards_container)

        self.apply_button = QPushButton("Apply wallpapers")
        self.apply_button.setMinimumHeight(42)
        self.apply_button.clicked.connect(self.apply_wallpapers)
        self.right_layout.addWidget(self.apply_button)
        self.right_layout.addStretch(1)

        right_scroll.setWidget(right)
        splitter.addWidget(right_scroll)
        splitter.setSizes([800, 460])

        outer.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

    def current_wallpaper(self) -> Wallpaper | None:
        selected = self.wallpaper_list.selectedItems()
        if not selected:
            return None
        value = selected[0].data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, Wallpaper) else None

    def refresh(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.wallpapers = discover_wallpapers()
            self.wallpaper_by_id = {w.workshop_id: w for w in self.wallpapers}
            self.populate_list()
            self.rebuild_monitor_cards()
            self.reload_setups()
            roots = workshop_roots()
            if roots:
                self.statusBar().showMessage(
                    f"Found {len(self.wallpapers)} wallpapers in {len(roots)} Steam library/libraries."
                )
            else:
                self.statusBar().showMessage(
                    "Wallpaper Engine Workshop folder was not found."
                )
        finally:
            QApplication.restoreOverrideCursor()

    def populate_list(self) -> None:
        query = self.search.text().strip().casefold()
        self.wallpaper_list.clear()

        for wallpaper in self.wallpapers:
            haystack = f"{wallpaper.title} {wallpaper.workshop_id}".casefold()
            if query and query not in haystack:
                continue

            pixmap = load_pixmap(wallpaper.preview, QSize(384, 216))
            icon = QIcon(pixmap) if not pixmap.isNull() else QIcon()
            label = f"⚠ {wallpaper.title}" if wallpaper.problem else wallpaper.title
            item = QListWidgetItem(icon, label)
            item.setData(Qt.ItemDataRole.UserRole, wallpaper)
            tooltip = f"{wallpaper.title}\nWorkshop ID: {wallpaper.workshop_id}\n{wallpaper.folder}"
            if wallpaper.problem:
                tooltip += f"\nProblem: {wallpaper.problem}"
            item.setToolTip(tooltip)
            self.wallpaper_list.addItem(item)

    def filter_wallpapers(self) -> None:
        self.populate_list()

    def rebuild_monitor_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.cards.clear()
        assignments = self.config.setdefault("assignments", {})
        monitors = connected_monitors(assignments)

        for monitor in monitors:
            assignments.setdefault(monitor, "")
            card = MonitorCard(monitor, self.assign_selected, self.clear_assignment)
            self.cards[monitor] = card
            self.cards_layout.addWidget(card)

        stale = [key for key in assignments if key not in monitors]
        for key in stale:
            assignments.pop(key, None)

        self.update_monitor_cards()

    def update_monitor_cards(self) -> None:
        assignments = self.config.get("assignments", {})
        for monitor, card in self.cards.items():
            wallpaper = self.wallpaper_by_id.get(str(assignments.get(monitor, "")))
            card.set_wallpaper(wallpaper)

    def show_selected_preview(self) -> None:
        wallpaper = self.current_wallpaper()
        if self.preview_movie is not None:
            self.preview_movie.stop()
            self.preview_movie = None

        if wallpaper is None:
            self.selected_preview.clear()
            self.selected_preview.setText("Select a wallpaper")
            self.selected_title.clear()
            self.selected_id.clear()
            self.delete_button.setEnabled(False)
            return

        self.delete_button.setEnabled(True)
        self.selected_title.setText(wallpaper.title)
        self.selected_id.setText(
            f"Workshop ID: {wallpaper.workshop_id}"
            + (f"\n⚠ {wallpaper.problem}" if wallpaper.problem else "")
        )

        if wallpaper.preview and wallpaper.preview.suffix.casefold() == ".gif":
            movie = QMovie(str(wallpaper.preview))
            movie.setScaledSize(QSize(640, 360))
            if movie.isValid():
                self.preview_movie = movie
                self.selected_preview.setMovie(movie)
                movie.start()
                return

        pixmap = load_pixmap(wallpaper.preview, QSize(960, 540))
        if pixmap.isNull():
            self.selected_preview.clear()
            self.selected_preview.setText("Preview unavailable")
        else:
            shown = pixmap.scaled(
                self.selected_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.selected_preview.setPixmap(shown)

    def assign_selected(self, monitor: str) -> None:
        wallpaper = self.current_wallpaper()
        if wallpaper is None:
            QMessageBox.information(self, APP_NAME, "Select a wallpaper first.")
            return
        self.config.setdefault("assignments", {})[monitor] = wallpaper.workshop_id
        self.update_monitor_cards()
        self.statusBar().showMessage(f"Assigned “{wallpaper.title}” to {monitor}.", 4000)

    def assign_selected_to_first_monitor(self) -> None:
        if self.cards:
            self.assign_selected(next(iter(self.cards)))

    def clear_assignment(self, monitor: str) -> None:
        self.config.setdefault("assignments", {})[monitor] = ""
        self.update_monitor_cards()

    def apply_wallpapers(self) -> None:
        try:
            save_config(self.config)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            process = apply_config(self.config)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        count = len(engine_pids())
        self.statusBar().showMessage(
            f"Applied wallpapers. linux-wallpaperengine PID {process.pid}; "
            f"{count} matching process{'es' if count != 1 else ''} running.",
            8000,
        )

    def update_colors(self) -> None:
        """Re-run ~/bin/wallpaper-theme for the wallpaper that is currently applied.

        The theme script reads config.json, which is only written by "Apply wallpapers",
        so this always reflects what is actually set, not unapplied selections here.
        """
        if self.theme_process is not None:
            return
        script = find_theme_script()
        if script is None:
            QMessageBox.warning(
                self,
                APP_NAME,
                "wallpaper-theme was not found (expected at ~/bin/wallpaper-theme).",
            )
            return

        process = QProcess(self)
        process.setProgram(script)
        process.finished.connect(self._on_colors_finished)
        process.errorOccurred.connect(self._on_colors_error)
        self.theme_process = process
        self.theme_button.setEnabled(False)
        self.theme_button.setText("Updating colors…")
        self.statusBar().showMessage("Updating colors from the wallpaper on DP-3…")
        process.start()

    def _finish_colors_run(self) -> QProcess | None:
        process, self.theme_process = self.theme_process, None
        self.theme_button.setEnabled(True)
        self.theme_button.setText("Update colors")
        if self._colors_rerun:  # the style changed while a run was in flight: run again with it
            self._colors_rerun = False
            QTimer.singleShot(0, self.update_colors)
        return process

    def _on_colors_finished(self, exit_code: int, _status) -> None:
        process = self._finish_colors_run()
        if process is None:
            return
        errors = bytes(process.readAllStandardError()).decode(errors="replace").strip()
        process.deleteLater()
        if exit_code == 0:
            self.statusBar().showMessage(
                "Colors updated. Restart any open Qt apps (e.g. the file manager) to see "
                "them there.",
                10000,
            )
        else:
            QMessageBox.warning(
                self,
                APP_NAME,
                f"Updating colors failed (exit {exit_code}).\n\n"
                f"{errors or 'See ~/.local/state/wallpaper-theme.log'}",
            )

    def _on_colors_error(self, _error) -> None:
        # Only fires for start failures/crashes; a normal non-zero exit comes via finished.
        process = self._finish_colors_run()
        if process is None:
            return
        message = process.errorString()
        process.deleteLater()
        QMessageBox.warning(self, APP_NAME, f"Could not run wallpaper-theme: {message}")

    # ---- saved setups ----------------------------------------------------------------------
    def _setup_summary(self, setup: dict) -> str:
        parts = []
        for monitor, workshop_id in setup["assignments"].items():
            wallpaper = self.wallpaper_by_id.get(workshop_id)
            parts.append(f"{monitor}: {wallpaper.title if wallpaper else f'{workshop_id} (not installed)'}")
        style = next((label for label, key, _ in THEME_STYLES if key == setup["style"]), setup["style"])
        mode = next((label for label, key, _ in THEME_MODES if key == setup["mode"]), setup["mode"])
        return "\n".join(parts + [f"Style: {style}", f"Mode: {mode}"])

    def reload_setups(self, select: str | None = None) -> None:
        """Refill the setup list from setups.json (keeps the current choice unless `select` is given)."""
        self.setups = load_setups()
        current = select if select is not None else self.setup_combo.currentData()
        self.setup_combo.clear()
        for name, setup in self.setups.items():
            self.setup_combo.addItem(name, name)
            self.setup_combo.setItemData(self.setup_combo.count() - 1, self._setup_summary(setup), Qt.ItemDataRole.ToolTipRole)
        self.setup_combo.setCurrentIndex(self.setup_combo.findData(current) if current is not None else -1)
        self.delete_setup_button.setEnabled(bool(self.setups))

    def save_setup(self) -> None:
        assignments = {m: str(w) for m, w in self.config.get("assignments", {}).items() if w}
        if not assignments:
            QMessageBox.information(self, APP_NAME, "Assign a wallpaper to at least one monitor first.")
            return
        name, ok = QInputDialog.getText(self, "Save setup", "Name for this setup:", text=self.setup_combo.currentData() or "")
        name = name.strip()
        if not ok or not name:
            return
        setups = load_setups()
        if name in setups and QMessageBox.question(
            self, APP_NAME, f"A setup named “{name}” already exists. Replace it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        setups[name] = {"assignments": assignments, "style": str(self.style_combo.currentData()),
                        "mode": str(self.mode_combo.currentData())}
        save_setups(setups)
        self.reload_setups(select=name)
        self.statusBar().showMessage(f"Saved setup “{name}”.", 5000)

    def apply_setup(self, _index: int) -> None:
        name = self.setup_combo.currentData()
        setup = self.setups.get(name) if name is not None else None
        if setup is None:
            return
        # Only monitors that exist right now and wallpapers that are still installed are applied;
        # anything else keeps what it has and is reported.
        skipped = []
        assignments = self.config.setdefault("assignments", {})
        for monitor, workshop_id in setup["assignments"].items():
            if monitor not in self.cards:
                skipped.append(f"{monitor} (monitor not connected)")
            elif workshop_id not in self.wallpaper_by_id:
                skipped.append(f"{monitor} (wallpaper {workshop_id} is no longer installed)")
            else:
                assignments[monitor] = workshop_id
        index = self.style_combo.findData(setup["style"])
        if index >= 0:
            self.style_combo.setCurrentIndex(index)     # programmatic: does not fire the re-theme
            save_theme_style(setup["style"])            # so the single re-theme in apply_config sees it
        index = self.mode_combo.findData(setup["mode"])
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
            save_theme_mode(setup["mode"])
        self.update_monitor_cards()
        self.apply_wallpapers()
        note = f"Switched to “{name}”."
        if skipped:
            note += " Skipped: " + "; ".join(skipped) + "."
        self.statusBar().showMessage(note, 10000)

    def delete_setup(self) -> None:
        name = self.setup_combo.currentData()
        if name is None:
            QMessageBox.information(self, APP_NAME, "Choose a setup in the list first.")
            return
        if QMessageBox.question(
            self, APP_NAME, f"Delete the setup “{name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        setups = load_setups()
        setups.pop(name, None)
        save_setups(setups)
        self.reload_setups(select="")
        self.statusBar().showMessage(f"Deleted setup “{name}”.", 5000)

    def style_changed(self, _index: int) -> None:
        save_theme_style(str(self.style_combo.currentData()))
        if self.theme_process is not None:
            self._colors_rerun = True  # pick the new style up when the current run ends
            return
        self.update_colors()

    def mode_changed(self, _index: int) -> None:
        save_theme_mode(str(self.mode_combo.currentData()))
        if self.theme_process is not None:
            self._colors_rerun = True  # pick the new mode up when the current run ends
            return
        self.update_colors()

    def delete_selected(self) -> None:
        wallpaper = self.current_wallpaper()
        if wallpaper is None:
            return
        # Only ever touch a numeric Workshop item folder directly inside a known Wallpaper Engine root.
        if not wallpaper.workshop_id.isdigit() or wallpaper.folder.parent not in workshop_roots():
            QMessageBox.warning(
                self,
                APP_NAME,
                f"Refusing to delete {wallpaper.folder}: it is not a Wallpaper Engine Workshop item folder.",
            )
            return

        assigned = sorted(
            m for m, wid in self.config.get("assignments", {}).items() if wid == wallpaper.workshop_id
        )
        details = [
            f"Folder: {wallpaper.folder}",
            "",
            "It will be moved to the Trash, so you can restore it from there.",
            "If you are still subscribed to it in the Steam Workshop, Steam may download it "
            "again; unsubscribe there to remove it for good.",
        ]
        if assigned:
            details.insert(
                0,
                f"It is currently assigned to {', '.join(assigned)}. That assignment will be cleared "
                "(press “Apply wallpapers” afterwards to update your desktop).\n",
            )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete wallpaper")
        box.setText(f"Move “{wallpaper.title}” to the Trash?")
        box.setInformativeText("\n".join(details))
        trash_button = box.addButton("Move to Trash", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel_button)
        box.exec()
        if box.clickedButton() is not trash_button:
            return

        ok, detail = move_to_trash(wallpaper.folder)
        if not ok:
            QMessageBox.critical(
                self, APP_NAME, f"Could not move {wallpaper.folder} to the Trash:\n{detail}"
            )
            return
        cleared = self._forget_wallpaper(wallpaper.workshop_id)
        row = self.wallpaper_list.currentRow()
        self.refresh()
        if self.wallpaper_list.count():  # keep going: select the neighbour so Delete can be repeated
            self.wallpaper_list.setCurrentRow(min(max(row, 0), self.wallpaper_list.count() - 1))
        message = f"Moved “{wallpaper.title}” to the Trash."
        if cleared:
            message += f" Cleared its assignment on {', '.join(cleared)}; press Apply wallpapers to update."
        self.statusBar().showMessage(message, 12000)

    def _forget_wallpaper(self, workshop_id: str) -> list[str]:
        """Drop every reference to a deleted wallpaper, in memory AND in the saved config (so
        login's `--apply` never tries to load a missing folder). Returns the monitors cleared."""

        def scrub(config: dict) -> list[str]:
            cleared = []
            assignments = config.setdefault("assignments", {})
            for monitor, wid in list(assignments.items()):
                if wid == workshop_id:
                    assignments[monitor] = ""
                    cleared.append(monitor)
            config.get("properties", {}).pop(workshop_id, None)
            config.get("skip_objects", {}).pop(workshop_id, None)
            return cleared

        cleared = scrub(self.config)
        try:
            saved = load_config()
            before = json.dumps(saved, sort_keys=True)
            scrub(saved)
            if json.dumps(saved, sort_keys=True) != before:
                save_config(saved)
        except OSError:
            pass
        return cleared

    def open_workshop_folder(self) -> None:
        roots = workshop_roots()
        if not roots:
            QMessageBox.warning(
                self,
                APP_NAME,
                "No Wallpaper Engine Workshop folder was found.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(roots[0])))


def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    window = MainWindow()
    window.show()
    return app.exec()


def apply_saved() -> int:
    config = load_config()
    try:
        process = apply_config(config)
    except Exception as exc:
        print(f"{APP_NAME}: {exc}", file=sys.stderr)
        return 1
    print(f"Started linux-wallpaperengine as PID {process.pid}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--apply-saved",
        action="store_true",
        help="Apply the saved monitor assignments without opening the GUI.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop linux-wallpaperengine (a desktop theme with its own background, see ~/bin/theme).",
    )
    args = parser.parse_args()
    if args.stop:
        stop_engine()
        return 0
    return apply_saved() if args.apply_saved else run_gui()


if __name__ == "__main__":
    raise SystemExit(main())

"""Builds the settings tabs from FIELD_SPECS.

Every widget, its .env key and its Config attribute come from one table, so
adding a setting later is a single row in settings_schema.py.
"""
from __future__ import annotations

import tkinter as tk
from contextlib import contextmanager
from tkinter import ttk
from typing import Any, Callable

import sounddevice as sd

from config import Config
from settings_schema import (
    ENGINES,
    FIELD_SPECS,
    TABS,
    engine_available,
    validate_profile_engine,
)
from voicebox_client import VoiceboxClient

MUTED = "#6b7280"


# --------------------------------------------------------------------------
# Live data lookups. All of these do blocking I/O -- callers must run them on
# a worker thread, never on the Tk thread.
# --------------------------------------------------------------------------
def fetch_profiles(base_url: str) -> list[dict]:
    return VoiceboxClient(base_url).list_profiles()


def fetch_downloaded_models(base_url: str) -> set[str]:
    import requests

    resp = requests.get(f"{base_url.rstrip('/')}/models/status", timeout=10)
    resp.raise_for_status()
    return {m["model_name"] for m in resp.json().get("models", []) if m.get("downloaded")}


def fetch_health(base_url: str) -> dict:
    import requests

    resp = requests.get(f"{base_url.rstrip('/')}/health", timeout=10)
    resp.raise_for_status()
    return resp.json()


def list_output_devices() -> list[tuple[str, str]]:
    """[(display label, device name)] for every output-capable device.

    The name is what gets stored -- indices shift between runs on Windows.
    """
    out: list[tuple[str, str]] = []
    try:
        hostapis = sd.query_hostapis()
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_output_channels"] > 0:
                api = hostapis[dev["hostapi"]]["name"] if dev["hostapi"] < len(hostapis) else "?"
                out.append((f"[{idx}] {dev['name']} - {api}", dev["name"]))
    except Exception:
        pass
    return out


class SettingsForm:
    """Owns the tk variables for every setting and the tabs they live on."""

    def __init__(self, notebook: ttk.Notebook, on_change: Callable[[], None]):
        self.notebook = notebook
        self._on_change_cb = on_change
        # Programmatic widget updates (loading .env, applying fetched profiles)
        # fire the same write traces a user edit does. Without this guard the
        # form marks itself dirty at startup and every close prompts about
        # unsaved changes the user never made.
        self._suppress = 0
        self.vars: dict[str, tk.Variable] = {}
        self.widgets: dict[str, tk.Widget] = {}
        self.list_texts: dict[str, tk.Text] = {}
        self.profiles: list[dict] = []
        self.downloaded: set[str] = set()
        self._engine_warning: ttk.Label | None = None
        self._frames: dict[str, ttk.Frame] = {}
        self._build()

    def on_change(self) -> None:
        if self._suppress == 0:
            self._on_change_cb()

    @contextmanager
    def quiet(self):
        """Apply programmatic updates without marking the form dirty."""
        self._suppress += 1
        try:
            yield
        finally:
            self._suppress -= 1

    # -- construction ----------------------------------------------------
    def _build(self) -> None:
        for tab in TABS:
            frame = ttk.Frame(self.notebook, padding=12)
            self.notebook.add(frame, text=tab)
            frame.columnconfigure(1, weight=1)
            self._frames[tab] = frame

        rows = {tab: 0 for tab in TABS}
        for spec in FIELD_SPECS:
            frame = self._frames[spec.tab]
            row = rows[spec.tab]
            rows[spec.tab] = self._add_field(frame, spec, row)

    def _add_field(self, frame: ttk.Frame, spec, row: int) -> int:
        label = ttk.Label(frame, text=spec.label)
        label.grid(row=row, column=0, sticky="nw", pady=(6, 0), padx=(0, 10))

        widget: tk.Widget
        if spec.kind == "bool":
            var = tk.BooleanVar(value=bool(spec.default))
            widget = ttk.Checkbutton(frame, variable=var, command=self.on_change)
        elif spec.kind in ("int", "float"):
            var = tk.StringVar(value=str(spec.default))
            widget = ttk.Spinbox(
                frame, textvariable=var, from_=spec.minimum, to=spec.maximum, width=12
            )
            var.trace_add("write", lambda *_: self.on_change())
        elif spec.kind == "choice":
            var = tk.StringVar(value=str(spec.default))
            widget = ttk.Combobox(
                frame, textvariable=var, values=list(spec.choices), state="readonly", width=24
            )
            widget.bind("<<ComboboxSelected>>", lambda _e: self.on_change())
        elif spec.kind in ("profile", "device"):
            var = tk.StringVar(value=str(spec.default))
            widget = ttk.Combobox(frame, textvariable=var, width=38)
            widget.bind("<<ComboboxSelected>>", lambda _e, s=spec: self._on_combo(s))
            var.trace_add("write", lambda *_: self.on_change())
        elif spec.kind == "list":
            var = tk.StringVar(value=",".join(spec.default))
            text = tk.Text(frame, height=5, width=34, wrap="none")
            text.insert("1.0", "\n".join(spec.default))
            text.bind("<KeyRelease>", lambda _e: self.on_change())
            self.list_texts[spec.attr] = text
            widget = text
        elif spec.kind == "secret":
            var = tk.StringVar(value=str(spec.default))
            holder = ttk.Frame(frame)
            entry = ttk.Entry(holder, textvariable=var, show="•", width=32)
            entry.pack(side="left")
            show = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                holder, text="Show", variable=show,
                command=lambda: entry.configure(show="" if show.get() else "•"),
            ).pack(side="left", padx=(6, 0))
            var.trace_add("write", lambda *_: self.on_change())
            widget = holder
        else:  # text
            var = tk.StringVar(value=str(spec.default))
            widget = ttk.Entry(frame, textvariable=var, width=34)
            var.trace_add("write", lambda *_: self.on_change())

        widget.grid(row=row, column=1, sticky="w", pady=(6, 0))
        self.vars[spec.attr] = var
        self.widgets[spec.attr] = widget
        row += 1

        if spec.attr == "voicebox_engine":
            self._engine_warning = ttk.Label(
                frame, text="", foreground="#b91c1c", wraplength=430, justify="left"
            )
            self._engine_warning.grid(row=row, column=1, sticky="w")
            row += 1

        if spec.help:
            ttk.Label(
                frame, text=spec.help, foreground=MUTED, wraplength=430, justify="left"
            ).grid(row=row, column=1, sticky="w", pady=(0, 4))
            row += 1
        return row

    # -- combo behaviour -------------------------------------------------
    def _on_combo(self, spec) -> None:
        if spec.attr == "voicebox_profile":
            # Selecting a profile adopts the engine it was built for -- the
            # single most common way to end up with an unusable pairing.
            profile = self.profile_by_name(self.vars["voicebox_profile"].get())
            if profile and profile.get("default_engine"):
                self.vars["voicebox_engine"].set(profile["default_engine"])
        self.refresh_engine_warning()
        self.on_change()

    def profile_by_name(self, name: str) -> dict | None:
        for p in self.profiles:
            if p.get("name") == name or p.get("id") == name:
                return p
        return None

    def refresh_engine_warning(self) -> str | None:
        """Update the inline warning; returns the message if Start must block."""
        if self._engine_warning is None:
            return None
        engine = self.vars["voicebox_engine"].get()
        profile = self.profile_by_name(self.vars["voicebox_profile"].get())
        blocking = validate_profile_engine(profile, engine)
        note = blocking
        if not note and self.downloaded and not engine_available(engine, self.downloaded):
            note = f"The model for '{engine}' is not downloaded yet in Voicebox."
        self._engine_warning.configure(
            text=note or "", foreground="#b91c1c" if blocking else "#b45309"
        )
        return blocking

    # -- populate from live data -----------------------------------------
    def set_profiles(self, profiles: list[dict]) -> None:
        self.profiles = profiles
        combo = self.widgets.get("voicebox_profile")
        if isinstance(combo, ttk.Combobox):
            combo.configure(values=[p["name"] for p in profiles])
        self.refresh_engine_warning()

    def set_downloaded(self, models: set[str]) -> None:
        self.downloaded = models
        combo = self.widgets.get("voicebox_engine")
        if isinstance(combo, ttk.Combobox):
            combo.configure(
                values=[
                    e if engine_available(e, models) else f"{e}  (not downloaded)"
                    for e in ENGINES
                ]
            )
        self.refresh_engine_warning()

    def set_devices(self, devices: list[tuple[str, str]]) -> None:
        combo = self.widgets.get("audio_output_device")
        if isinstance(combo, ttk.Combobox):
            combo.configure(values=["(auto-detect VB-Cable)"] + [d[0] for d in devices])
        self._devices = devices

    # -- value marshalling -----------------------------------------------
    def env_values(self) -> dict[str, str]:
        """Current widget state as {ENV_KEY: serialized string}."""
        out: dict[str, str] = {}
        for spec in FIELD_SPECS:
            if spec.kind == "list":
                raw = self.list_texts[spec.attr].get("1.0", "end").strip()
                names = [ln.strip().lower() for ln in raw.splitlines() if ln.strip()]
                out[spec.env_key] = ",".join(names)
                continue
            value: Any = self.vars[spec.attr].get()
            if spec.kind == "bool":
                out[spec.env_key] = "true" if value else "false"
            elif spec.kind == "choice" and spec.attr == "voicebox_engine":
                out[spec.env_key] = str(value).split("  (")[0]
            elif spec.kind == "device":
                out[spec.env_key] = "" if str(value).startswith("(auto") else _device_name(
                    str(value), getattr(self, "_devices", [])
                )
            else:
                out[spec.env_key] = str(value)
        return out

    def to_config(self) -> Config:
        """Build a Config from the widgets, never from os.environ."""
        return Config.from_env(self.env_values())

    def load(self, values: dict[str, str]) -> None:
        """Populate widgets from {ENV_KEY: string}. Never marks dirty."""
        with self.quiet():
            self._load(values)

    def _load(self, values: dict[str, str]) -> None:
        for spec in FIELD_SPECS:
            raw = values.get(spec.env_key)
            parsed = spec.parse(values) if raw is not None else spec.default
            if spec.kind == "list":
                text = self.list_texts[spec.attr]
                text.delete("1.0", "end")
                text.insert("1.0", "\n".join(parsed))
            elif spec.kind == "bool":
                self.vars[spec.attr].set(bool(parsed))
            elif spec.kind == "device":
                label = _device_label(str(parsed), getattr(self, "_devices", []))
                self.vars[spec.attr].set(label)
            else:
                self.vars[spec.attr].set(_fmt(parsed))
        self.refresh_engine_warning()


def _fmt(value) -> str:
    """Render a value for a widget without adding noise to .env.

    A float timeout of 180 must stay "180", not become "180.0", or every save
    rewrites a line the user never touched.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _device_name(label: str, devices: list[tuple[str, str]]) -> str:
    for lbl, name in devices:
        if lbl == label:
            return name
    return label


def _device_label(name: str, devices: list[tuple[str, str]]) -> str:
    if not name:
        return "(auto-detect VB-Cable)"
    for lbl, dev_name in devices:
        if dev_name == name:
            return lbl
    return name

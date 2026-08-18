"""Tkinter control panel for the Twitch -> Voicebox TTS bot.

Launched by `python main.py`. The bot itself runs on a background asyncio
thread (see bot_runner.py); this module only touches Tk.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import bot_runner as br
from bot_runner import BotRunner
from config import Config
from env_file import read_env, write_env
from gui_forms import (
    SettingsForm,
    fetch_downloaded_models,
    fetch_health,
    fetch_profiles,
    list_output_devices,
)
from settings_schema import RESTART_FIELDS

LOG_LINES_MAX = 2000
PUMP_MS = 100
PUMP_BATCH = 200

STATE_STYLE = {
    br.STOPPED: ("Stopped", "#6b7280"),
    br.STARTING: ("Starting...", "#b45309"),
    br.WARMING: ("Warming up", "#b45309"),
    br.LISTENING: ("Listening", "#15803d"),
    br.RECONNECTING: ("Reconnecting...", "#b45309"),
    br.STOPPING: ("Stopping...", "#b45309"),
    br.ERROR: ("Error", "#b91c1c"),
}


def app_dir() -> Path:
    """Where .env lives -- next to the exe when frozen, else next to this file."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


class App:
    def __init__(self, root: tk.Tk, autostart: bool = False):
        self.root = root
        self.runner = BotRunner()
        self.env_path = app_dir() / ".env"
        self.dirty = False
        # Worker threads post callables here instead of calling root.after
        # directly -- Tk is not thread-safe, and after() from another thread
        # raises "main thread is not in main loop".
        self.ui_q: queue.Queue = queue.Queue()
        self._warm_started = 0.0
        self._backup_taken = False

        root.title("sy-yapper - Twitch chat TTS")
        root.geometry("860x640")
        root.minsize(720, 520)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self._on_tk_error

        self._build_toolbar()
        self._build_banner()
        self._build_notebook()
        self._build_footer()

        self._install_log_handler()
        self.reload_from_env()
        self.refresh_live_data()

        self.root.after(PUMP_MS, self._pump)
        if autostart:
            # Give the window a beat to paint before the bot floods it with logs.
            self.root.after(400, self.start)

    # -- layout ----------------------------------------------------------
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        bar.pack(fill="x")

        self.start_btn = ttk.Button(bar, text="Start", command=self.start, width=9)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop, width=9, state="disabled")
        self.stop_btn.pack(side="left", padx=(6, 12))

        self.status = tk.Label(bar, text="Stopped", fg="white", bg="#6b7280", padx=10, pady=3)
        self.status.pack(side="left")

        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=90)

        self.stats_lbl = ttk.Label(bar, text="", foreground="#6b7280")
        self.stats_lbl.pack(side="right")

    def _build_banner(self) -> None:
        self.banner = tk.Frame(self.root, bg="#fee2e2")
        self.banner_lbl = tk.Label(
            self.banner, text="", bg="#fee2e2", fg="#7f1d1d", justify="left",
            wraplength=700, anchor="w", padx=10, pady=6,
        )
        self.banner_lbl.pack(side="left", fill="x", expand=True)
        tk.Button(
            self.banner, text="x", bg="#fee2e2", fg="#7f1d1d", bd=0,
            command=self.hide_banner, padx=8,
        ).pack(side="right")

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=6)

        # Log tab first -- it is what you watch while streaming.
        log_frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(log_frame, text="Log")
        self.log = tk.Text(
            log_frame, wrap="word", height=18, state="disabled",
            bg="#0f172a", fg="#e2e8f0", insertbackground="#e2e8f0",
            font=("Consolas", 9), relief="flat",
        )
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("WARNING", foreground="#fbbf24")
        self.log.tag_configure("ERROR", foreground="#f87171")
        self.log.tag_configure("SPEAK", foreground="#4ade80")

        self.autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(log_frame, text="Autoscroll", variable=self.autoscroll).pack(
            side="bottom", anchor="w"
        )

        self.form = SettingsForm(self.notebook, on_change=self.mark_dirty)
        self._add_voice_actions()
        self._add_audio_actions()
        self._add_preview()

    def _add_voice_actions(self) -> None:
        frame = self.form._frames["Voice"]
        row = frame.grid_size()[1]
        bar = ttk.Frame(frame)
        bar.grid(row=row, column=1, sticky="w", pady=(10, 0))
        ttk.Button(bar, text="Refresh profiles", command=self.refresh_live_data).pack(side="left")
        ttk.Button(bar, text="Test connection", command=self.test_connection).pack(
            side="left", padx=(6, 0)
        )
        self.health_lbl = ttk.Label(frame, text="", foreground="#6b7280", wraplength=430)
        self.health_lbl.grid(row=row + 1, column=1, sticky="w", pady=(4, 0))

    def _add_audio_actions(self) -> None:
        frame = self.form._frames["Audio"]
        row = frame.grid_size()[1]
        bar = ttk.Frame(frame)
        bar.grid(row=row, column=1, sticky="w", pady=(10, 0))
        ttk.Button(bar, text="Refresh devices", command=self.refresh_devices).pack(side="left")
        ttk.Button(bar, text="Test tone", command=self.test_tone).pack(side="left", padx=(6, 0))
        self.device_note = ttk.Label(frame, text="", foreground="#6b7280", wraplength=430)
        self.device_note.grid(row=row + 1, column=1, sticky="w", pady=(4, 0))

    def _add_preview(self) -> None:
        """Live preview of exactly what the bot would say for a given message."""
        frame = self.form._frames["Behavior"]
        row = frame.grid_size()[1]
        ttk.Separator(frame, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(12, 8)
        )
        ttk.Label(frame, text="Preview").grid(row=row + 1, column=0, sticky="nw")
        self.preview_in = tk.StringVar(value="Kappa that clutch was insane")
        entry = ttk.Entry(frame, textvariable=self.preview_in, width=34)
        entry.grid(row=row + 1, column=1, sticky="w")
        self.preview_out = ttk.Label(frame, text="", foreground="#15803d", wraplength=430)
        self.preview_out.grid(row=row + 2, column=1, sticky="w", pady=(4, 0))
        ttk.Label(
            frame,
            text="Shows username prefix, emoji stripping and truncation. Twitch emote "
                 "removal cannot be previewed -- it relies on the character ranges "
                 "Twitch attaches to real messages.",
            foreground="#6b7280", wraplength=430, justify="left",
        ).grid(row=row + 3, column=1, sticky="w", pady=(2, 0))
        self.preview_in.trace_add("write", lambda *_: self.update_preview())

    def _build_footer(self) -> None:
        foot = ttk.Frame(self.root, padding=(12, 0, 12, 10))
        foot.pack(fill="x")
        self.dirty_lbl = ttk.Label(foot, text="", foreground="#b45309")
        self.dirty_lbl.pack(side="left")
        ttk.Button(foot, text="Save to .env", command=self.save_env).pack(side="right")
        ttk.Button(foot, text="Reload", command=self.reload_from_env).pack(
            side="right", padx=(0, 6)
        )
        ttk.Button(foot, text="Apply", command=self.apply).pack(side="right", padx=(0, 6))

    # -- logging ---------------------------------------------------------
    def _install_log_handler(self) -> None:
        self.log_q: queue.Queue = queue.Queue(maxsize=5000)
        handler = logging.handlers.QueueHandler(self.log_q)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)

    def append_log(self, text: str, tag: str = "") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag or ())
        excess = int(self.log.index("end-1c").split(".")[0]) - LOG_LINES_MAX
        if excess > 0:
            self.log.delete("1.0", f"{excess}.0")
        self.log.configure(state="disabled")
        if self.autoscroll.get():
            self.log.see("end")

    # -- the pump --------------------------------------------------------
    def _pump(self) -> None:
        try:
            for _ in range(PUMP_BATCH):
                try:
                    record = self.log_q.get_nowait()
                except queue.Empty:
                    break
                msg = record.getMessage() if hasattr(record, "getMessage") else str(record)
                tag = record.levelname if record.levelno >= logging.WARNING else ""
                if getattr(record, "speak", False):
                    tag = "SPEAK"
                self.append_log(msg, tag)

            for _ in range(PUMP_BATCH):
                try:
                    kind, payload = self.runner.events.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(kind, payload)

            for _ in range(PUMP_BATCH):
                try:
                    fn = self.ui_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:
                    logging.getLogger(__name__).exception("UI callback failed")
        finally:
            self._refresh_status()
            self.root.after(PUMP_MS, self._pump)

    def _handle_event(self, kind: str, payload) -> None:
        if kind == "state":
            state, detail = payload
            if state == br.ERROR and detail:
                self.show_banner(str(detail))
            if state == br.WARMING:
                self._warm_started = time.monotonic()
        elif kind == "warmup_failed":
            self.show_banner(
                f"Warmup failed: {payload}\nEvery message is likely to fail too -- "
                "check that Voicebox is running and the engine matches the profile."
            )

    def _refresh_status(self) -> None:
        state = self.runner.state
        text, colour = STATE_STYLE.get(state, (state, "#6b7280"))
        if state == br.WARMING and self._warm_started:
            elapsed = int(time.monotonic() - self._warm_started)
            engine = self.form.vars["voicebox_engine"].get().split("  (")[0]
            text = f"Warming up {engine} - {elapsed}s"
        elif state == br.LISTENING:
            text = f"Listening to #{self.form.vars['twitch_channel'].get()}"
        self.status.configure(text=text, bg=colour)

        busy = state in (br.STARTING, br.WARMING, br.STOPPING)
        if busy and not self.progress.winfo_ismapped():
            self.progress.pack(side="left", padx=(10, 0))
            self.progress.start(12)
        elif not busy and self.progress.winfo_ismapped():
            self.progress.stop()
            self.progress.pack_forget()

        running = state not in (br.STOPPED, br.ERROR)
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

        s = self.runner.stats
        self.stats_lbl.configure(
            text=f"spoken {s.spoken} · skipped {s.skipped} · dropped {s.dropped} · errors {s.errors}"
        )

    # -- banner ----------------------------------------------------------
    def show_banner(self, text: str) -> None:
        self.banner_lbl.configure(text=text)
        if not self.banner.winfo_ismapped():
            self.banner.pack(fill="x", padx=12, pady=(0, 4), after=self.root.winfo_children()[0])

    def hide_banner(self) -> None:
        # Clear the text too, so a stale error cannot reappear later.
        self.banner_lbl.configure(text="")
        if self.banner.winfo_ismapped():
            self.banner.pack_forget()

    def _on_tk_error(self, exc, val, tb) -> None:
        import traceback

        self.append_log("".join(traceback.format_exception(exc, val, tb)).rstrip(), "ERROR")

    # -- settings --------------------------------------------------------
    def mark_dirty(self) -> None:
        self.dirty = True
        self.dirty_lbl.configure(text="● unsaved changes")
        self.update_preview()

    def clear_dirty(self) -> None:
        self.dirty = False
        self.dirty_lbl.configure(text="")

    def update_preview(self) -> None:
        try:
            from main import build_spoken_text
            from twitch_chat import ChatMessage

            cfg = self.form.to_config()
            out = build_spoken_text(ChatMessage("chatter", self.preview_in.get(), ""), cfg)
            self.preview_out.configure(
                text="(skipped - nothing left to say)" if out is None else out,
                foreground="#6b7280" if out is None else "#15803d",
            )
        except Exception:
            pass

    def reload_from_env(self) -> None:
        values = read_env(self.env_path)
        self.form.load(values)
        self.clear_dirty()
        self.update_preview()

    def save_env(self) -> None:
        if not self._backup_taken and self.env_path.exists():
            backup = self.env_path.with_name(self.env_path.name + ".bak")
            backup.write_bytes(self.env_path.read_bytes())
            self._backup_taken = True
        write_env(self.env_path, self.form.env_values())
        self.clear_dirty()
        self.append_log(f"Saved settings to {self.env_path}")

    def apply(self) -> None:
        """Push settings to the running bot, restarting only if required."""
        cfg = self.form.to_config()
        if self.runner.state in (br.STOPPED, br.ERROR):
            self.append_log("Settings applied (bot is not running).")
            self.clear_dirty()
            return
        changed = self._changed_restart_fields(cfg)
        if changed:
            self.append_log(f"Restarting bot for: {', '.join(sorted(changed))}")
            self.restart()
        else:
            self.runner.apply_live(cfg)
            self.append_log("Applied live (no restart needed).")
        self.clear_dirty()

    def _changed_restart_fields(self, cfg: Config) -> set[str]:
        live = self.runner._cfg
        if live is None:
            return set()
        return {a for a in RESTART_FIELDS if getattr(cfg, a) != getattr(live, a)}

    def post_ui(self, fn) -> None:
        """Schedule `fn` to run on the Tk thread. Safe from any thread."""
        self.ui_q.put(fn)

    # -- live data -------------------------------------------------------
    def refresh_live_data(self) -> None:
        base = self.form.vars["voicebox_base_url"].get()

        def work() -> None:
            try:
                profiles = fetch_profiles(base)
            except Exception as exc:
                message = f"Voicebox offline ({exc}). Refresh to retry."
                self.post_ui(lambda: self.health_lbl.configure(text=message))
                # None means "keep what we already have" -- blanking the list
                # would silently disable the profile/engine compatibility check.
                profiles = None
            try:
                models = fetch_downloaded_models(base)
            except Exception:
                models = set()
            self.post_ui(lambda: self._apply_live_data(profiles, models))

        threading.Thread(target=work, daemon=True).start()
        self.refresh_devices()

    def _apply_live_data(self, profiles: list[dict] | None, models: set[str]) -> None:
        with self.form.quiet():
            selected = self.form.vars["voicebox_profile"].get()
            if profiles is not None:
                self.form.set_profiles(profiles)
                self.health_lbl.configure(text=f"{len(profiles)} profile(s) available.")
            if models:
                self.form.set_downloaded(models)
            self.form.vars["voicebox_profile"].set(selected)
            self.form.refresh_engine_warning()

    def refresh_devices(self) -> None:
        def work() -> None:
            devices = list_output_devices()
            self.post_ui(lambda: self._apply_devices(devices))

        threading.Thread(target=work, daemon=True).start()

    def _apply_devices(self, devices) -> None:
        with self.form.quiet():
            current = self.form.vars["audio_output_device"].get()
            self.form.set_devices(devices)
            self.form.vars["audio_output_device"].set(current)

    def test_connection(self) -> None:
        base = self.form.vars["voicebox_base_url"].get()

        def work() -> None:
            try:
                h = fetch_health(base)
                text = (
                    f"{h.get('status')} · model_loaded={h.get('model_loaded')} · "
                    f"backend={h.get('backend_variant')} · gpu={h.get('gpu_type') or 'none'}"
                )
            except Exception as exc:
                text = f"Could not reach Voicebox: {exc}"
            self.post_ui(lambda: self.health_lbl.configure(text=text))

        threading.Thread(target=work, daemon=True).start()

    def test_tone(self) -> None:
        """Play a short tone so you can confirm it lands in OBS."""
        def work() -> None:
            try:
                import numpy as np

                from audio_player import AudioPlayer, resolve_device

                cfg = self.form.to_config()
                player = AudioPlayer(resolve_device(cfg.audio_output_device))
                sr = 24000
                t = np.linspace(0, 0.4, int(sr * 0.4), endpoint=False)
                tone = (0.2 * np.sin(2 * np.pi * 440 * t)).astype("float32")
                import io

                import soundfile as sf

                buf = io.BytesIO()
                sf.write(buf, tone, sr, format="WAV")
                player.play(buf.getvalue())
                msg = "Test tone sent."
            except Exception as exc:
                msg = f"Test tone failed: {exc}"
            self.post_ui(lambda: self.device_note.configure(text=msg))

        threading.Thread(target=work, daemon=True).start()

    # -- bot control -----------------------------------------------------
    def start(self) -> None:
        blocking = self.form.refresh_engine_warning()
        if blocking:
            self.notebook.select(self.form._frames["Voice"])
            self.show_banner(blocking)
            return
        cfg = self.form.to_config()
        if not cfg.twitch_channel:
            self.notebook.select(self.form._frames["Twitch"])
            self.show_banner("Set a Twitch channel first.")
            return
        self.hide_banner()
        self.runner.start(cfg)

    def stop(self) -> None:
        self.runner.stop()

    def restart(self) -> None:
        self.runner.stop()

        def when_stopped() -> None:
            if self.runner.state == br.STOPPED:
                self.start()
            else:
                self.root.after(200, when_stopped)

        self.root.after(200, when_stopped)

    def on_close(self) -> None:
        if self.dirty and not messagebox.askokcancel(
            "Unsaved changes", "You have unsaved settings. Close anyway?"
        ):
            return
        self.status.configure(text="Stopping...", bg="#b45309")
        self.root.update_idletasks()
        self.runner.shutdown(timeout=5)
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        logging.shutdown()
        # Closing the window ends the program. os._exit rather than returning
        # from mainloop: a synthesis request stuck in requests.post holds a
        # non-daemon ThreadPoolExecutor thread that atexit would join, which
        # can stall exit for the full VOICEBOX_TIMEOUT.
        os._exit(0)


def run_gui(autostart: bool = False) -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root, autostart=autostart)
    root.mainloop()


if __name__ == "__main__":
    run_gui()

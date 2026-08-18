"""Owns the bot's asyncio loop on a background thread.

Deliberately free of any Tkinter import: this is the headless half, so it can
be smoke-tested without a display, and so the CLI never pulls in a GUI.

Threading model:

    caller's thread (Tk)                asyncio thread (daemon)
    --------------------                -----------------------
    start()  -> run_coroutine_threadsafe(_run())
    stop()   -> player.stop() directly, then task.cancel via call_soon_threadsafe
    drain events from a thread-safe queue.Queue
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any

import main as bot
from config import Config
from errors import ConfigError

logger = logging.getLogger(__name__)

# Fields read fresh on every message, so they can be applied without a restart.
LIVE_FIELDS = tuple(
    spec.attr for spec in __import__("settings_schema").FIELD_SPECS if not spec.restart
)


def _first_cause(exc: BaseException) -> BaseException:
    """Unwrap nested ExceptionGroups down to the first underlying exception."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


STOPPED = "stopped"
STARTING = "starting"
WARMING = "warming"
LISTENING = "listening"
RECONNECTING = "reconnecting"
STOPPING = "stopping"
ERROR = "error"


@dataclass
class Stats:
    spoken: int = 0
    skipped: int = 0
    dropped: int = 0
    errors: int = 0
    queued: int = 0


class BotRunner:
    """Starts/stops the bot on its own event loop and reports back by queue."""

    def __init__(self) -> None:
        self.events: "queue.Queue[tuple[str, Any]]" = queue.Queue(maxsize=5000)
        self.stats = Stats()
        self.state = STOPPED
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Future | None = None
        # The real asyncio.Task. run_coroutine_threadsafe hands back a
        # concurrent.futures.Future whose .cancel() is a no-op once the
        # coroutine is running, so cancelling *that* would silently do nothing.
        self._atask: asyncio.Task | None = None
        self._player = None
        self._cfg: Config | None = None
        self._ready = threading.Event()

    # -- lifecycle -------------------------------------------------------
    def start_loop(self) -> None:
        """Spin up the event loop thread once, for the app's lifetime."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop_main, name="bot-loop", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _loop_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def _emit(self, kind: str, payload: Any = None) -> None:
        try:
            self.events.put_nowait((kind, payload))
        except queue.Full:
            pass  # a chat raid must never block the bot on a full UI queue

    def _set_state(self, state: str, detail: Any = None) -> None:
        self.state = state
        self._emit("state", (state, detail))

    # -- start -----------------------------------------------------------
    def start(self, cfg: Config) -> None:
        if self.state not in (STOPPED, ERROR):
            return
        self.start_loop()
        self.stats = Stats()
        self._set_state(STARTING)
        assert self._loop is not None
        # Keep the exact instance the bot closes over, so apply_live can mutate it.
        self._cfg = cfg
        self._task = asyncio.run_coroutine_threadsafe(self._run(cfg), self._loop)

    async def _run(self, cfg: Config) -> None:
        self._atask = asyncio.current_task()
        try:
            # Both of these do blocking I/O (an HTTP call and a device scan),
            # so they go off the loop and their ConfigErrors become a banner.
            components = await asyncio.to_thread(bot.build_components, cfg, self._on_state)
            self._player = components[2]
            self._player.reset()
            await bot.run_bot(cfg, on_event=self._on_event, components=components)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            # run_bot uses a TaskGroup, which wraps whatever failed inside an
            # ExceptionGroup -- unwrap to the first real cause for the banner.
            cause = _first_cause(exc)
            # GeneratorExit arrives when the coroutine is torn down at shutdown;
            # it is a normal stop, not a crash worth showing the user.
            if isinstance(cause, (asyncio.CancelledError, GeneratorExit)):
                raise
            if isinstance(cause, ConfigError):
                self._set_state(ERROR, str(cause))
                return
            logger.error("Bot stopped unexpectedly", exc_info=cause)
            self._set_state(ERROR, str(cause) or type(cause).__name__)
            return

    # -- callbacks from the bot ------------------------------------------
    def _on_state(self, state: str) -> None:
        if state == "connected" and self.state in (STARTING, RECONNECTING, WARMING):
            self._emit("connected", None)
        elif state == "reconnecting":
            self._set_state(RECONNECTING)

    def _on_event(self, kind: str, payload: Any) -> None:
        if kind == "spoke":
            self.stats.spoken += 1
        elif kind == "skipped":
            self.stats.skipped += 1
        elif kind == "dropped":
            self.stats.dropped += 1
        elif kind == "error":
            self.stats.errors += 1
        elif kind == "warming":
            self._set_state(WARMING, payload)
        elif kind == "listening":
            self._set_state(LISTENING, payload)
        self._emit(kind, payload)

    # -- stop ------------------------------------------------------------
    def stop(self) -> None:
        if self.state == STOPPED:
            return
        self._set_state(STOPPING)
        # Unblock any in-progress playback first so the write loop returns
        # promptly; cancelling alone would leave it writing to the end.
        if self._player is not None:
            self._player.stop()
        task, loop, atask = self._task, self._loop, self._atask
        if task is not None and loop is not None and atask is not None:
            loop.call_soon_threadsafe(atask.cancel)

            def _await_stop() -> None:
                try:
                    task.result(timeout=15)
                except BaseException:
                    pass  # CancelledError is the expected outcome here
                # Only report STOPPED if nothing has started since. A slow stop
                # completing after a fresh start would otherwise clobber the
                # new run's state and leave the UI lying about what is running.
                if self._task is not task:
                    return
                self._task = None
                self._atask = None
                self._set_state(STOPPED)

            threading.Thread(target=_await_stop, daemon=True).start()
        else:
            self._set_state(STOPPED)

    def shutdown(self, timeout: float = 5.0) -> bool:
        """Stop the bot and tear the loop down. Best-effort, never raises.

        Waits for the cancelled task to actually finish before stopping the
        loop -- killing the loop mid-cancellation orphans the task and logs
        "Task was destroyed but it is pending". Returns False if the wait timed
        out, which tells the caller a hard exit is warranted (a synthesis
        request stuck in requests.post can hold a non-daemon executor thread
        for the full timeout, which would otherwise hang interpreter exit).
        """
        task = self._task
        try:
            self.stop()
        except Exception:
            pass
        clean = True
        if task is not None:
            try:
                task.result(timeout=timeout)
            except TimeoutError:
                clean = False
            except BaseException:
                pass  # cancelled, which is exactly what we asked for
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        return clean

    def apply_live(self, cfg: Config) -> None:
        """Push settings read fresh per message onto the *running* Config.

        Must mutate the very object the bot closed over -- handing it a new
        instance would change nothing. Only safe for fields not captured at
        construction time; the GUI checks RESTART_FIELDS before calling this.
        """
        live = self._cfg
        if live is None:
            return
        for attr in LIVE_FIELDS:
            value = getattr(cfg, attr)
            # Replace list objects wholesale rather than mutating in place, so
            # the consumer thread never sees a half-updated list.
            setattr(live, attr, list(value) if isinstance(value, list) else value)

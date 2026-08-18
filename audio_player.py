"""Plays WAV audio bytes out to a specific output device (e.g. a VB-Cable input)."""
from __future__ import annotations

import io
import logging
import threading

import numpy as np

import sounddevice as sd
import soundfile as sf

from errors import ConfigError

logger = logging.getLogger(__name__)


def resolve_device(spec: str) -> int:
    """Resolve a device spec (index, exact name, or substring) to a sounddevice index.

    Falls back to auto-detecting a VB-Cable input device if spec is empty.
    """
    devices = sd.query_devices()

    if spec:
        if spec.isdigit():
            return int(spec)
        spec_lower = spec.lower()
        for idx, dev in enumerate(devices):
            if dev["max_output_channels"] > 0 and spec_lower in dev["name"].lower():
                return idx
        raise ConfigError(
            f"No output device matching '{spec}' found. Run list_audio_devices.py to see options."
        )

    for idx, dev in enumerate(devices):
        if dev["max_output_channels"] > 0 and "cable input" in dev["name"].lower():
            logger.info("Auto-selected audio device: [%d] %s", idx, dev["name"])
            return idx

    raise ConfigError(
        "AUDIO_OUTPUT_DEVICE is not set and no VB-Cable device was auto-detected.\n"
        "Run `python list_audio_devices.py`, find your VB-Cable input device, "
        "and set AUDIO_OUTPUT_DEVICE in .env."
    )


class AudioPlayer:
    """Plays WAV bytes to one output device, interruptibly.

    Playback writes to an explicit OutputStream in small blocks rather than
    using sd.play()/sd.wait(). That matters for the GUI's Stop button:
    sd.stop() called from another thread races with sd.wait()'s own
    stream.close(), which double-closes the same PortAudio pointer. Checking a
    threading.Event between blocks stops within ~50ms with no shared state.
    """

    BLOCK = 2048

    def __init__(self, device: int):
        self.device = device
        self._stop = threading.Event()

    def stop(self) -> None:
        """Interrupt any in-progress playback. Safe to call from any thread."""
        self._stop.set()

    def reset(self) -> None:
        self._stop.clear()

    def warmup(self) -> None:
        """Open the output device once so the first real message does not pay for it."""
        with sd.OutputStream(samplerate=24000, device=self.device, channels=1) as stream:
            stream.write(np.zeros((1200, 1), dtype="float32"))

    def play(self, wav_bytes: bytes) -> None:
        data, samplerate = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=True)
        channels = data.shape[1]
        with sd.OutputStream(samplerate=samplerate, device=self.device, channels=channels) as stream:
            for start in range(0, len(data), self.BLOCK):
                if self._stop.is_set():
                    return
                stream.write(data[start : start + self.BLOCK])

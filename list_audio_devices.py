"""Prints all audio devices known to sounddevice, so you can find your VB-Cable input.

Run: python list_audio_devices.py
Look for something like "CABLE Input (VB-Audio Virtual Cable)" and put its
name (or index) into AUDIO_OUTPUT_DEVICE in .env.
"""
import sounddevice as sd

for idx, dev in enumerate(sd.query_devices()):
    kinds = []
    if dev["max_output_channels"] > 0:
        kinds.append("output")
    if dev["max_input_channels"] > 0:
        kinds.append("input")
    print(f"[{idx}] {dev['name']}  ({'/'.join(kinds)}, {dev['default_samplerate']:.0f} Hz)")

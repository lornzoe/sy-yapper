"""Fix Voicebox's `[Errno 22] Invalid argument: ...\\snapshots\\...\\config.json`.

Voicebox's bundled downloader cannot follow the symlinks that huggingface_hub
puts in the model cache on Windows. The download dies at 0%, and generation
fails with the same error even though the files are on disk and readable.

This replaces those symlinks with hardlinks to the same blobs -- no extra disk
use, and no reparse points for Voicebox to trip over.

    python fix_hf_symlinks.py              # fix every cached model
    python fix_hf_symlinks.py chatterbox   # only models matching "chatterbox"

Afterwards, clear Voicebox's stuck task state or it will keep reporting the
model as not downloaded:

    curl -X POST http://127.0.0.1:17493/tasks/clear
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

CACHE = Path.home() / ".cache" / "huggingface" / "hub"


def fix_model(model_dir: Path) -> tuple[int, int, int]:
    hardlinked = copied = failed = 0
    for dirpath, _dirnames, filenames in os.walk(model_dir / "snapshots"):
        for name in filenames:
            path = Path(dirpath) / name
            if not path.is_symlink():
                continue
            try:
                target = Path(os.path.realpath(path))
                if not target.exists():
                    print(f"    ! broken link, skipping: {name}")
                    failed += 1
                    continue
                path.unlink()
                try:
                    os.link(target, path)   # same blob, no extra disk
                    hardlinked += 1
                except OSError:
                    shutil.copyfile(target, path)   # different volume, etc.
                    copied += 1
            except OSError as exc:
                print(f"    ! {name}: {exc}")
                failed += 1
    return hardlinked, copied, failed


def main() -> int:
    if not CACHE.exists():
        print(f"No HuggingFace cache at {CACHE} -- nothing to fix.")
        return 0

    pattern = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    models = [d for d in CACHE.glob("models--*") if pattern in d.name.lower()]
    if not models:
        print(f"No cached models{' matching ' + pattern if pattern else ''} in {CACHE}.")
        return 1

    total = 0
    for model in models:
        pretty = model.name.replace("models--", "").replace("--", "/")
        hardlinked, copied, failed = fix_model(model)
        total += hardlinked + copied
        if hardlinked or copied or failed:
            print(f"  {pretty}: {hardlinked} hardlinked, {copied} copied, {failed} failed")
        else:
            print(f"  {pretty}: already fine")

    if total:
        print(
            f"\nFixed {total} file(s). Now clear Voicebox's stuck task state:\n"
            "    curl -X POST http://127.0.0.1:17493/tasks/clear"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

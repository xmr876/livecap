"""List WASAPI loopback devices so we know system-audio capture is possible.

    .venv\\Scripts\\python.exe scripts\\check_loopback.py
"""
from __future__ import annotations


def main() -> int:
    try:
        import soundcard as sc
    except ImportError:
        print("soundcard is not installed:  .venv\\Scripts\\python.exe -m pip install soundcard")
        return 1

    speaker = sc.default_speaker()
    print("default speaker:", speaker.name)

    loopbacks = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]
    print(f"loopback devices: {len(loopbacks)}")
    for mic in loopbacks:
        print("   ", mic.name)
    if not loopbacks:
        print("no loopback device found - system audio capture will not work")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

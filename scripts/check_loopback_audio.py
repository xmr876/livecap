"""Record a few seconds of system audio and report the level.

This isolates capture from VAD/ASR: if this prints SILENT while something is
playing, the loopback device or the playback route is the problem.

    .venv\\Scripts\\python.exe scripts\\check_loopback_audio.py [seconds]
"""
from __future__ import annotations

import sys
import time

import numpy as np
import soundcard as sc


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    speaker = sc.default_speaker()
    print(f"default speaker: {speaker.name}")
    mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)

    peak = 0.0
    rms_best = 0.0
    frames = 0
    started = time.time()
    with mic.recorder(samplerate=48000, channels=2) as recorder:
        while time.time() - started < seconds:
            block = recorder.record(numframes=4800)
            if block.size == 0:
                continue
            frames += len(block)
            peak = max(peak, float(np.abs(block).max()))
            rms_best = max(rms_best, float(np.sqrt(np.mean(block ** 2))))

    captured = frames / 48000.0
    print(f"captured {captured:.1f}s of audio, peak {peak:.4f}, best rms {rms_best:.4f}")
    if peak < 0.005:
        print("SILENT - nothing reached the capture device")
        return 1
    print("AUDIBLE - loopback capture works")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

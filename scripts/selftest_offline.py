"""Offline self-test for the VAD segmenter and the translator plumbing.

Needs no network and no models: it feeds synthetic PCM through the segmenter and
checks that utterances are cut on pauses.

    .venv\\Scripts\\python.exe scripts\\selftest_offline.py
"""
from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecap.config import Config, parse_args            # noqa: E402
from livecap.segmenter import Segmenter                  # noqa: E402
from livecap.translate import NoopTranslator             # noqa: E402

SR = 16000


def tone(seconds: float, amp: float = 0.2, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def quiet(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def pcm_bytes(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def test_segmenter() -> None:
    cfg = Config()
    in_q: queue.Queue[bytes] = queue.Queue()
    out_q: queue.Queue = queue.Queue()
    segmenter = Segmenter(cfg, in_q, out_q)
    segmenter.start()

    stream = np.concatenate([
        quiet(0.5), tone(2.0), quiet(0.8),
        tone(1.5), quiet(0.6),
        tone(3.0), quiet(1.0),
    ])
    block = int(0.1 * SR)
    for i in range(0, len(stream), block):
        in_q.put(pcm_bytes(stream[i:i + block]))
    time.sleep(0.6)
    segmenter.stop()

    segments = []
    while True:
        try:
            segments.append(out_q.get_nowait())
        except queue.Empty:
            break

    durations = [len(s.pcm) / SR for s in segments]
    print(f"segmenter -> {len(segments)} segments: {[round(d, 2) for d in durations]}")
    assert len(segments) == 3, f"expected 3 utterances, got {len(segments)}"
    for expected, got in zip((2.0, 1.5, 3.0), durations):
        assert expected - 0.2 <= got <= expected + 0.6, f"{expected}s utterance came out as {got:.2f}s"
    assert segments[1].started_at >= segments[0].ended_at, "timestamps must be monotonic"
    print("segmenter OK")


def test_cli() -> None:
    cfg = parse_args(["--url", "https://example.com/live", "--translator", "none",
                      "--no-overlay", "--max-segment", "5"])
    assert cfg.source == "url" and cfg.max_segment == 5.0
    assert cfg.overlay is False and cfg.translator == "none"
    print("cli OK")


def test_translators() -> None:
    assert NoopTranslator().translate("こんにちは") == ""
    print("translator plumbing OK")


if __name__ == "__main__":
    test_cli()
    test_translators()
    test_segmenter()
    print("ALL OFFLINE TESTS PASSED")

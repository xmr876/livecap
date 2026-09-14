"""Energy-based voice activity detection -> speech segments.

Whisper is happiest with utterances of a few seconds, and cutting on natural
pauses is what keeps the subtitle latency low. A plain RMS threshold with a
pre-roll buffer and a hangover works well for streamed speech and has no extra
model dependency.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("livecap.segment")

_TAIL_FRACTION = 0.6  # keep a bit of the trailing silence: Whisper likes context
_TARGET_RMS = 0.06    # gentle AGC target for each segment
_MAX_GAIN = 5.0       # never amplify more than this (avoids boosting pure noise)
_ABS_FLOOR = 0.0025   # below this a frame is silence no matter what


@dataclass
class Segment:
    pcm: np.ndarray          # float32 mono, 16 kHz
    started_at: float        # wall clock when the first speech frame arrived
    ended_at: float          # wall clock when the segment was closed


class Segmenter(threading.Thread):
    def __init__(self, cfg, in_queue: "queue.Queue[bytes]", out_queue: "queue.Queue[Segment]"):
        super().__init__(name="segmenter", daemon=True)
        self.cfg = cfg
        self.in_q = in_queue
        self.out_q = out_queue
        self._stop = threading.Event()

        self.frame_len = int(cfg.sample_rate * cfg.frame_ms / 1000)
        self.frame_dur = cfg.frame_ms / 1000.0
        self.preroll_frames = max(1, int(round(cfg.preroll / self.frame_dur)))
        self.min_silence_frames = max(1, int(round(cfg.min_silence / self.frame_dur)))
        self.min_speech_frames = max(1, int(round(cfg.min_speech / self.frame_dur)))
        self.max_frames = max(1, int(round(cfg.max_segment / self.frame_dur)))

    # ------------------------------------------------------------------ helpers
    def _close(self, frames: list[np.ndarray], speech_frames: int,
               started_at: float, ended_at: float) -> None:
        if speech_frames < self.min_speech_frames or not frames:
            return
        keep = min(len(frames), speech_frames + int(self.min_silence_frames * _TAIL_FRACTION))
        pcm = np.concatenate(frames[:keep])
        pcm = self._normalize(pcm)
        seg = Segment(pcm=pcm, started_at=started_at, ended_at=ended_at)
        try:
            self.out_q.put_nowait(seg)
        except queue.Full:
            try:
                self.out_q.get_nowait()
                self.out_q.put_nowait(seg)
            except queue.Empty:
                pass

    @staticmethod
    def _normalize(pcm: np.ndarray) -> np.ndarray:
        """Gentle AGC: Whisper is much more accurate on a consistent level.

        Playback volume varies a lot (quiet listening, quiet streamers), so pull
        each segment towards a comfortable RMS instead of trusting the source.
        """
        if pcm.size == 0:
            return pcm
        rms = float(np.sqrt(np.mean(pcm * pcm)))
        if rms < 1e-5:
            return pcm
        gain = min(_MAX_GAIN, _TARGET_RMS / rms)
        if abs(gain - 1.0) < 0.1:
            return pcm
        return np.clip(pcm * gain, -1.0, 1.0).astype(np.float32)

    # ------------------------------------------------------------------ thread
    def run(self) -> None:
        cfg = self.cfg
        leftover = np.empty(0, dtype=np.float32)
        preroll: deque[np.ndarray] = deque(maxlen=self.preroll_frames)
        frames: list[np.ndarray] = []
        speech_frames = 0
        silence_run = 0
        in_speech = False
        started_at = 0.0
        last_frame_at = 0.0
        noise_floor: float | None = None
        floor_alpha = 0.05

        while not self._stop.is_set():
            try:
                chunk = self.in_q.get(timeout=0.25)
            except queue.Empty:
                continue
            if not chunk:
                continue

            block = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
            if leftover.size:
                block = np.concatenate((leftover, block))
            usable = (len(block) // self.frame_len) * self.frame_len
            leftover = block[usable:].copy()

            for off in range(0, usable, self.frame_len):
                frame = block[off:off + self.frame_len]
                now = time.time()
                last_frame_at = now
                rms = float(np.sqrt(np.mean(frame * frame)))

                if not in_speech:
                    # track the noise floor only while nobody is talking, then
                    # require a clear margin above it: quiet playback still works
                    noise_floor = rms if noise_floor is None else (
                        (1 - floor_alpha) * noise_floor + floor_alpha * rms)
                enter = max(cfg.vad_threshold, (noise_floor or 0.0) * 2.5)
                # hysteresis: once talking, quieter syllables still count as
                # speech, otherwise quiet sentence endings get chopped off
                leave = max(_ABS_FLOOR, enter * 0.55)
                threshold = leave if in_speech else enter
                loud = rms >= threshold and rms >= _ABS_FLOOR

                if not in_speech:
                    preroll.append(frame)
                    if loud:
                        in_speech = True
                        started_at = now
                        frames = list(preroll)
                        speech_frames = 1
                        silence_run = 0
                    continue

                frames.append(frame)
                if loud:
                    speech_frames += 1
                    silence_run = 0
                else:
                    silence_run += 1

                if silence_run >= self.min_silence_frames or len(frames) >= self.max_frames:
                    self._close(frames, speech_frames, started_at, last_frame_at)
                    in_speech = False
                    speech_frames = 0
                    silence_run = 0
                    frames = []
                    preroll.clear()

        if in_speech:  # flush whatever is left on shutdown
            self._close(frames, speech_frames, started_at, last_frame_at)

    def stop(self) -> None:
        self._stop.set()

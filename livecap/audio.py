"""Audio acquisition: turn a stream / file / system loopback into 16 kHz mono PCM.

Strategy
--------
* ``url``      : ``python -m yt_dlp -o - <url>`` piped straight into ffmpeg.
                 yt-dlp deals with the site specifics (HLS, cookies, headers),
                 ffmpeg decodes and resamples, we read raw PCM from its stdout.
* ``file``     : ffmpeg reads the file with ``-re`` so it plays at realtime speed.
* ``loopback`` : WASAPI loopback of the default speaker via the optional
                 ``soundcard`` package (no virtual cable needed).
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time

log = logging.getLogger("livecap.audio")

READ_BLOCK = 4096  # bytes; must stay a multiple of 2 (s16le)

# This machine may have a system-wide proxy configured (urllib picks it up from
# the registry). Local addresses must never be sent through it.
_NO_PROXY = "127.0.0.1,localhost,::1"


class AudioSource:
    """Pushes raw s16le mono PCM into ``out_queue`` until stopped."""

    def __init__(self, cfg, out_queue: "queue.Queue[bytes]"):
        self.cfg = cfg
        self.q = out_queue
        self._stop = threading.Event()
        self._procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()
        self._total_bytes = 0  # across reconnects, so --max-duration is a global cap

    # ------------------------------------------------------------------ utils
    def _register(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.append(proc)

    def _ffmpeg_pcm_args(self) -> list[str]:
        return [
            "-vn", "-sn", "-dn",
            "-f", "s16le", "-acodec", "pcm_s16le",
            "-ac", "1", "-ar", str(self.cfg.sample_rate),
            "-flush_packets", "1",
            "pipe:1",
        ]

    def _spawn_ffmpeg(self, input_args: list[str], stdin=None) -> subprocess.Popen:
        cmd = [self.cfg.ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
               *input_args, *self._ffmpeg_pcm_args()]
        log.info("ffmpeg: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, env=self._child_env(),
        )
        self._register(proc)
        threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True).start()
        return proc

    @staticmethod
    def _child_env() -> dict[str, str]:
        env = dict(os.environ)
        env["NO_PROXY"] = _NO_PROXY
        env["no_proxy"] = _NO_PROXY
        return env

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen) -> None:
        try:
            assert proc.stderr is not None
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    log.debug("ffmpeg: %s", line)
        except Exception:  # noqa: BLE001 - stderr draining must never crash the reader
            pass

    def _emit(self, data: bytes) -> None:
        limit = int(self.cfg.max_duration * self.cfg.sample_rate * 2) if self.cfg.max_duration > 0 else 0
        if limit and self._total_bytes >= limit:
            if not self._stop.is_set():
                log.info("reached --max-duration %.1fs, stopping", self.cfg.max_duration)
                self._stop.set()
            return
        self._total_bytes += len(data)
        try:
            self.q.put(data, timeout=1.0)
        except queue.Full:
            try:  # consumer is way behind: keep the freshest audio
                self.q.get_nowait()
                self.q.put_nowait(data)
            except queue.Empty:
                pass

    def _read_stream(self, stream) -> None:
        while not self._stop.is_set():
            data = stream.read(READ_BLOCK)
            if not data:
                break
            self._emit(data)

    # ------------------------------------------------------------------ sources
    def _ytdlp_cmd(self) -> list[str]:
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--quiet", "--no-warnings", "--no-progress",
            "--no-playlist", "--no-part", "--no-cache-dir",
            "-f", "bestaudio/best",
            "-o", "-",
        ]
        # HLS / DASH downloads need ffmpeg; yt-dlp does not inherit our path.
        ffmpeg_dir = os.path.dirname(self.cfg.ffmpeg) if self.cfg.ffmpeg else ""
        if ffmpeg_dir and os.path.isdir(ffmpeg_dir):
            cmd += ["--ffmpeg-location", ffmpeg_dir]
        if self.cfg.cookies:
            cmd += ["--cookies", self.cfg.cookies]
        if self.cfg.cookies_from_browser:
            cmd += ["--cookies-from-browser", self.cfg.cookies_from_browser]
        if self.cfg.ytdlp_args:
            cmd += self.cfg.ytdlp_args.split()
        cmd.append(self.cfg.url)
        return cmd

    def _run_url(self) -> None:
        while not self._stop.is_set():
            ytdlp_cmd = self._ytdlp_cmd()
            log.info("yt-dlp: %s <%s>", " ".join(ytdlp_cmd[2:-1]), self.cfg.url)
            ytdlp = subprocess.Popen(
                ytdlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                env=self._child_env(),
            )
            self._register(ytdlp)
            threading.Thread(target=self._drain_stderr, args=(ytdlp,), daemon=True).start()
            ffmpeg = None
            try:
                ffmpeg = self._spawn_ffmpeg(["-i", "pipe:0"], stdin=ytdlp.stdout)
                if ytdlp.stdout is not None:
                    ytdlp.stdout.close()  # ffmpeg owns the read end now
                self._read_stream(ffmpeg.stdout)
            finally:
                _terminate(ytdlp)
                _terminate(ffmpeg)
            if self._stop.is_set():
                return
            log.warning("stream ended, reconnecting in %.1fs", self.cfg.restart_delay)
            self._stop.wait(self.cfg.restart_delay)

    def _run_file(self) -> None:
        ffmpeg = self._spawn_ffmpeg(["-re", "-i", self.cfg.file])
        try:
            self._read_stream(ffmpeg.stdout)
        finally:
            _terminate(ffmpeg)

    def _run_loopback(self) -> None:
        try:
            import numpy as np
            import soundcard as sc
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SystemExit(
                "loopback capture needs extra packages: "
                ".venv\\Scripts\\python.exe -m pip install soundcard numpy"
            ) from exc

        speaker = sc.default_speaker()
        if self.cfg.loopback_device:
            wanted = self.cfg.loopback_device.lower()
            matches = [m for m in sc.all_microphones(include_loopback=True)
                       if m.isloopback and wanted in m.name.lower()]
            if not matches:
                raise SystemExit(f"no loopback device matching {self.cfg.loopback_device!r}; "
                                 f"run scripts\\check_loopback.py to list them")
            # loopback names mirror the output device name
            speaker = sc.get_microphone(id=str(matches[0].name), include_loopback=True)
            log.info("loopback capture (matched) from: %s", matches[0].name)
            mic = speaker
        else:
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            log.info("loopback capture from: %s", speaker.name)
        ratio = 3  # 48 kHz -> 16 kHz
        with mic.recorder(samplerate=48000, channels=2) as rec:
            while not self._stop.is_set():
                block = rec.record(numframes=4800)          # 100 ms
                mono = block.mean(axis=1)                    # stereo -> mono
                trimmed = mono[: len(mono) // ratio * ratio].reshape(-1, ratio).mean(axis=1)
                self._emit((trimmed * 32767).astype("<i2").tobytes())

    # ------------------------------------------------------------------ driver
    def run(self) -> None:
        try:
            if self.cfg.source == "url":
                self._run_url()
            elif self.cfg.source == "file":
                self._run_file()
            else:
                self._run_loopback()
        except Exception as exc:  # noqa: BLE001 - surface failures through the queue
            log.error("audio source failed: %s", exc)
            raise

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            _terminate(proc)


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass

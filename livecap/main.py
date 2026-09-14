"""livecap pipeline: audio -> VAD segmentation -> ASR -> translation -> overlay."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime

from .asr import WhisperASR
from .audio import AudioSource
from .config import Config, parse_args
from .segmenter import Segment, Segmenter
from .translate import build_translator

log = logging.getLogger("livecap")


class _Emitter:
    """Mimics a Qt signal so console mode and overlay mode share one sink API."""

    def __init__(self, fn):
        self._fn = fn

    def emit(self, text: str):  # noqa: D401 - Qt-compatible name
        self._fn(text)


class ConsoleSink:
    def __init__(self):
        self.ja = _Emitter(lambda t: print(f"\n[日] {t}", flush=True))
        self.zh = _Emitter(lambda t: print(f"[中] {t}", flush=True))
        self.status = _Emitter(lambda t: print(f"[·] {t}", flush=True))
        self.clear = _Emitter(lambda t: None)


class Pipeline:
    def __init__(self, cfg: Config, sink):
        self.cfg = cfg
        self.sink = sink
        self.audio_q: queue.Queue[bytes] = queue.Queue(maxsize=256)
        self.seg_q: queue.Queue[Segment] = queue.Queue(maxsize=16)
        self.tr_q: queue.Queue[tuple[str, Segment, float]] = queue.Queue(maxsize=16)

        self._stop = threading.Event()
        self._ready = threading.Event()
        self.audio = AudioSource(cfg, self.audio_q)
        self.segmenter = Segmenter(cfg, self.audio_q, self.seg_q)
        self.asr: WhisperASR | None = None
        self.translator = None
        self._log_fh = None

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        return self._ready.is_set() and not self._stop.is_set()

    def start_async(self) -> threading.Thread:
        thread = threading.Thread(target=self._bootstrap, name="bootstrap", daemon=True)
        thread.start()
        return thread

    # -------------------------------------------------------------- bootstrap
    def _bootstrap(self) -> None:
        try:
            self.sink.status.emit("加载识别模型（首次较慢）…")
            self.asr = WhisperASR(self.cfg)
            self.sink.status.emit("加载翻译模型…")
            self.translator = build_translator(self.cfg)
            self.sink.status.emit(f"翻译后端：{getattr(self.translator, 'name', '?')}")
            if self.cfg.log_path:
                self._log_fh = open(self.cfg.log_path, "a", encoding="utf-8")
            self.sink.status.emit(f"就绪 · {self.cfg.source} · {self.cfg.device}")
            self._ready.set()

            self.segmenter.start()
            threading.Thread(target=self._audio_loop, name="audio", daemon=True).start()
            threading.Thread(target=self._asr_loop, name="asr", daemon=True).start()
            threading.Thread(target=self._mt_loop, name="mt", daemon=True).start()
        except Exception as exc:  # noqa: BLE001 - report instead of dying silently
            log.exception("startup failed")
            self.sink.status.emit(f"启动失败：{exc}")
            self._stop.set()

    # ------------------------------------------------------------------ loops
    def _audio_loop(self) -> None:
        try:
            self.audio.run()
        except Exception as exc:  # noqa: BLE001
            self.sink.status.emit(f"音频中断：{exc}")
        if self._stop.is_set():
            return
        self.sink.status.emit("音频结束，正在输出最后几句…")
        try:  # flush whatever the segmenter is still holding
            self.segmenter.stop()
        except Exception:  # noqa: BLE001
            pass
        self._drain()
        self._stop.set()

    def _drain(self, timeout: float = 20.0) -> None:
        """Let the ASR / translation workers finish the segments still in flight."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.seg_q.empty() or not self.tr_q.empty():
                time.sleep(0.2)
                continue
            time.sleep(0.5)          # queues look empty; make sure nothing new arrives
            if self.seg_q.empty() and self.tr_q.empty():
                return

    def _asr_loop(self) -> None:
        while not self._stop.is_set():
            try:
                seg = self.seg_q.get(timeout=0.25)
            except queue.Empty:
                continue
            lag = time.time() - seg.ended_at
            if lag > self.cfg.max_lag:
                # the recognizer is behind (dense speech / slow GPU): showing a
                # stale line would push the subtitles further and further behind
                log.warning("dropping a %.1fs segment, %.1fs behind", seg.ended_at - seg.started_at, lag)
                continue
            assert self.asr is not None
            try:
                text, asr_s = self.asr.transcribe(seg.pcm)
            except Exception as exc:  # noqa: BLE001
                log.warning("ASR failed: %s", exc)
                continue
            if not text:
                continue
            self.sink.ja.emit(text)
            self._push(self.tr_q, (text, seg, asr_s))
            self.sink.status.emit(
                f"识别 {asr_s * 1000:.0f}ms · 句长 {len(seg.pcm) / self.cfg.sample_rate:.1f}s"
            )

    def _mt_loop(self) -> None:
        while not self._stop.is_set():
            try:
                text, seg, asr_s = self.tr_q.get(timeout=0.25)
            except queue.Empty:
                continue
            if time.time() - seg.ended_at > self.cfg.max_lag:
                log.warning("dropping a stale translation")
                continue
            started = time.perf_counter()
            zh = ""
            try:
                if self.translator is not None:
                    zh = self.translator.translate(text)
            except Exception as exc:  # noqa: BLE001
                log.warning("translation failed: %s", exc)
            mt_s = time.perf_counter() - started
            total = time.time() - seg.ended_at
            if zh:
                self.sink.zh.emit(zh)
            self.sink.status.emit(
                f"识别 {asr_s * 1000:.0f}ms · 翻译 {mt_s * 1000:.0f}ms · 端到端 {total:.1f}s"
            )
            self._write_log(text, zh, asr_s, mt_s, total)

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _push(q: queue.Queue, item) -> None:
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(item)
            except queue.Empty:
                pass

    def _write_log(self, ja: str, zh: str, asr_s: float, mt_s: float, total: float) -> None:
        if self._log_fh is None:
            return
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "ja": ja, "zh": zh,
            "asr_ms": round(asr_s * 1000), "mt_ms": round(mt_s * 1000),
            "latency_s": round(total, 2),
        }
        try:
            self._log_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._log_fh.flush()
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self.audio.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.segmenter.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:  # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(cfg, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if cfg.check_models or cfg.download_models:
        from .models import describe, download_all, missing

        if cfg.check_models:
            print(describe())
            return 0 if not missing() else 3
        print("开始下载模型（首次约 4GB，断线可重跑本命令续传）…", flush=True)
        try:
            download_all(note=lambda message: print(message, flush=True))
        except Exception as exc:  # noqa: BLE001 - report instead of a raw traceback
            print(f"下载失败：{type(exc).__name__}: {exc}", flush=True)
            return 1
        print(describe())
        return 0

    if not cfg.overlay:
        pipeline = Pipeline(cfg, ConsoleSink())
        pipeline.start_async()
        try:
            while not pipeline._stop.is_set():  # noqa: SLF001 - simple wait loop
                time.sleep(0.3)
        except KeyboardInterrupt:
            print("\n已停止")
        finally:
            pipeline.stop()
        return 0

    from .overlay import run_overlay

    holder: dict[str, Pipeline] = {}

    def worker_start(bridge):
        pipeline = Pipeline(cfg, bridge)
        holder["pipeline"] = pipeline
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(pipeline.stop)
        except Exception:  # noqa: BLE001
            pass
        pipeline.start_async()

        def watch():
            pipeline._stop.wait()  # noqa: SLF001 - close the overlay when audio ends
            time.sleep(1.0)
            try:
                bridge.finished.emit()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=watch, name="watcher", daemon=True).start()

    try:
        return run_overlay(cfg, worker_start)
    finally:
        if "pipeline" in holder:
            holder["pipeline"].stop()


if __name__ == "__main__":
    raise SystemExit(main())

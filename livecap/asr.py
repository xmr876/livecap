"""Japanese speech recognition via faster-whisper (CTranslate2)."""
from __future__ import annotations

import logging
import os
import re
import site
import sys
import time

import numpy as np

log = logging.getLogger("livecap.asr")

# os.add_dll_directory() handles must outlive the call, otherwise the directory
# is dropped again as soon as the object is collected.
_DLL_HANDLES: list = []


def prepare_cuda_dlls() -> list[str]:
    """Expose pip-installed cuDNN / cuBLAS DLLs to CTranslate2 on Windows.

    CTranslate2's CUDA backend needs cuDNN 9 and cuBLAS, but a plain CUDA
    toolkit install does not ship cuDNN. The ``nvidia-*-cu12`` wheels do, they
    just are not on the DLL search path by default.
    """
    added: list[str] = []
    if os.name != "nt":
        return added
    roots: list[str] = []
    try:
        roots.extend(site.getsitepackages())
    except Exception:  # noqa: BLE001
        pass
    try:
        roots.append(site.getusersitepackages())
    except Exception:  # noqa: BLE001
        pass
    roots.append(os.path.join(sys.prefix, "Lib", "site-packages"))
    # frozen builds: the DLLs sit next to the exe / in the bundle
    from .paths import app_root, bundled_root

    roots.extend([str(app_root()), str(bundled_root())])

    for root in dict.fromkeys(roots):
        nvidia = os.path.join(root, "nvidia")
        if not os.path.isdir(nvidia):
            continue
        for name in os.listdir(nvidia):
            path = os.path.join(nvidia, name, "bin")
            if not os.path.isdir(path):
                continue
            try:
                _DLL_HANDLES.append(os.add_dll_directory(path))
                added.append(path)
            except Exception:  # noqa: BLE001
                pass
    if added:
        os.environ["PATH"] = os.pathsep.join(added + [os.environ.get("PATH", "")])
        log.info("CUDA dll directories: %s", ", ".join(added))
    return added

# Whisper likes to hallucinate these on silence / background music.
_JUNK = re.compile(
    r"^[\s。、,.!?！？]*$|"
    r"^(ご視聴ありがとうござ(いま|いました)|チャンネル登録(を)?(お願い|よろしく)|"
    r"おやすみなさい|Thank you for watching\.?|Thanks for watching\.?|"
    r"字幕|(Subtitles?|Captions?) by.*|Amara\.org.*)$",
    re.IGNORECASE,
)


class WhisperASR:
    """Thin wrapper that keeps a rolling prompt so names stay consistent."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.model = self._load(cfg.device, cfg.compute_type)
        self.history: list[str] = []

    def _load(self, device: str, compute_type: str):
        from faster_whisper import WhisperModel

        from .paths import resolve_model

        model_path = resolve_model(self.cfg.asr_model)
        if device == "cuda":
            prepare_cuda_dlls()
        try:
            log.info("loading ASR model %s on %s/%s", model_path, device, compute_type)
            return WhisperModel(
                model_path, device=device, compute_type=compute_type,
                cpu_threads=self.cfg.cpu_threads, num_workers=1,
            )
        except Exception as exc:  # noqa: BLE001 - retry on CPU rather than dying
            if device == "cpu":
                raise
            log.warning("CUDA load failed (%s); retrying on CPU int8", exc)
            self.cfg.device, self.cfg.compute_type = "cpu", "int8"
            return WhisperModel(
                model_path, device="cpu", compute_type="int8",
                cpu_threads=self.cfg.cpu_threads, num_workers=1,
            )

    def _prompt(self) -> str | None:
        if not self.history:
            return None
        return "".join(self.history[-self.cfg.context_lines:])[-200:]

    def transcribe(self, pcm: np.ndarray) -> tuple[str, float]:
        """Return ``(japanese_text, seconds_spent)``."""
        started = time.perf_counter()
        segments, _info = self.model.transcribe(
            pcm,
            language=self.cfg.language,
            beam_size=self.cfg.beam_size,
            best_of=1,
            temperature=0.0,
            vad_filter=False,                  # we already cut on pauses
            condition_on_previous_text=False,  # avoid runaway loops on live audio
            initial_prompt=self._prompt(),
            without_timestamps=True,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
        )
        text = "".join(seg.text for seg in segments).strip()
        elapsed = time.perf_counter() - started

        if not text or _JUNK.match(text):
            return "", elapsed
        self.history.append(text)
        return text, elapsed

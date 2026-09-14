"""Downloading the models an installation needs.

The installer deliberately does not carry the model weights (5 GB+); it carries
the app and calls this module to fetch them:

* ASR  : ``Systran/faster-whisper-large-v3`` (CTranslate2 Whisper, ~3 GB)
* MT   : ``JustFrederik/nllb-200-distilled-600M-ct2-float16`` - a public
         CTranslate2 conversion of facebook/nllb-200-distilled-600M (~1.2 GB),
         same layout our own conversion script produces.

Both land under ``models/`` next to the exe, so an uninstall removes them too.
HuggingFace is blocked in some networks, so every download retries through
hf-mirror.com.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .paths import models_dir, resolve_model

log = logging.getLogger("livecap.models")

HF_MIRROR = "https://hf-mirror.com"

ASR_REPO = "Systran/faster-whisper-large-v3"
ASR_DIRNAME = "faster-whisper-large-v3"
MT_REPO = "JustFrederik/nllb-200-distilled-600M-ct2-float16"
MT_DIRNAME = "nllb-200-600M-ct2"


def asr_path() -> Path:
    return models_dir() / ASR_DIRNAME


def mt_path() -> Path:
    return models_dir() / MT_DIRNAME


def _has_model(directory: Path) -> bool:
    return (directory / "model.bin").exists()


def status() -> dict[str, bool]:
    """Which of the two models are already on disk."""
    return {
        "asr": _has_model(asr_path()) or (lambda p: p.is_dir() and _has_model(p))(
            Path(resolve_model(ASR_REPO))),
        "mt": _has_model(mt_path()) and (mt_path() / "tokenizer.json").exists(),
    }


def missing() -> list[str]:
    state = status()
    return [name for name, ready in state.items() if not ready]


def folder_size(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except Exception:  # noqa: BLE001
        return 0


def _download(repo: str, target: Path, note) -> None:
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from huggingface_hub import snapshot_download

    target.mkdir(parents=True, exist_ok=True)
    note(f"下载 {repo}")
    snapshot_download(repo_id=repo, local_dir=str(target))


def _download_resilient(repo: str, target: Path, note) -> None:
    try:
        _download(repo, target, note)
    except Exception as exc:  # noqa: BLE001 - mirror is the usual second chance
        note(f"直连失败：{exc}")
        note(f"改用镜像 {HF_MIRROR} 重试")
        os.environ["HF_ENDPOINT"] = HF_MIRROR
        _download(repo, target, note)


def download(kind: str, note=print) -> Path:
    """Fetch one model (``"asr"`` or ``"mt"``) and return where it landed."""
    if kind == "asr":
        target = asr_path()
        if _has_model(target):
            note("语音识别模型已存在，跳过")
            return target
        _download_resilient(ASR_REPO, target, note)
        return target
    if kind == "mt":
        target = mt_path()
        if _has_model(target) and (target / "tokenizer.json").exists():
            note("翻译模型已存在，跳过")
            return target
        _download_resilient(MT_REPO, target, note)
        return target
    raise ValueError(f"unknown model kind: {kind}")


def download_all(note=print) -> dict[str, str]:
    """Fetch whatever is missing; returns the resolved paths."""
    result: dict[str, str] = {}
    for kind in ("asr", "mt"):
        result[kind] = str(download(kind, note))
    note("全部模型就绪")
    return result


def describe() -> str:
    """One-line summary for logs / the GUI."""
    state = status()
    lines = []
    for kind, label in (("asr", "语音识别"), ("mt", "中文翻译")):
        path = asr_path() if kind == "asr" else mt_path()
        size = folder_size(path) / (1024 ** 3)
        lines.append(f"{label}模型：{'已就绪' if state[kind] else '缺失'}"
                     f"{f'（{size:.2f} GB）' if size > 0.01 else ''}")
    return "\n".join(lines)

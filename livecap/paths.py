"""Locating the app's own files and its models, frozen or not.

The packaged build must not depend on PyTorch, so the translation model ships as
a CTranslate2 directory under ``models/``. Whisper models are looked up in the
local HuggingFace cache *offline*, so a frozen app never blocks on the network.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("livecap.paths")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Folder holding the app's own files: the exe folder when frozen."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_root() -> Path:
    """Where PyInstaller put bundled data (same as app_root for onedir builds)."""
    return Path(getattr(sys, "_MEIPASS", app_root()))


def models_dir() -> Path:
    """Folder with livecap's own converted models (models/ next to the exe)."""
    override = os.environ.get("LIVECAP_MODELS")
    if override:
        return Path(override)
    for candidate in (app_root() / "models", bundled_root() / "models"):
        if candidate.is_dir():
            return candidate
    return app_root() / "models"


def hf_cache_dir() -> Path:
    override = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if override:
        return Path(override)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def cached_snapshot(ref: str) -> str | None:
    """Find a HuggingFace repo in the local cache without any network library.

    Deliberately avoids huggingface_hub: a frozen build should not need TLS just
    to locate a model that is already on disk.
    """
    folder = hf_cache_dir() / ("models--" + ref.replace("/", "--"))
    snapshots = folder / "snapshots"
    if not snapshots.is_dir():
        return None

    main = folder / "refs" / "main"
    if main.is_file():
        try:
            target = snapshots / main.read_text(encoding="utf-8").strip()
            if target.is_dir():
                return str(target)
        except Exception:  # noqa: BLE001
            pass

    candidates = [p for p in snapshots.iterdir() if p.is_dir()]
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(newest)


def resolve_model(ref: str) -> str:
    """Turn a model reference into a local path when possible.

    Accepts an existing directory, a folder under ``models/``, or a repo id that
    is already in the HuggingFace cache. Only as a last resort does it hand the
    reference back so the caller can download it.
    """
    if not ref:
        return ref
    path = Path(ref)
    if path.exists():
        return str(path)

    candidate = models_dir() / ref
    if candidate.exists():
        return str(candidate)

    # models/<basename> - how the installer's downloader lays them out
    short = models_dir() / ref.split("/")[-1]
    if short.exists():
        return str(short)

    cached = cached_snapshot(ref)
    if cached:
        log.info("model %s -> %s", ref, cached)
        return cached

    try:  # needs network libs; optional by design
        from huggingface_hub import snapshot_download

        resolved = snapshot_download(ref, local_files_only=True)
        log.info("model %s -> %s", ref, resolved)
        return resolved
    except Exception:  # noqa: BLE001 - not cached; let the caller try to download
        log.warning("model %s is not in the local cache", ref)
        return ref


def find_ffmpeg() -> str:
    """Bundled ffmpeg first, then an explicit override, then PATH."""
    override = os.environ.get("FFMPEG_BINARY")
    if override and Path(override).exists():
        return override
    for base in (app_root(), bundled_root()):
        for relative in ("tools/ffmpeg/bin/ffmpeg.exe", "ffmpeg/bin/ffmpeg.exe", "ffmpeg.exe"):
            candidate = base / relative
            if candidate.exists():
                return str(candidate)
    from shutil import which

    return which("ffmpeg") or "ffmpeg"

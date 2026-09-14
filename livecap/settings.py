"""Tiny JSON settings store.

Lives next to the exe (``settings.json``) so the packaged app is self contained;
falls back to ``%APPDATA%\\livecap`` when that folder is not writable.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .paths import app_root

log = logging.getLogger("livecap.settings")

FILENAME = "settings.json"

DEFAULTS: dict = {
    "llm_provider": "DeepSeek · deepseek-flash（推荐）",
    "llm_base_url": "https://api.deepseek.com",
    "llm_model": "deepseek-flash",
    "llm_thinking": "disabled",
    "llm_api_key": "",
    "asr_model": "Systran/faster-whisper-large-v3",
    "translator": "ct2",
    "loopback_device": "",
    "font_zh": 27,
    # remembered subtitle-bar position (None = default bottom centre)
    "bar_x": None,
    "bar_y": None,
}


def settings_path() -> Path:
    local = app_root() / FILENAME
    try:
        local.parent.mkdir(parents=True, exist_ok=True)
        if not local.exists():
            local.write_text("{}", encoding="utf-8")
        return local
    except Exception:  # noqa: BLE001 - e.g. read-only program folder
        fallback = Path(os.environ.get("APPDATA") or Path.home()) / "livecap"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback / FILENAME


def _read_text(path: Path) -> str:
    """Read the file whatever BOM/encoding an editor threw at it.

    Notepad and PowerShell's ``Set-Content -Encoding UTF8`` add a UTF-8 BOM,
    which plain ``encoding="utf-8"`` refuses to decode - that used to make the
    app fall back to defaults and then overwrite the user's settings.
    """
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")      # strips a UTF-8 BOM if present
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


def load() -> dict:
    data = dict(DEFAULTS)
    path = settings_path()
    try:
        stored = json.loads(_read_text(path) or "{}")
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except Exception as exc:  # noqa: BLE001
        # keep the unreadable file around instead of silently losing the API key
        log.error("could not read %s (%s); keeping a .bak copy", path, exc)
        try:
            path.replace(path.with_name(path.name + ".bak"))
        except Exception:  # noqa: BLE001
            pass

    # a settings file written before the model rename would keep failing with
    # "invalid model": map deepseek-chat / deepseek-reasoner onto deepseek-flash
    from .translate import normalize_llm_model

    data["llm_model"] = normalize_llm_model(str(data.get("llm_model", "")))
    if not str(data.get("llm_base_url", "")).strip():
        data["llm_base_url"] = DEFAULTS["llm_base_url"]
    return data


def save(data: dict) -> None:
    try:
        known = {k: data.get(k, v) for k, v in DEFAULTS.items()}
        settings_path().write_text(json.dumps(known, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not save settings: %s", exc)

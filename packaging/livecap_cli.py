"""Frozen console entry point (same engine, for URLs/files/advanced options)."""
from __future__ import annotations

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()   # keeps PyInstaller from re-spawning the app
    from livecap.main import main

    raise SystemExit(main(sys.argv[1:]))

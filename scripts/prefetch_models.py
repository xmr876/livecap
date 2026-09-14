"""Prefetch the models livecap needs into the local HuggingFace cache.

Usage:  python scripts/prefetch_models.py [repo ...]
"""
from __future__ import annotations

import os
import sys

DEFAULT_REPOS = [
    "Systran/faster-whisper-large-v3",              # ASR, best Japanese accuracy
    "deepdml/faster-whisper-large-v3-turbo-ct2",    # ASR, ~4x faster, lower latency
    "facebook/nllb-200-distilled-600M",             # local ja -> zh translation
    "Helsinki-NLP/opus-mt-ja-en",                   # optional marian pivot, stage 1
    "Helsinki-NLP/opus-mt-en-zh",                   # optional marian pivot, stage 2
]


def main(argv: list[str]) -> int:
    # must be set before huggingface_hub reads its constants
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    from huggingface_hub import snapshot_download

    repos = argv[1:] or DEFAULT_REPOS
    failures = 0
    for repo in repos:
        try:
            path = snapshot_download(repo, max_workers=4)
            print(f"OK   {repo} -> {path}", flush=True)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            failures += 1
            print(f"FAIL {repo}: {type(exc).__name__}: {exc}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

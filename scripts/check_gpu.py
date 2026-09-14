"""Check that CTranslate2 can actually use the GPU on this machine.

    .venv\\Scripts\\python.exe scripts\\check_gpu.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecap.asr import prepare_cuda_dlls  # noqa: E402


def main() -> int:
    added = prepare_cuda_dlls()
    print("dll dirs added:", len(added))
    for path in added:
        print("   ", path)

    import ctranslate2

    print("ctranslate2", ctranslate2.__version__)
    try:
        count = ctranslate2.get_cuda_device_count()
        print("cuda devices:", count)
    except Exception as exc:  # noqa: BLE001
        print("cuda devices: FAILED -", type(exc).__name__, exc)
        return 1
    if count == 0:
        print("no CUDA device visible to CTranslate2")
        return 1
    for device in ("cuda", "cpu"):
        try:
            print(f"{device} compute types:", sorted(ctranslate2.get_supported_compute_types(device)))
        except Exception as exc:  # noqa: BLE001
            print(f"{device} compute types: FAILED -", type(exc).__name__, exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

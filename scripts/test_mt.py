"""Smoke-test the CTranslate2 NLLB translator (GPU and CPU).

    .venv\\Scripts\\python.exe scripts\\test_mt.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecap.asr import prepare_cuda_dlls            # noqa: E402
from livecap.translate import CT2NLLBTranslator      # noqa: E402

SENTENCES = [
    "みなさん、こんばんは。",
    "今日も配信を見に来てくれてありがとうございます。",
    "天気予報によると、明日の午後は東京でも雨が降るそうです。",
    "このニュースについて、あなたはどう思いますか。",
    "総務省が発表したデータによると、去年の訪日外国人は二千五百万人を超えました。",
    "ちょっと待って、いま音声は聞こえていますか。",
    "とても勉強になりました。",
    "今日の配信はここまでにします。おやすみなさい。",
]


def run(model_dir: str, device: str, compute_type: str | None = None) -> None:
    print(f"\n=== {device} / {compute_type or 'auto'} ===")
    started = time.perf_counter()
    translator = CT2NLLBTranslator(model_dir, device=device, compute_type=compute_type)
    print(f"load: {time.perf_counter() - started:.2f}s")
    total = 0.0
    for sentence in SENTENCES:
        t0 = time.perf_counter()
        out = translator.translate(sentence)
        dt = time.perf_counter() - t0
        total += dt
        print(f"[{dt * 1000:6.0f}ms] {sentence}\n          -> {out}")
    print(f"avg {total / len(SENTENCES) * 1000:.0f} ms/sentence")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).resolve().parent.parent / "models" / "nllb-200-600M-ct2")
    compute_type = sys.argv[2] if len(sys.argv) > 2 else None
    prepare_cuda_dlls()
    run(model, "cuda", compute_type)
    if "--cpu" in sys.argv:
        run(model, "cpu", compute_type)

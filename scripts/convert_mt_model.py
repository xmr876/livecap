"""Convert the NLLB translation model to CTranslate2 format.

Why: the packaged app should not carry PyTorch (~2.6 GB). CTranslate2 is already
there for Whisper, and it runs the same model faster and with a smaller footprint.

    .venv\\Scripts\\python.exe scripts\\convert_mt_model.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENIZER_FILES = (
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "sentencepiece.bpe.model", "vocab.json", "merges.txt",
)  # NOTE: config.json is CTranslate2's own file - never overwrite it with the HF one


def convert(model: str, out: Path, quantization: str) -> None:
    if (out / "model.bin").exists():
        print(f"already converted: {out}")
        return
    out.mkdir(parents=True, exist_ok=True)
    converter = Path(sys.executable).parent / "ct2-transformers-converter.exe"
    cmd = ([str(converter)] if converter.exists()
           else [sys.executable, "-m", "ctranslate2.converters.transformers"])
    cmd += ["--model", model, "--output_dir", str(out),
            "--quantization", quantization, "--force"]
    print("running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def copy_tokenizer(src: Path, out: Path) -> list[str]:
    copied = []
    for name in TOKENIZER_FILES:
        candidate = src / name
        if candidate.exists():
            shutil.copy2(candidate, out / name)
            copied.append(name)
    return copied


def smoke_test(out: Path) -> None:
    from livecap.asr import prepare_cuda_dlls
    from livecap.translate import CT2NLLBTranslator

    prepare_cuda_dlls()
    translator = CT2NLLBTranslator(str(out), device="cuda")
    for sentence in (
        "今日も配信を見に来てくれてありがとうございます。",
        "総務省が発表したデータによると、去年の訪日外国人は二千五百万人を超えました。",
    ):
        print(f"ja: {sentence}")
        print(f"zh: {translator.translate(sentence)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="facebook/nllb-200-distilled-600M")
    parser.add_argument("--out", default=str(ROOT / "models" / "nllb-200-600M-ct2"))
    parser.add_argument("--quantization", default="int8_float16",
                        choices=["int8_float16", "int8", "float16", "int8_float32", "float32"])
    parser.add_argument("--skip-test", action="store_true")
    parser.add_argument("--skip-convert", action="store_true",
                        help="only copy the tokenizer files and smoke test")
    args = parser.parse_args()

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from huggingface_hub import snapshot_download

    src = Path(snapshot_download(args.model))
    print("snapshot:", src, flush=True)

    out = Path(args.out)
    if not args.skip_convert:
        convert(args.model, out, args.quantization)
    print("tokenizer files copied:", copy_tokenizer(src, out), flush=True)

    if not (out / "tokenizer.json").exists():
        print("WARNING: no tokenizer.json - the app will need transformers for the tokenizer")
        return 2
    if not args.skip_test:
        sys.path.insert(0, str(ROOT))
        smoke_test(out)
    print("OK", out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

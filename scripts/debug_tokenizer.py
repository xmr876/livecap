"""Work out how to feed NLLB sources to CTranslate2 correctly."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "nllb-200-600M-ct2"
TEXT = "今日も配信を見に来てくれてありがとうございます。"

from tokenizers import Tokenizer  # noqa: E402

tok = Tokenizer.from_file(str(MODEL / "tokenizer.json"))
enc = tok.encode(TEXT)
print("tokenizers-only tokens:", enc.tokens)
print("ids[:8]              :", enc.ids[:8])
for name in ("jpn_Jpan", "zho_Hans", "</s>", "<unk>", "<s>"):
    print(f"  id({name}) = {tok.token_to_id(name)}")

print("\n-- transformers reference --")
try:
    from transformers import AutoTokenizer

    hf = AutoTokenizer.from_pretrained("facebook/nllb-200-distilled-600M", src_lang="jpn_Jpan")
    ids = hf(TEXT)["input_ids"]
    print("hf ids[:8]   :", ids[:8])
    print("hf tokens[:8]:", hf.convert_ids_to_tokens(ids[:8]))
    print("hf all tokens:", hf.convert_ids_to_tokens(ids))
except Exception as exc:  # noqa: BLE001
    print("transformers unavailable:", type(exc).__name__, exc)
    sys.exit(0)

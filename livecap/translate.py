"""Japanese -> Chinese translation backends.

All backends expose ``translate(text) -> str`` and are driven by a single worker
thread, so they do not need to be thread safe.

* ``ct2``    : the default. NLLB-200-distilled-600M converted to CTranslate2
               (``models/nllb-200-600M-ct2``), direct jpn_Jpan -> zho_Hans.
               Runs on the GPU in ~0.1s per line and needs no PyTorch, which is
               what makes the packaged build small.
* ``llm``    : any OpenAI-compatible chat endpoint. Defaults follow the current
               DeepSeek docs (https://api-docs.deepseek.com/api/create-chat-completion):
               base URL ``https://api.deepseek.com``, model ``deepseek-flash``,
               thinking mode explicitly **disabled** (thinking would add seconds
               of latency to every subtitle line).
* ``nllb``   : the same model through transformers + PyTorch (dev installs only;
               not available in the packaged build).
* ``marian`` : Helsinki-NLP/opus-mt-ja-en then opus-mt-en-zh (no official
               direct ja->zh OPUS model exists, so this pivots through English).
* ``none``   : Japanese only.
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

from .paths import resolve_model

log = logging.getLogger("livecap.translate")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
# model names the DeepSeek API no longer serves (see docs: only deepseek-flash
# and deepseek-v4-pro are valid now)
RETIRED_DEEPSEEK_MODELS = {"deepseek-chat", "deepseek-reasoner", "deepseek-coder"}

_SYSTEM_PROMPT = (
    "你在为日文直播做实时字幕翻译。把用户给出的日文翻成简体中文口语，"
    "只输出译文本身：不要解释、不要加引号、不要保留罗马字、不要重复原文。"
    "人名、作品名、专有名词保持通行译法。遇到语气词、笑声、口癖要译得自然。"
)


def normalize_llm_model(model: str) -> str:
    """Map retired DeepSeek model names onto the current one."""
    name = (model or "").strip()
    if not name or name.lower() in RETIRED_DEEPSEEK_MODELS:
        return DEEPSEEK_MODEL
    return name


def chat_completions_url(base_url: str) -> str:
    base = (base_url or DEEPSEEK_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def alternate_chat_url(url: str) -> str | None:
    """DeepSeek answers on both ``/chat/completions`` and ``/v1/chat/completions``.

    Whichever form the user typed, fall back to the other one if we see a 404.
    """
    if "/v1/chat/completions" in url:
        return url.replace("/v1/chat/completions", "/chat/completions")
    if url.endswith("/chat/completions"):
        return url[: -len("/chat/completions")] + "/v1/chat/completions"
    return None


class Translator:
    name = "base"

    def translate(self, text: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def self_check(self) -> None:
        """Raise when this backend cannot translate right now.

        Called once at startup so a bad API key or a broken model falls back to
        another backend instead of silently producing Japanese-only subtitles.
        """
        return

    def close(self) -> None:
        pass


class NoopTranslator(Translator):
    name = "none"

    def translate(self, text: str) -> str:
        return ""


class CT2NLLBTranslator(Translator):
    """NLLB via CTranslate2: no torch, no transformers, GPU or CPU.

    NLLB wants the source language token in front of the text and the target
    language token as the decoder prefix, e.g.
    ``["jpn_Jpan", "▁今日", …, "</s>"] -> ["zho_Hans", …]``.
    """

    name = "ct2"
    src_token = "jpn_Jpan"
    tgt_token = "zho_Hans"

    def __init__(self, model_dir: str, device: str | None = None,
                 compute_type: str | None = None):
        import ctranslate2
        from tokenizers import Tokenizer

        path = Path(resolve_model(model_dir))
        tokenizer_file = path / "tokenizer.json"
        if not tokenizer_file.exists():
            raise RuntimeError(
                f"{path} has no tokenizer.json - run: "
                f".venv\\Scripts\\python.exe scripts\\convert_mt_model.py"
            )
        self.tokenizer = Tokenizer.from_file(str(tokenizer_file))
        for token in (self.src_token, self.tgt_token):
            if self.tokenizer.token_to_id(token) is None:
                raise RuntimeError(f"tokenizer has no {token} token")

        if device is None:
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        if compute_type is None:
            # the shipped model is float16; on CPU int8 is dramatically faster.
            # ("default" would re-quantize the fp16 weights for a GPU load.)
            compute_type = "float16" if device == "cuda" else "int8"
        log.info("loading MT model %s on %s/%s", path, device, compute_type)
        self.translator = ctranslate2.Translator(
            str(path), device=device, compute_type=compute_type,
            inter_threads=1, intra_threads=4,
        )

    def translate(self, text: str) -> str:
        tokens = self.tokenizer.encode(text).tokens
        while tokens and tokens[-1] == "<unk>":   # artifact of the raw tokenizer
            tokens.pop()
        if not tokens:
            return ""
        results = self.translator.translate_batch(
            [[self.src_token] + tokens],
            target_prefix=[[self.tgt_token]],
            beam_size=1, max_decoding_length=200, max_batch_size=1,
        )
        hypothesis = results[0].hypotheses[0]
        if hypothesis and hypothesis[0] == self.tgt_token:
            hypothesis = hypothesis[1:]
        ids = [self.tokenizer.token_to_id(token) for token in hypothesis]
        return self.tokenizer.decode([i for i in ids if i is not None],
                                     skip_special_tokens=True).strip()

    def self_check(self) -> None:
        if not self.translate("こんにちは"):
            raise RuntimeError("the model returned nothing")


class _Seq2Seq(Translator):
    """Shared plumbing for the local HuggingFace seq2seq models (dev only)."""

    def __init__(self, device: str | None = None):
        import torch

        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

    @property
    def _dtype(self):
        return self._torch.float16 if self.device == "cuda" else self._torch.float32

    def _load(self, model_name: str, **kwargs):
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_name, **kwargs)
        try:  # transformers >= 5 renamed torch_dtype -> dtype
            model = AutoModelForSeq2SeqLM.from_pretrained(model_name, dtype=self._dtype)
        except TypeError:
            model = AutoModelForSeq2SeqLM.from_pretrained(model_name, torch_dtype=self._dtype)
        model = model.to(self.device).eval()
        log.info("loaded %s on %s (%s)", model_name, self.device, self._dtype)
        return tokenizer, model

    def _generate(self, tokenizer, model, text: str, extra: dict | None = None) -> str:
        batch = tokenizer([text], return_tensors="pt", padding=True, truncation=True,
                          max_length=512).to(self.device)
        with self._torch.inference_mode():
            # max_length=None keeps transformers from warning about max_new_tokens
            out = model.generate(**batch, max_new_tokens=220, max_length=None,
                                 num_beams=1, do_sample=False, **(extra or {}))
        return tokenizer.batch_decode(out, skip_special_tokens=True)[0].strip()


class NLLBTranslator(_Seq2Seq):
    name = "nllb"

    def __init__(self, model_name: str, device: str | None = None):
        super().__init__(device)
        self.tok, self.model = self._load(model_name, src_lang="jpn_Jpan")
        self._bos = self.tok.convert_tokens_to_ids("zho_Hans")

    def translate(self, text: str) -> str:
        return self._generate(self.tok, self.model, text,
                              {"forced_bos_token_id": self._bos})


class MarianTranslator(_Seq2Seq):
    """OPUS-MT. With a pivot model it chains ja->en->zh."""

    name = "marian"

    def __init__(self, model_name: str, pivot_model: str | None = None,
                 device: str | None = None):
        super().__init__(device)
        self.tok, self.model = self._load(model_name)
        self.pivot_tok = self.pivot_model = None
        if pivot_model:
            self.pivot_tok, self.pivot_model = self._load(pivot_model)

    def translate(self, text: str) -> str:
        mid = self._generate(self.tok, self.model, text)
        if self.pivot_model is None or not mid:
            return mid
        return self._generate(self.pivot_tok, self.pivot_model, mid)


def llm_request(base_url: str, model: str, api_key: str, messages: list[dict],
                *, max_tokens: int = 400, temperature: float | None = None,
                thinking: str = "auto", timeout: float = 30.0) -> tuple[str, str]:
    """POST /chat/completions and return ``(text, endpoint_used)``.

    Follows the current DeepSeek API shape:
      * base URL ``https://api.deepseek.com`` (``/v1`` also works),
      * ``model`` is ``deepseek-flash`` or ``deepseek-v4-pro``,
      * ``thinking: {"type": "disabled"}`` keeps subtitle latency low - thinking
        is ON by default server side,
      * the answer is in ``choices[0].message.content`` (``reasoning_content``
        only carries the chain of thought).
    Non-DeepSeek endpoints simply do not get the ``thinking`` field.
    """
    import requests

    url = chat_completions_url(base_url)
    is_deepseek = "deepseek" in (base_url or "").lower()
    if thinking == "auto":
        thinking = "disabled" if is_deepseek else "none"

    payload: dict = {
        "model": normalize_llm_model(model),
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if thinking in ("enabled", "disabled"):
        payload["thinking"] = {"type": thinking}

    headers = {"Authorization": f"Bearer {api_key or 'local'}",
               "Content-Type": "application/json"}

    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if response.status_code == 404:
        fallback = alternate_chat_url(url)
        if fallback:
            log.warning("%s returned 404, retrying %s", url, fallback)
            url = fallback
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} ({url}): {response.text[:240]}")

    try:
        message = response.json()["choices"][0]["message"]
    except (KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(f"unexpected response: {response.text[:240]}") from exc

    text = (message.get("content") or "").strip()
    if not text and message.get("reasoning_content"):
        raise RuntimeError("model only returned reasoning content - thinking mode is on")
    return text, url


def llm_probe(base_url: str, model: str, api_key: str, thinking: str = "auto") -> str:
    """Tiny live check used by the GUI's 「测试连接」 button."""
    text, url = llm_request(
        base_url, model, api_key,
        [{"role": "user", "content": "把「こんにちは」翻译成简体中文，只输出译文"}],
        max_tokens=64, temperature=0.0, thinking=thinking, timeout=25.0,
    )
    return f"{text}  [{url} · {normalize_llm_model(model)}]"


class LLMTranslator(Translator):
    name = "llm"

    def __init__(self, base_url: str, model: str, api_key: str, context: int = 4,
                 thinking: str = "auto", temperature: float = 1.3):
        local = any(host in (base_url or "") for host in ("127.0.0.1", "localhost", "::1"))
        if not api_key and not local:
            raise RuntimeError("no API key configured (fill it in the panel or set the env var)")
        self.base_url = (base_url or DEEPSEEK_BASE_URL).strip()
        self.model = normalize_llm_model(model)
        self.api_key = api_key
        self.thinking = thinking
        self.temperature = temperature          # DeepSeek recommends 1.3 for translation
        self.history: deque[str] = deque(maxlen=max(1, context))
        log.info("LLM translator: %s @ %s (thinking=%s)",
                 self.model, chat_completions_url(self.base_url), thinking)

    def translate(self, text: str) -> str:
        user = text
        if self.history:
            lines = "\n".join(f"前文{i + 1}: {t}" for i, t in enumerate(self.history))
            user = f"{lines}\n当前: {text}"
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]
        out, _ = llm_request(self.base_url, self.model, self.api_key, messages,
                             max_tokens=400, temperature=self.temperature,
                             thinking=self.thinking)
        self.history.append(text)
        return out

    def self_check(self) -> None:
        """One tiny request: catches a wrong key / model / endpoint up front."""
        llm_probe(self.base_url, self.model, self.api_key, self.thinking)


def build_translator(cfg) -> Translator:
    """Create the configured translator, degrading to a working one if needed."""
    kind = cfg.translator
    if kind == "none":
        return NoopTranslator()

    errors: list[str] = []
    for candidate in [kind] + [k for k in ("ct2", "llm") if k != kind]:
        try:
            translator = _make(cfg, candidate)
            translator.self_check()          # actually usable?
            if candidate != kind:
                log.warning("%s is unavailable, using %s instead", kind, candidate)
            return translator
        except Exception as exc:  # noqa: BLE001 - never let MT setup kill the pipeline
            errors.append(f"{candidate}({exc})")
            log.error("translator %s unavailable: %s", candidate, exc)

    log.warning("no translator available: %s - Japanese subtitles only", "; ".join(errors))
    return NoopTranslator()


def _make(cfg, kind: str) -> Translator:
    if kind == "ct2":
        return CT2NLLBTranslator(cfg.mt_ct2_dir,
                                 compute_type=cfg.mt_compute_type or None)
    if kind == "nllb":
        return NLLBTranslator(cfg.nllb_model)
    if kind == "marian":
        return MarianTranslator(cfg.mt_model, cfg.mt_pivot_model)
    if kind == "llm":
        return LLMTranslator(cfg.llm_base_url, cfg.llm_model, cfg.llm_api_key,
                             cfg.llm_context, cfg.llm_thinking, cfg.llm_temperature)
    raise ValueError(f"unknown translator: {kind}")

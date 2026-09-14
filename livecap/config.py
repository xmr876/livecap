"""Configuration and CLI for livecap."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path

from .paths import find_ffmpeg


@dataclass
class Config:
    # input - system audio (WASAPI loopback) is the default: no URL, no cookies
    source: str = "loopback"       # loopback | url | file
    url: str = ""
    file: str = ""
    ffmpeg: str = field(default_factory=find_ffmpeg)
    restart_delay: float = 3.0
    max_duration: float = 0.0    # >0: stop after N seconds of audio (handy for tests)
    cookies: str = ""            # Netscape cookies.txt for sites that need a login
    cookies_from_browser: str = ""
    ytdlp_args: str = ""         # extra raw yt-dlp arguments
    loopback_device: str = ""    # substring match for --source loopback

    # ASR
    asr_model: str = "Systran/faster-whisper-large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str = "ja"
    beam_size: int = 1
    cpu_threads: int = 4
    context_lines: int = 3

    # segmentation (energy VAD)
    sample_rate: int = 16000
    frame_ms: int = 30
    vad_threshold: float = 0.004   # absolute floor; the segmenter adapts above the noise floor
    min_silence: float = 0.35
    min_speech: float = 0.25
    max_segment: float = 7.0
    preroll: float = 0.20
    max_lag: float = 5.0           # drop segments older than this (keeps subtitles live)

    # translation
    translator: str = "ct2"        # ct2 | llm | nllb | marian | none
    mt_ct2_dir: str = "nllb-200-600M-ct2"
    mt_compute_type: str = ""      # empty = auto (float16 on GPU, int8 on CPU)
    mt_model: str = "Helsinki-NLP/opus-mt-ja-en"
    mt_pivot_model: str = "Helsinki-NLP/opus-mt-en-zh"
    nllb_model: str = "facebook/nllb-200-distilled-600M"
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"    # current DeepSeek models: deepseek-flash / deepseek-v4-pro
    llm_thinking: str = "auto"           # auto | enabled | disabled (auto = off for DeepSeek)
    llm_temperature: float = 1.3         # DeepSeek's recommended value for translation
    llm_api_key_env: str = "DEEPSEEK_API_KEY"
    llm_api_key_value: str = ""     # set from the GUI / --llm-api-key
    llm_context: int = 4

    # output
    overlay: bool = True
    font_zh: int = 27
    font_ja: int = 23              # "slightly smaller" than the Chinese line
    max_width_fraction: float = 0.6   # bar width as a share of the screen
    fixed_width: bool = True       # wide fixed bar; False = shrink-wrap the box
    clear_after: float = 12.0
    opacity: float = 0.92
    log_path: str = ""
    show_partial: bool = True
    verbose: bool = False
    download_models: bool = False   # --download-models: fetch models then exit
    check_models: bool = False      # --check-models: report what is missing

    @property
    def llm_api_key(self) -> str:
        """Explicit key wins; otherwise fall back to the environment variable."""
        return (self.llm_api_key_value or os.environ.get(self.llm_api_key_env, "")).strip()


def parse_args(argv: list[str] | None = None) -> Config:
    p = argparse.ArgumentParser(
        prog="livecap",
        description="Realtime Japanese live-stream subtitles (Japanese + Chinese).",
    )
    p.add_argument("--source", choices=["url", "file", "loopback"], default=Config.source)
    p.add_argument("--url", default="", help="live stream page URL (YouTube/Twitch/niconico/bilibili ...)")
    p.add_argument("--file", default="", help="local media file (played at realtime speed)")
    p.add_argument("--ffmpeg", default=None, help="path to ffmpeg.exe")
    p.add_argument("--max-duration", type=float, default=0.0,
                   help="stop after N seconds of audio, then print the tail (0 = unlimited)")
    p.add_argument("--cookies", default="", help="Netscape cookies.txt (YouTube often needs this)")
    p.add_argument("--cookies-from-browser", default="",
                   help="read cookies from a browser, e.g. edge / chrome / firefox")
    p.add_argument("--ytdlp-args", default="", help="extra yt-dlp arguments, space separated")
    p.add_argument("--loopback-device", default="",
                   help="pick the output device to capture, e.g. \"Realtek\" (see check_loopback.py)")

    g = p.add_argument_group("ASR")
    g.add_argument("--asr-model", default=Config.asr_model)
    g.add_argument("--device", default=Config.device, choices=["cuda", "cpu"])
    g.add_argument("--compute-type", default=Config.compute_type,
                   choices=["float16", "int8_float16", "int8", "float32"])
    g.add_argument("--beam-size", type=int, default=Config.beam_size)

    g = p.add_argument_group("segmentation")
    g.add_argument("--vad-threshold", type=float, default=Config.vad_threshold)
    g.add_argument("--min-silence", type=float, default=Config.min_silence)
    g.add_argument("--min-speech", type=float, default=Config.min_speech)
    g.add_argument("--max-segment", type=float, default=Config.max_segment)
    g.add_argument("--max-lag", type=float, default=Config.max_lag,
                   help="drop sentences that are this many seconds behind (default 5)")

    g = p.add_argument_group("translation")
    g.add_argument("--translator", default=Config.translator,
                   choices=["ct2", "llm", "nllb", "marian", "none"])
    g.add_argument("--mt-ct2-dir", dest="mt_ct2_dir", default=Config.mt_ct2_dir,
                   help="folder with the converted NLLB model (models/…-ct2)")
    g.add_argument("--mt-compute-type", dest="mt_compute_type", default="",
                   help="override CTranslate2 compute type, e.g. int8 / float16 / default")
    g.add_argument("--mt-model", default=Config.mt_model)
    g.add_argument("--mt-pivot", dest="mt_pivot_model", default=Config.mt_pivot_model)
    g.add_argument("--nllb-model", default=Config.nllb_model)
    g.add_argument("--llm-base-url", default=Config.llm_base_url)
    g.add_argument("--llm-model", default=Config.llm_model)
    g.add_argument("--llm-api-key-env", default=Config.llm_api_key_env)
    g.add_argument("--llm-api-key", dest="llm_api_key_value", default="",
                   help="API key for the LLM translator (otherwise read from --llm-api-key-env)")
    g.add_argument("--llm-thinking", default=Config.llm_thinking,
                   choices=["auto", "enabled", "disabled"],
                   help="DeepSeek thinking mode; auto = disabled (keeps subtitles fast)")
    g.add_argument("--llm-temperature", type=float, default=Config.llm_temperature)

    g = p.add_argument_group("output")
    g.add_argument("--no-overlay", action="store_true", help="print subtitles to console instead")
    g.add_argument("--font-ja", type=int, default=Config.font_ja)
    g.add_argument("--font-zh", type=int, default=Config.font_zh)
    g.add_argument("--clear-after", type=float, default=Config.clear_after)
    g.add_argument("--bar-width", dest="max_width_fraction", type=float,
                   default=Config.max_width_fraction, help="bar width as a share of the screen")
    g.add_argument("--shrink-wrap", dest="fixed_width", action="store_false",
                   help="let the box hug the text instead of a full-width bar")
    g.add_argument("--log", dest="log_path", default="", help="append transcripts to this jsonl file")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--download-models", action="store_true",
                   help="download the ASR + translation models (about 4 GB) and exit")
    p.add_argument("--check-models", action="store_true",
                   help="print which models are present and exit (0 = all there)")

    a = p.parse_args(argv)
    cfg = Config(
        source=a.source, url=a.url, file=a.file, max_duration=a.max_duration,
        cookies=a.cookies, cookies_from_browser=a.cookies_from_browser,
        ytdlp_args=a.ytdlp_args, loopback_device=a.loopback_device,
        asr_model=a.asr_model, device=a.device, compute_type=a.compute_type,
        beam_size=a.beam_size, vad_threshold=a.vad_threshold, min_silence=a.min_silence,
        min_speech=a.min_speech, max_segment=a.max_segment, max_lag=a.max_lag,
        translator=a.translator, mt_model=a.mt_model, nllb_model=a.nllb_model,
        mt_pivot_model=a.mt_pivot_model, mt_ct2_dir=a.mt_ct2_dir,
        mt_compute_type=a.mt_compute_type,
        llm_base_url=a.llm_base_url, llm_model=a.llm_model, llm_api_key_env=a.llm_api_key_env,
        llm_api_key_value=a.llm_api_key_value, llm_thinking=a.llm_thinking,
        llm_temperature=a.llm_temperature,
        overlay=not a.no_overlay, font_ja=a.font_ja, font_zh=a.font_zh,
        clear_after=a.clear_after, log_path=a.log_path, verbose=a.verbose,
        download_models=a.download_models, check_models=a.check_models,
        max_width_fraction=a.max_width_fraction, fixed_width=a.fixed_width,
    )
    if a.ffmpeg:
        cfg.ffmpeg = a.ffmpeg
    if cfg.source == "url" and not cfg.url:
        p.error("--url is required when --source url")
    if cfg.source == "file":
        if not cfg.file:
            p.error("--file is required when --source file")
        if not Path(cfg.file).exists():
            p.error(f"file not found: {cfg.file}")
    return cfg

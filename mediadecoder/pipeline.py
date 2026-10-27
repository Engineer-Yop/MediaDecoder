"""Validate -> analyze locally -> (optionally) ask Claude for the recipe."""
from __future__ import annotations

import time
from pathlib import Path

from . import ai
from .audio_analysis import analyze_audio
from .config import Settings
from .image_analysis import analyze_image
from .media import CancelFn, MediaError, ProgressFn, check_cancel, detect_kind, probe, validate_file
from .video_analysis import analyze_video


def analyze_file(path: Path, settings: Settings, progress: ProgressFn, cancel: CancelFn):
    """Local analysis only. Returns (analysis_dict, images[(label, jpeg)])."""
    path = Path(path)
    progress(1, "Checking file")
    kind = detect_kind(path)
    validate_file(path, settings.max_file_mb)
    file_info = {"name": path.name, "size_bytes": path.stat().st_size, "extension": path.suffix.lower()}

    if kind == "image":
        data, images = analyze_image(path, settings, progress, cancel)
    else:
        progress(2, "Reading streams")
        info = probe(path)
        if kind == "video" and not info.get("video"):
            if not info.get("audio"):
                raise MediaError("The file contains no video or audio streams.")
            kind = "audio"  # e.g. an audio-only MP4
        if kind == "video":
            data, images = analyze_video(path, info, settings, progress, cancel)
            if info.get("audio"):
                audio, audio_images = analyze_audio(path, info, settings, progress, cancel,
                                                    p0=51, p1=70, standalone=False)
                data["warnings"] += audio.pop("warnings")
                data.update(audio)
                images += audio_images
                onsets = audio.get("onset_times_s") or []
                cut_times = [b["time"] for b in data["transitions"] if b["type"] == "hard cut"]
                if onsets and len(cut_times) >= 3:
                    on_beat = sum(1 for c in cut_times if min(abs(c - o) for o in onsets) < 0.09)
                    data["pacing"]["cuts_on_audio_onsets_pct"] = round(100 * on_beat / len(cut_times), 1)
        else:
            if not info.get("audio"):
                raise MediaError("No audio stream was found in this file.")
            data, images = analyze_audio(path, info, settings, progress, cancel, p0=5, p1=70)
    check_cancel(cancel)
    data["file"] = file_info
    data.setdefault("warnings", [])
    data["note"] = "All values were measured locally; interpretations are estimates."
    return data, images


def run(path: Path, settings: Settings, progress: ProgressFn, cancel: CancelFn,
        analyze_only: bool = False) -> dict:
    started = time.time()
    exe = None
    if not analyze_only:
        progress(0.5, "Checking Claude sign-in")
        exe = ai.preflight(settings)
    analysis, images = analyze_file(path, settings, progress, cancel)
    result = {"file": analysis["file"], "kind": analysis["kind"], "analysis": analysis,
              "images_sent": [label for label, _ in images], "recipe": None, "meta": {}}
    if not analyze_only:
        recipe, meta = ai.generate_recipe(analysis, images, settings, exe, progress, cancel)
        result["recipe"] = recipe
        result["meta"] = meta
    result["meta"]["elapsed_s"] = round(time.time() - started, 1)
    progress(100, "Done")
    return result

"""Video: scene/cut detection over the whole (capped) file, transitions, camera motion,
pacing, frame sampling and embedded subtitles."""
from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from .config import Settings
from .image_analysis import palette
from .media import (CancelFn, MediaError, ProgressFn, aspect_label, check_cancel, fmt_time,
                    run_ffmpeg)
from .motion import summarize_motion

LOWRES_W, LOWRES_H, LOWRES_FPS = 192, 108, 4.0
_PTS = re.compile(r"pts_time:([\d.]+)")
_SCORE = re.compile(r"lavfi\.scene_score=([\d.]+)")
_BLACK = re.compile(r"black_start:([\d.]+)\s+black_end:([\d.]+)")
_TIME = re.compile(r"time=(\d+):(\d+):([\d.]+)")


def _scene_pass(path: Path, cap: float, progress: ProgressFn, cancel: CancelFn, p0: float, p1: float):
    """One decode of the whole capped video at low resolution: per-frame scene scores + black intervals."""
    scores: list[tuple[float, float]] = []
    blacks: list[tuple[float, float]] = []
    pending_t: list[float] = []

    def on_line(line: str):
        m = _PTS.search(line)
        if m and "Parsed_metadata" in line:
            pending_t.append(float(m.group(1)))
            if len(scores) % 300 == 0 and cap:
                progress(p0 + (p1 - p0) * min(1.0, pending_t[-1] / cap),
                         f"Detecting scene changes ({fmt_time(pending_t[-1])} / {fmt_time(cap)})")
            return
        m = _SCORE.search(line)
        if m and pending_t:
            scores.append((pending_t[-1], float(m.group(1))))
            return
        m = _BLACK.search(line)
        if m:
            blacks.append((float(m.group(1)), float(m.group(2))))

    vf = ("scale=320:-2,blackdetect=d=0.08:pix_th=0.10,"
          "select='gte(scene,0)',metadata=print:key=lavfi.scene_score")
    code, _, err = run_ffmpeg(["-i", str(path), "-t", f"{cap:.3f}", "-map", "0:v:0", "-an", "-sn",
                               "-vf", vf, "-f", "null", "-"],
                              cancel=cancel, on_stderr_line=on_line, want_stdout=False)
    if code != 0 and not scores:
        tail = err.strip().splitlines()[-1] if err.strip() else "unknown error"
        raise MediaError(f"Could not decode the video stream - the file may be corrupted ({tail}).")
    decode_errors = sum(1 for line in err.splitlines() if "error while decoding" in line.lower()
                        or "corrupt" in line.lower())
    return scores, blacks, decode_errors


def _lowres_pass(path: Path, cap: float, cancel: CancelFn) -> np.ndarray:
    vf = f"fps={LOWRES_FPS},scale={LOWRES_W}:{LOWRES_H},format=gray"
    _, out, _ = run_ffmpeg(["-i", str(path), "-t", f"{cap:.3f}", "-map", "0:v:0", "-an", "-sn",
                            "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"], cancel=cancel)
    n = len(out) // (LOWRES_W * LOWRES_H)
    if n == 0:
        return np.zeros((0, LOWRES_H, LOWRES_W), dtype=np.float32)
    return np.frombuffer(out[: n * LOWRES_W * LOWRES_H], dtype=np.uint8).reshape(n, LOWRES_H, LOWRES_W).astype(np.float32)


def _detect_boundaries(scores, blacks, threshold: float, duration: float):
    """Turn per-frame scene scores into boundaries with a transition type."""
    if not scores:
        return []
    times = np.array([t for t, _ in scores])
    vals = np.array([s for _, s in scores])
    frame_dt = float(np.median(np.diff(times))) if len(times) > 1 else 1 / 30
    boundaries = []

    # Hard cuts: a single-frame spike above threshold, local max within +-2 frames.
    for i, v in enumerate(vals):
        if v >= threshold and v == vals[max(0, i - 2): i + 3].max():
            boundaries.append({"time": float(times[i]), "type": "hard cut", "score": round(float(v), 3)})

    # Gradual transitions: a sustained run of moderate scores that adds up to a cut's worth of change.
    low = threshold * 0.12
    i = 0
    while i < len(vals):
        if low < vals[i] < threshold:
            j = i
            while j + 1 < len(vals) and low < vals[j + 1] < threshold:
                j += 1
            span = times[j] - times[i] + frame_dt
            if 0.25 <= span <= 3.0 and vals[i:j + 1].sum() >= threshold * 1.8:
                mid = float((times[i] + times[j]) / 2)
                boundaries.append({"time": mid, "type": "gradual (dissolve / crossfade / morph - or very fast motion)",
                                   "span_s": round(float(span), 2), "score": round(float(vals[i:j + 1].sum()), 3)})
            i = j + 1
        else:
            i += 1

    # Dips to black: a black interval between two non-black sections.
    for b0, b1 in blacks:
        if b0 > 0.05 and b1 < duration - 0.05:
            boundaries.append({"time": (b0 + b1) / 2, "type": "dip to black / fade through black",
                               "span_s": round(b1 - b0, 2)})

    boundaries.sort(key=lambda b: b["time"])
    merged = []
    for b in boundaries:  # keep one boundary per 0.3 s, preferring fades > cuts > gradual
        if merged and b["time"] - merged[-1]["time"] < 0.3:
            rank = {"dip": 0, "hard": 1, "grad": 2}
            if rank[b["type"][:4]] < rank[merged[-1]["type"][:4]]:
                merged[-1] = b
            continue
        merged.append(b)
    return [b for b in merged if 0.1 < b["time"] < duration - 0.1]


def _fades(lum: np.ndarray, times: np.ndarray, duration: float) -> list[dict]:
    out = []
    if len(lum) < 4:
        return out
    if lum[0] < 18 and lum[min(len(lum) - 1, 8)] > lum[0] + 20:
        end = next((times[i] for i in range(len(lum)) if lum[i] > lum[0] + 0.8 * (lum[:12].max() - lum[0])), times[0])
        out.append({"type": "fade in from black", "start": 0.0, "end": round(float(end), 2)})
    if lum[-1] < 18 and lum[max(0, len(lum) - 9)] > lum[-1] + 20:
        out.append({"type": "fade out to black", "start": round(float(max(0, duration - 2)), 2), "end": round(duration, 2)})
    if lum[0] > 235 and lum[min(len(lum) - 1, 8)] < lum[0] - 25:
        out.append({"type": "fade in from white", "start": 0.0})
    if lum[-1] > 235 and lum[max(0, len(lum) - 9)] < lum[-1] - 25:
        out.append({"type": "fade out to white", "end": round(duration, 2)})
    return out


def _choose_frames(scenes: list[dict], cap: float, interval: float, max_frames: int) -> list[dict]:
    picks: list[dict] = []
    for s in scenes:
        d = s["end"] - s["start"]
        picks.append({"time": s["start"] + min(0.35, d * 0.2), "scene": s["index"], "reason": "scene start", "prio": 0})
        if d > 2 * interval or d > 4:
            picks.append({"time": s["start"] + d / 2, "scene": s["index"], "reason": "mid scene", "prio": 1})
        if d > 8:
            picks.append({"time": s["end"] - min(0.5, d * 0.1), "scene": s["index"], "reason": "scene end", "prio": 1})
    t = interval
    while t < cap - 0.2:
        scene = next((s["index"] for s in scenes if s["start"] <= t < s["end"]), scenes[-1]["index"])
        picks.append({"time": t, "scene": scene, "reason": f"every {interval:g}s", "prio": 2})
        t += interval
    picks.append({"time": max(0.0, cap - 0.15), "scene": scenes[-1]["index"], "reason": "final frame", "prio": 0})

    picks.sort(key=lambda p: (p["time"], p["prio"]))
    deduped: list[dict] = []
    for p in picks:
        if deduped and p["time"] - deduped[-1]["time"] < 0.4:
            if p["prio"] < deduped[-1]["prio"]:
                deduped[-1] = p
            continue
        deduped.append(p)

    if len(deduped) > max_frames:  # keep scene starts first, then fill evenly with the rest
        must = [p for p in deduped if p["prio"] == 0]
        rest = [p for p in deduped if p["prio"] > 0]
        if len(must) > max_frames:
            idx = np.linspace(0, len(must) - 1, max_frames).round().astype(int)
            chosen = [must[i] for i in sorted(set(idx))]
        else:
            room = max_frames - len(must)
            idx = np.linspace(0, len(rest) - 1, room).round().astype(int) if room and rest else []
            chosen = must + [rest[i] for i in sorted(set(idx))]
        deduped = sorted(chosen, key=lambda p: p["time"])
    for p in deduped:
        p.pop("prio", None)
        p["time"] = round(p["time"], 2)
    return deduped


def _extract_jpeg(path: Path, t: float, w: int, h: int, cancel: CancelFn) -> bytes:
    scale = "scale=768:-2" if w >= h else "scale=-2:768"
    if max(w, h) <= 768:
        scale = "scale=iw:ih"
    _, out, _ = run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
                            "-vf", scale, "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                           cancel=cancel)
    return out


def _subtitles(path: Path, cancel: CancelFn) -> str:
    code, out, _ = run_ffmpeg(["-i", str(path), "-map", "0:s:0", "-f", "srt", "-"], cancel=cancel)
    return out.decode("utf-8", errors="replace")[:20000] if code == 0 else ""


def analyze_video(path: Path, info: dict, settings: Settings, progress: ProgressFn, cancel: CancelFn):
    v = info["video"]
    duration = info.get("duration_s") or 0.0
    cap_limit = settings.max_duration_min * 60
    cap = min(duration, cap_limit) if duration else cap_limit
    warnings = []
    if duration and duration > cap_limit:
        warnings.append(f"Video is {fmt_time(duration)} long; analysis covers the first {fmt_time(cap)} "
                        "(raise the limit in Settings).")

    progress(4, "Detecting scene changes")
    scores, blacks, decode_errors = _scene_pass(path, cap, progress, cancel, 4, 30)
    if scores:
        cap = min(cap, scores[-1][0] + 1 / (v.get("fps") or 30)) if not duration else cap
        if not duration:
            duration = cap
    if decode_errors:
        warnings.append(f"FFmpeg reported {decode_errors} decode error(s); the file may be partially corrupted.")

    check_cancel(cancel)
    boundaries = _detect_boundaries(scores, blacks, settings.scene_threshold, cap)
    edges = [0.0, *[b["time"] for b in boundaries], cap]
    scenes = []
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        if b - a <= 0.05:
            continue
        scenes.append({"index": len(scenes) + 1, "start": round(a, 2), "end": round(b, 2),
                       "duration": round(b - a, 2),
                       "transition_in": boundaries[i - 1]["type"] if i > 0 else "opening"})

    progress(31, "Measuring camera motion")
    low = _lowres_pass(path, cap, cancel)
    low_t = np.arange(len(low)) / LOWRES_FPS
    lum = low.mean(axis=(1, 2)) if len(low) else np.zeros(0)
    for s in scenes:
        check_cancel(cancel)
        sel = np.where((low_t >= s["start"] + 0.1) & (low_t <= s["end"] - 0.1))[0]
        frames = [low[i] for i in sel[:120]]
        s["camera_motion"] = summarize_motion(frames, LOWRES_FPS)
        s["mean_brightness"] = round(float(lum[sel].mean()) / 255, 3) if len(sel) else None
    fades = _fades(lum, low_t, cap)

    durations = [s["duration"] for s in scenes] or [cap]
    pacing = {
        "scene_count": len(scenes),
        "cuts_per_minute": round(max(0, len(scenes) - 1) / (cap / 60), 2) if cap else 0,
        "average_shot_s": round(float(np.mean(durations)), 2),
        "median_shot_s": round(float(np.median(durations)), 2),
        "shortest_shot_s": round(float(min(durations)), 2),
        "longest_shot_s": round(float(max(durations)), 2),
    }

    progress(42, "Extracting key frames")
    picks = _choose_frames(scenes, cap, max(1.0, settings.frame_interval_s), settings.max_frames)
    images = []
    w, h = v.get("width") or 1920, v.get("height") or 1080
    for n, p in enumerate(picks):
        check_cancel(cancel)
        progress(42 + 8 * n / max(1, len(picks)), f"Extracting key frames ({n + 1}/{len(picks)})")
        jpg = _extract_jpeg(path, p["time"], w, h, cancel)
        if not jpg:
            continue
        p["frame_no"] = len(images) + 1
        try:
            p["palette"] = palette(Image.open(BytesIO(jpg)), 5)
        except Exception:
            pass
        label = (f"Frame {p['frame_no']} - t={p['time']:.2f}s ({fmt_time(p['time'])}) - "
                 f"scene {p['scene']} - {p['reason']}")
        images.append((label, jpg))
    if not images:
        raise MediaError("No frames could be extracted from the video; the file may be corrupted.")

    subs = _subtitles(path, cancel) if info.get("subtitles") else ""
    data = {
        "kind": "video",
        "metadata": {
            "container": info.get("container"), "duration_s": round(duration, 3), "duration": fmt_time(duration),
            "analyzed_s": round(cap, 3), "width": w, "height": h, "aspect_ratio": aspect_label(w, h),
            "orientation": "vertical" if h > w else "horizontal" if w > h else "square",
            "fps": v.get("fps"), "video_codec": v.get("codec"), "pix_fmt": v.get("pix_fmt"),
            "hdr": v.get("hdr"), "rotation_flag": v.get("rotation"),
            "bitrate_kbps": info.get("bitrate_kbps"), "has_audio": bool(info.get("audio")),
            "audio_stream": info.get("audio"), "subtitle_streams": info.get("subtitles"),
            "container_tags": info.get("tags"),
        },
        "pacing": pacing,
        "scenes": scenes,
        "transitions": [b for b in boundaries],
        "fades": fades,
        "sampled_frames": picks,
        "embedded_subtitles_srt": subs,
        "warnings": warnings,
    }
    return data, images

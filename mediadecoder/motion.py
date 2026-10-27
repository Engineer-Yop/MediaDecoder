"""Global camera-motion estimation from small grayscale frames (numpy only).

Translation comes from phase correlation; zoom from testing a few scale factors after
translation compensation. Results are coarse by design: they tell the vision model
"pan left, slow" rather than replacing its judgement.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

_SCALES = (0.90, 0.94, 0.97, 1.0, 1.03, 1.06, 1.10)
_WINDOWS: dict[tuple[int, int], np.ndarray] = {}


def _window(h: int, w: int) -> np.ndarray:
    key = (h, w)
    if key not in _WINDOWS:
        _WINDOWS[key] = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    return _WINDOWS[key]


def phase_shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """(dx, dy, peak) where b is approximately a with its content moved by (dx, dy) pixels."""
    win = _window(*a.shape)
    fa = np.fft.fft2((a - a.mean()) * win)
    fb = np.fft.fft2((b - b.mean()) * win)
    cross = fb * np.conj(fa)
    cross /= np.abs(cross) + 1e-9
    corr = np.fft.ifft2(cross).real
    py, px = np.unravel_index(int(np.argmax(corr)), corr.shape)
    peak = float(corr[py, px])
    h, w = a.shape
    if py > h // 2:
        py -= h
    if px > w // 2:
        px -= w
    return float(px), float(py), peak


def _ncc(x: np.ndarray, y: np.ndarray) -> float:
    x = x - x.mean()
    y = y - y.mean()
    denom = math.sqrt(float((x * x).sum()) * float((y * y).sum())) + 1e-9
    return float((x * y).sum()) / denom


def zoom_factor(a: np.ndarray, b: np.ndarray, dx: float, dy: float) -> float:
    """Scale s such that a magnified by s (about its centre) best matches b. s > 1 = zoom in."""
    h, w = a.shape
    aligned = np.roll(np.roll(a, int(round(dy)), axis=0), int(round(dx)), axis=1).astype(np.float32)
    ch, cw = int(h * 0.6), int(w * 0.6)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    ref = b[y0:y0 + ch, x0:x0 + cw].astype(np.float32)
    src = Image.fromarray(aligned)  # float32 -> mode "F"
    scores = {}
    for s in _SCALES:
        sh, sw = ch / s, cw / s
        bx, by = (w - sw) / 2, (h - sh) / 2
        crop = np.asarray(src.resize((cw, ch), Image.BILINEAR, box=(bx, by, bx + sw, by + sh)))
        scores[s] = _ncc(crop, ref)
    best = max(scores, key=scores.get)
    # Only report a zoom when it clearly beats "no zoom" (resampling noise otherwise wins ties).
    return best if scores[best] - scores[1.0] > 0.01 else 1.0


def summarize_motion(frames: list[np.ndarray], fps: float) -> dict:
    """Describe the dominant camera motion across consecutive frames sampled at `fps`."""
    if len(frames) < 2:
        return {"label": "too short to measure", "pan_speed": 0.0, "tilt_speed": 0.0,
                "zoom_rate": 0.0, "shake": 0.0, "activity": 0.0}
    h, w = frames[0].shape
    dxs, dys, logs, peaks, diffs = [], [], [], [], []
    for a, b in zip(frames, frames[1:]):
        dx, dy, peak = phase_shift(a, b)
        dxs.append(dx)
        dys.append(dy)
        peaks.append(peak)
        logs.append(math.log(zoom_factor(a, b, dx, dy)))
        diffs.append(float(np.abs(b.astype(np.float32) - a.astype(np.float32)).mean()) / 255.0)

    vx = float(np.median(dxs)) * fps / w          # frame-widths per second (+ = content moves right)
    vy = float(np.median(dys)) * fps / h
    zoom = float(np.mean(logs)) * fps             # log-scale per second (+ = zoom in)
    shake = float(np.std(dxs) / w + np.std(dys) / h)
    activity = float(np.mean(diffs))
    confidence = float(np.median(peaks))

    labels = []
    pan_min = 0.035 if abs(zoom) > 0.025 else 0.02  # zooms about an off-centre point read as slight pans
    if abs(vx) > pan_min:
        labels.append(("pan left" if vx > 0 else "pan right") + _speed(abs(vx)))
    if abs(vy) > pan_min:
        labels.append(("tilt up" if vy > 0 else "tilt down") + _speed(abs(vy)))
    if abs(zoom) > 0.025:
        labels.append(("push-in / zoom in" if zoom > 0 else "pull-out / zoom out") + _speed(abs(zoom) * 0.7))
    if shake > 0.03 and not labels:
        labels.append("handheld / shaky")
    elif shake > 0.05:
        labels.append("with handheld shake")
    if not labels:
        if activity < 0.012:
            labels.append("static / locked-off (little movement)")
        elif confidence < 0.08:
            labels.append("no single global camera move (subject motion, morphing or complex move)")
        else:
            labels.append("static camera, with subject motion or a lighting/fade change")
    return {
        "label": ", ".join(labels),
        "pan_speed": round(vx, 3),
        "tilt_speed": round(vy, 3),
        "zoom_rate": round(zoom, 3),
        "shake": round(shake, 3),
        "activity": round(activity, 3),
        "tracking_confidence": round(confidence, 3),
    }


def _speed(v: float) -> str:
    if v < 0.06:
        return " (slow)"
    if v < 0.2:
        return " (moderate)"
    return " (fast)"

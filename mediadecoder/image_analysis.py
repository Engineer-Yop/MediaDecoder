"""Still images and animated GIF/WEBP: metadata, provenance hints, measured visual stats."""
from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
from PIL import ExifTags, Image, ImageOps, ImageSequence, UnidentifiedImageError

from .config import Settings
from .media import CancelFn, MediaError, ProgressFn, aspect_label, check_cancel, fmt_time
from .motion import summarize_motion

Image.MAX_IMAGE_PIXELS = 400_000_000  # allow big renders, still guard against decompression bombs

# Strings that, if present in metadata or raw file bytes, hint at the creating tool.
_MARKERS = {
    "c2pa": "C2PA Content Credentials manifest present",
    "trainedalgorithmicmedia": "IPTC DigitalSourceType says AI-generated (trainedAlgorithmicMedia)",
    "compositewithtrainedalgorithmicmedia": "IPTC DigitalSourceType says AI-edited composite",
    "midjourney": "Midjourney", "dall-e": "DALL-E", "dall·e": "DALL-E", "openai": "OpenAI",
    "firefly": "Adobe Firefly", "stable diffusion": "Stable Diffusion", "comfyui": "ComfyUI",
    "novelai": "NovelAI", "leonardo.ai": "Leonardo.Ai", "ideogram": "Ideogram", "imagen": "Google Imagen",
    "gemini": "Google Gemini", "black forest labs": "FLUX (Black Forest Labs)", "krea": "Krea",
    "photoshop": "Adobe Photoshop", "lightroom": "Adobe Lightroom", "camera raw": "Adobe Camera Raw",
    "snapseed": "Snapseed", "canva": "Canva", "capcut": "CapCut", "picsart": "Picsart", "gimp": "GIMP",
    "affinity": "Affinity", "procreate": "Procreate", "illustrator": "Adobe Illustrator",
    "after effects": "Adobe After Effects", "premiere": "Adobe Premiere", "davinci": "DaVinci Resolve",
    "blender": "Blender", "ezgif": "ezgif", "giphy": "GIPHY", "screentogif": "ScreenToGif",
}
_EXIF_KEEP = {
    "Make", "Model", "Software", "DateTime", "DateTimeOriginal", "Artist", "LensModel", "FocalLength",
    "FocalLengthIn35mmFilm", "FNumber", "ExposureTime", "ISOSpeedRatings", "PhotographicSensitivity",
    "Flash", "WhiteBalance", "ExposureBiasValue", "ExposureProgram", "MeteringMode", "ImageDescription",
    "UserComment", "ColorSpace", "BodySerialNumber",
}
_TEXT_KEYS = ("parameters", "prompt", "workflow", "Comment", "comment", "Description", "Software",
              "Title", "Author", "Source", "Generation time", "dream", "sd-metadata", "invokeai_metadata")


def _jsonable(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace").strip("\x00")[:2000]
    try:
        f = float(v)
        return round(f, 4) if not float(f).is_integer() else int(f)
    except (TypeError, ValueError):
        return str(v)[:2000]


def read_exif(img: Image.Image) -> dict:
    out = {}
    try:
        exif = img.getexif()
    except Exception:
        return out
    items = dict(exif.items())
    try:
        items.update(exif.get_ifd(0x8769).items())  # Exif sub-IFD (lens, exposure...)
    except Exception:
        pass
    for tag, value in items.items():
        name = ExifTags.TAGS.get(tag, str(tag))
        if name in _EXIF_KEEP:
            out[name] = _jsonable(value)
    return out


def read_embedded_text(img: Image.Image) -> dict:
    """PNG text chunks / WEBP & JPEG comments - AI generators often store their prompt here."""
    out = {}
    for key in _TEXT_KEYS:
        if key in img.info and img.info[key]:
            value = img.info[key]
            text = value if isinstance(value, str) else str(_jsonable(value))
            out[key] = text[:6000 if key in ("parameters", "prompt") else 3000]
    return out


def read_xmp(img: Image.Image, raw: bytes) -> dict:
    xmp = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp") or b""
    if isinstance(xmp, bytes):
        xmp = xmp.decode("utf-8", errors="replace")
    if not xmp:
        m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", raw, re.S)
        xmp = m.group(0).decode("utf-8", errors="replace") if m else ""
    if not xmp:
        return {}
    out: dict = {}
    tools = set(re.findall(r'(?:CreatorTool|softwareAgent)\s*=\s*"([^"]+)"', xmp))
    tools |= set(re.findall(r"<(?:xmp:CreatorTool|stEvt:softwareAgent)>([^<]+)<", xmp))
    if tools:
        out["software_history"] = sorted(tools)[:15]
    src = re.findall(r"DigitalSourceType[^a-zA-Z]+(?:[^\"<]*/)?([A-Za-z]+)", xmp)
    if src:
        out["digital_source_type"] = sorted(set(src))
    crs = dict(re.findall(r'crs:([A-Za-z0-9]+)\s*=\s*"([^"]{1,40})"', xmp))
    if crs:  # Lightroom / Camera Raw develop settings: real editing numbers
        keep = [k for k in crs if not k.lower().startswith(("version", "processversion", "rawfilename",
                                                            "hasSettings".lower(), "alreadyapplied"))]
        out["camera_raw_settings"] = {k: crs[k] for k in keep[:45]}
    return out


def provenance_hints(raw: bytes, texts: list[str]) -> list[str]:
    haystack = (raw[:524288] + raw[-262144:]).lower()
    joined = " ".join(texts).lower().encode("utf-8", errors="ignore")
    found = []
    for marker, label in _MARKERS.items():
        m = marker.encode("utf-8")
        if m in joined or m in haystack:
            if label not in found:
                found.append(label)
    return found


def palette(img: Image.Image, n: int = 8) -> list[dict]:
    small = img.convert("RGB").copy()
    small.thumbnail((256, 256))
    q = small.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    out = []
    for count, idx in counts[:n]:
        r, g, b = pal[idx * 3: idx * 3 + 3]
        out.append({"hex": f"#{r:02X}{g:02X}{b:02X}", "pct": round(100 * count / total, 1)})
    return out


def _laplacian_var(gray: np.ndarray) -> float:
    g = gray.astype(np.float32)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def visual_stats(img: Image.Image) -> dict:
    rgb = img.convert("RGB").copy()
    rgb.thumbnail((512, 512))
    arr = np.asarray(rgb).astype(np.float32)
    lum = 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]
    hsv = np.asarray(rgb.convert("HSV")).astype(np.float32)
    h, w = lum.shape
    cy, cx = h // 4, w // 4
    centre = lum[cy:h - cy, cx:w - cx]
    corners = np.concatenate([lum[:cy, :cx].ravel(), lum[:cy, -cx:].ravel(),
                              lum[-cy:, :cx].ravel(), lum[-cy:, -cx:].ravel()]) if cy and cx else centre.ravel()
    sharp_c = _laplacian_var(centre) if centre.size > 9 else 0.0
    sharp_all = _laplacian_var(lum)
    sharp_edge = _laplacian_var(np.vstack([lum[:cy], lum[-cy:]])) if cy > 2 else sharp_all
    mean_l = float(lum.mean())
    warmth = float(arr[..., 0].mean() - arr[..., 2].mean())
    left, right = float(lum[:, : w // 2].mean()), float(lum[:, w // 2:].mean())
    top, bottom = float(lum[: h // 2].mean()), float(lum[h // 2:].mean())
    return {
        "brightness_mean": round(mean_l / 255, 3),
        "contrast_std": round(float(lum.std()) / 255, 3),
        "shadows_p5": round(float(np.percentile(lum, 5)) / 255, 3),
        "highlights_p95": round(float(np.percentile(lum, 95)) / 255, 3),
        "clipped_black_pct": round(100 * float((lum < 4).mean()), 2),
        "clipped_white_pct": round(100 * float((lum > 251).mean()), 2),
        "saturation_mean": round(float(hsv[..., 1].mean()) / 255, 3),
        "color_temperature": "warm" if warmth > 12 else "cool" if warmth < -12 else "neutral",
        "warmth_r_minus_b": round(warmth, 1),
        "vignette_ratio_corners_vs_centre": round(float(corners.mean()) / (float(centre.mean()) + 1e-6), 3),
        "sharpness_laplacian_var": round(sharp_all, 1),
        "centre_vs_edge_sharpness": round(sharp_c / (sharp_edge + 1e-6), 2),
        "light_bias": {
            "left_minus_right": round((left - right) / 255, 3),
            "top_minus_bottom": round((top - bottom) / 255, 3),
        },
        "palette": palette(rgb, 8),
    }


def to_jpeg(img: Image.Image, max_side: int, quality: int = 88) -> bytes:
    im = img
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (128, 128, 128, 255))
        bg.alpha_composite(im)
        im = bg
    im = im.convert("RGB")
    if max(im.size) > max_side:
        im = im.copy()
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _open_checked(path: Path) -> Image.Image:
    try:
        with Image.open(path) as probe_img:
            probe_img.verify()
        img = Image.open(path)
        img.load()
        return img
    except UnidentifiedImageError:
        raise MediaError("The file is not a readable image (unknown or corrupted format).")
    except Image.DecompressionBombError:
        raise MediaError("The image is too large to analyze safely (over 400 megapixels).")
    except (OSError, SyntaxError, ValueError) as exc:
        raise MediaError(f"The image appears to be corrupted or truncated ({exc}).")


def analyze_image(path: Path, settings: Settings, progress: ProgressFn, cancel: CancelFn):
    """Returns (data, images) where images is a list of (label, jpeg_bytes)."""
    progress(5, "Reading image")
    img = _open_checked(path)
    raw = path.read_bytes()
    if getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1:
        return analyze_animation(img, path, raw, settings, progress, cancel)

    exif = read_exif(img)
    texts = read_embedded_text(img)
    xmp = read_xmp(img, raw)
    upright = ImageOps.exif_transpose(img)
    w, h = upright.size
    check_cancel(cancel)
    progress(15, "Measuring colour, lighting and sharpness")
    stats = visual_stats(upright)
    hints = provenance_hints(raw, [str(v) for v in (*exif.values(), *texts.values())] + [str(xmp)])
    data = {
        "kind": "image",
        "metadata": {
            "format": img.format, "width": w, "height": h, "aspect_ratio": aspect_label(w, h),
            "megapixels": round(w * h / 1e6, 2), "mode": img.mode,
            "has_transparency": img.mode in ("RGBA", "LA") or "transparency" in img.info,
            "dpi": _jsonable(img.info.get("dpi")) if img.info.get("dpi") else None,
            "icc_profile": bool(img.info.get("icc_profile")),
        },
        "exif": exif,
        "embedded_text": texts,
        "xmp": xmp,
        "provenance_hints": hints,
        "visual": stats,
    }
    progress(25, "Preparing image for the vision model")
    images = [(f"The full image ({w}x{h}, {aspect_label(w, h)})", to_jpeg(upright, 1568, 90))]
    if max(w, h) >= 2400:  # extra centre crop so fine texture / text is legible
        cw, ch = w // 2, h // 2
        crop = upright.crop((w // 4, h // 4, w // 4 + cw, h // 4 + ch))
        images.append(("Centre crop at higher detail (middle 50% of the image)", to_jpeg(crop, 1200, 90)))
    return data, images


def analyze_animation(img: Image.Image, path: Path, raw: bytes, settings: Settings,
                      progress: ProgressFn, cancel: CancelFn):
    progress(5, "Reading animation frames")
    durations, smalls, times = [], [], []
    t = 0.0
    total = min(getattr(img, "n_frames", 1), 3000)
    for i, frame in enumerate(ImageSequence.Iterator(img)):
        if i >= total:
            break
        if i % 25 == 0:
            check_cancel(cancel)
            progress(5 + 20 * i / total, f"Reading animation frames ({i}/{total})")
        d = frame.info.get("duration", 100) or 100
        d = 100 if d < 20 else d  # browsers clamp tiny GIF delays to 100 ms
        times.append(t)
        durations.append(d)
        t += d / 1000
        g = frame.convert("L").resize((192, 108), Image.BILINEAR)
        smalls.append(np.asarray(g, dtype=np.float32))
    total_s = t
    w, h = img.size
    diffs = [float(np.abs(b - a).mean()) / 255 for a, b in zip(smalls, smalls[1:])]
    cut_idx = [i + 1 for i, d in enumerate(diffs) if d > 0.12]
    unique = 1 + sum(1 for d in diffs if d > 0.004)
    bounds = [0, *cut_idx, len(smalls)]
    segments = []
    eff_fps = len(smalls) / total_s if total_s else 10
    for a, b in zip(bounds, bounds[1:]):
        if b - a < 1:
            continue
        seg_frames = smalls[a:b][:: max(1, (b - a) // 30)]
        segments.append({
            "start": round(times[a], 2), "end": round(times[b - 1] + durations[b - 1] / 1000, 2),
            "frames": b - a,
            "camera_motion": summarize_motion(seg_frames, eff_fps / max(1, (b - a) // 30)),
        })

    limit = min(settings.max_frames, 24)
    chosen = {0, len(smalls) - 1, *cut_idx}
    step = max(1, len(smalls) // max(1, limit - len(chosen)))
    chosen |= set(range(0, len(smalls), step))
    chosen = sorted(chosen)
    if len(chosen) > limit:
        keep = np.linspace(0, len(chosen) - 1, limit).round().astype(int)
        chosen = sorted({chosen[k] for k in keep} | {0})[:limit]

    progress(28, "Extracting key animation frames")
    images = []
    first = None
    for n, idx in enumerate(chosen):
        check_cancel(cancel)
        img.seek(idx)
        frame = img.convert("RGBA")
        if first is None:
            first = frame.copy()
        images.append((f"Frame {idx + 1}/{len(smalls)} at t={times[idx]:.2f}s", to_jpeg(frame, 768, 85)))
    img.seek(0)

    texts = read_embedded_text(img)
    xmp = read_xmp(img, raw)
    exif = read_exif(img)
    hints = provenance_hints(raw, [str(v) for v in (*exif.values(), *texts.values())] + [str(xmp)])
    loop_diff = float(np.abs(smalls[-1] - smalls[0]).mean()) / 255 if len(smalls) > 1 else 0
    data = {
        "kind": "animation",
        "metadata": {
            "format": img.format, "width": w, "height": h, "aspect_ratio": aspect_label(w, h),
            "frame_count": len(smalls), "duration_s": round(total_s, 2),
            "duration": fmt_time(total_s), "effective_fps": round(eff_fps, 2),
            "loop": img.info.get("loop", "n/a (0 = infinite)"),
            "frame_delay_ms": {"min": min(durations), "max": max(durations),
                               "median": float(np.median(durations))},
            "unique_frame_ratio": round(unique / len(smalls), 3),
            "seamless_loop": loop_diff < 0.03,
            "palette_mode": img.mode,
        },
        "hard_cuts_at_s": [round(times[i], 2) for i in cut_idx],
        "segments": segments,
        "embedded_text": texts,
        "xmp": xmp,
        "exif": exif,
        "provenance_hints": hints,
        "visual": visual_stats(first) if first is not None else {},
    }
    return data, images

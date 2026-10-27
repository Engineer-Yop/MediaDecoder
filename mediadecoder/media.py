"""File-type detection, FFmpeg discovery, process control and stream probing."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

SUPPORTED = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image",
    ".mp4": "video", ".mov": "video", ".webm": "video", ".mkv": "video",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
}

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


class MediaError(Exception):
    """An error whose message is shown to the user as-is."""


class Cancelled(Exception):
    pass


def check_cancel(cancel: Optional[CancelFn]) -> None:
    if cancel and cancel():
        raise Cancelled()


def ffmpeg_exe() -> str:
    """FFmpeg bundled with imageio-ffmpeg, else one on PATH."""
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    raise MediaError("FFmpeg was not found. Reinstall the app, or install FFmpeg and add it to PATH.")


def run_ffmpeg(
    args: list[str],
    cancel: Optional[CancelFn] = None,
    on_stderr_line: Optional[Callable[[str], None]] = None,
    want_stdout: bool = True,
) -> tuple[int, bytes, str]:
    """Run ffmpeg with `args`. Reads stdout/stderr on threads so large outputs never deadlock.

    Returns (returncode, stdout_bytes, stderr_text). Raises Cancelled if `cancel()` turns true.
    """
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", *args]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if want_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    out_chunks: list[bytes] = []
    err_lines: list[str] = []

    def read_out():
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
            out_chunks.append(chunk)

    def read_err():
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            err_lines.append(line)
            if on_stderr_line:
                on_stderr_line(line)

    threads = [threading.Thread(target=read_err, daemon=True)]
    if want_stdout:
        threads.append(threading.Thread(target=read_out, daemon=True))
    for t in threads:
        t.start()
    while True:
        try:
            proc.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel and cancel():
                proc.kill()
                proc.wait()
                raise Cancelled()
    for t in threads:
        t.join()
    return proc.returncode, b"".join(out_chunks), "\n".join(err_lines)


def detect_kind(path: Path) -> str:
    kind = SUPPORTED.get(path.suffix.lower())
    if not kind:
        exts = ", ".join(sorted(e.lstrip(".").upper() for e in SUPPORTED))
        raise MediaError(f"Unsupported file type '{path.suffix or '(none)'}'. Supported: {exts}.")
    return kind


def validate_file(path: Path, max_mb: int) -> None:
    if not path.exists() or not path.is_file():
        raise MediaError(f"File not found: {path}")
    size = path.stat().st_size
    if size == 0:
        raise MediaError("The file is empty (0 bytes) and cannot be analyzed.")
    if size > max_mb * 1024 * 1024:
        raise MediaError(
            f"The file is too large ({size / 1024 / 1024:,.0f} MB). The limit is {max_mb:,} MB "
            "(change it in Settings)."
        )


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_BITRATE = re.compile(r"bitrate:\s*(\d+)\s*kb/s")
_STREAM = re.compile(r"^\s*Stream #\d+:(\d+)[^:]*:\s*(Video|Audio|Subtitle|Data|Attachment):\s*(.*)$")
_DIMS = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
_FPS = re.compile(r"([\d.]+)\s*fps")
_TBR = re.compile(r"([\d.]+)\s*tbr")
_HZ = re.compile(r"(\d+)\s*Hz,\s*([^,]+)")
_KBS = re.compile(r"(\d+)\s*kb/s")
_ROTATION = re.compile(r"rotation of\s*(-?[\d.]+)\s*degrees")
_META = re.compile(r"^\s{4}([A-Za-z0-9_.\-:]+)\s*:\s(.*)$")


def probe(path: Path) -> dict:
    """Parse `ffmpeg -i` output (imageio-ffmpeg ships ffmpeg but not ffprobe)."""
    _, _, err = run_ffmpeg(["-i", str(path)], want_stdout=False)
    if "Invalid data found" in err or "moov atom not found" in err:
        raise MediaError("The file appears to be corrupted or is not a valid media file (FFmpeg could not read it).")
    if "Input #0" not in err:
        last = err.strip().splitlines()[-1] if err.strip() else "unknown error"
        raise MediaError(f"FFmpeg could not open the file: {last}")

    info: dict = {"container": None, "duration_s": None, "bitrate_kbps": None, "video": None,
                  "audio": None, "subtitles": [], "tags": {}}
    m = re.search(r"Input #0,\s*([^,]+(?:,[^,']+)*?),\s*from", err)
    if m:
        info["container"] = m.group(1).strip()
    m = _DURATION.search(err)
    if m:
        h, mi, s = m.groups()
        info["duration_s"] = int(h) * 3600 + int(mi) * 60 + float(s)
    m = _BITRATE.search(err)
    if m:
        info["bitrate_kbps"] = int(m.group(1))

    in_container_meta = False
    for line in err.splitlines():
        if line.startswith("  Metadata:") and info["video"] is None and info["audio"] is None:
            in_container_meta = True
            continue
        if in_container_meta:
            mm = _META.match(line)
            if mm:
                info["tags"][mm.group(1).strip()] = mm.group(2).strip()[:300]
                continue
            in_container_meta = False

        sm = _STREAM.match(line)
        if not sm:
            rm = _ROTATION.search(line)
            if rm and info["video"] is not None:
                info["video"]["rotation"] = float(rm.group(1))
            continue
        kind, desc = sm.group(2), sm.group(3)
        if kind == "Video" and info["video"] is None and "attached pic" not in desc:
            codec = desc.split(",")[0].split(" ")[0]
            parts = [p.strip() for p in desc.split(",")]
            dims = _DIMS.search(desc)
            fps = _FPS.search(desc) or _TBR.search(desc)
            kbs = _KBS.search(desc)
            info["video"] = {
                "codec": codec,
                "pix_fmt": parts[1].split("(")[0].strip() if len(parts) > 1 else None,
                "width": int(dims.group(1)) if dims else None,
                "height": int(dims.group(2)) if dims else None,
                "fps": float(fps.group(1)) if fps else None,
                "bitrate_kbps": int(kbs.group(1)) if kbs else None,
                "rotation": 0.0,
                "hdr": any(t in desc for t in ("bt2020", "smpte2084", "arib-std-b67")),
            }
        elif kind == "Audio" and info["audio"] is None:
            hz = _HZ.search(desc)
            kbs = _KBS.search(desc)
            info["audio"] = {
                "codec": desc.split(",")[0].split(" ")[0],
                "sample_rate": int(hz.group(1)) if hz else None,
                "channels": hz.group(2).strip() if hz else None,
                "bitrate_kbps": int(kbs.group(1)) if kbs else None,
            }
        elif kind == "Subtitle":
            info["subtitles"].append(desc.split(",")[0].split(" ")[0])

    v = info["video"]
    if v and v.get("width") and abs(v.get("rotation", 0)) in (90.0, 270.0):
        v["width"], v["height"] = v["height"], v["width"]  # ffmpeg auto-rotates on decode
    return info


def aspect_label(w: int, h: int) -> str:
    if not w or not h:
        return "unknown"
    ratio = w / h
    common = {"1:1": 1.0, "4:5": 0.8, "3:4": 0.75, "2:3": 2 / 3, "9:16": 9 / 16, "16:9": 16 / 9,
              "3:2": 1.5, "4:3": 4 / 3, "5:4": 1.25, "21:9": 21 / 9, "1.85:1": 1.85, "2.39:1": 2.39}
    name, value = min(common.items(), key=lambda kv: abs(kv[1] - ratio))
    return name if abs(value - ratio) / value < 0.03 else f"{ratio:.3f}:1"


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    m, s = divmod(seconds, 60)
    return f"{int(m):02d}:{s:05.2f}"

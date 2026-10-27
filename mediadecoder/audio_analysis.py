"""Audio (standalone or a video's soundtrack): loudness, tempo, spectrum, voice pitch,
a spectrogram image for the vision model, and local Whisper transcription."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .config import Settings, config_dir
from .media import CancelFn, Cancelled, MediaError, ProgressFn, check_cancel, fmt_time, run_ffmpeg

SR = 16000
N_FFT = 1024
HOP = 512
N_MELS = 64
_whisper_cache: dict = {}


def decode_audio(path: Path, cap: float, cancel: CancelFn) -> np.ndarray:
    code, out, err = run_ffmpeg(["-i", str(path), "-t", f"{cap:.3f}", "-map", "0:a:0", "-vn", "-sn",
                                 "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], cancel=cancel)
    if not out:
        tail = err.strip().splitlines()[-1] if err.strip() else "no audio samples"
        raise MediaError(f"Could not decode the audio stream - the file may be corrupted ({tail}).")
    return np.frombuffer(out[: len(out) // 4 * 4], dtype=np.float32).copy()


def _mel_filters() -> np.ndarray:
    def hz_to_mel(f):
        return 2595 * np.log10(1 + f / 700)

    def mel_to_hz(m):
        return 700 * (10 ** (m / 2595) - 1)

    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    pts = mel_to_hz(np.linspace(hz_to_mel(30), hz_to_mel(SR / 2), N_MELS + 2))
    fb = np.zeros((len(freqs), N_MELS), dtype=np.float32)
    for i in range(N_MELS):
        lo, c, hi = pts[i], pts[i + 1], pts[i + 2]
        up = (freqs - lo) / (c - lo)
        down = (hi - freqs) / (hi - c)
        fb[:, i] = np.maximum(0, np.minimum(up, down))
    fb /= fb.sum(axis=0, keepdims=True) + 1e-9
    return fb


def _features(x: np.ndarray, cancel: CancelFn):
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::HOP]
    win = np.hanning(N_FFT).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    fb = _mel_filters()
    mel, rms, centroid, bands = [], [], [], []
    low_m, high_m = freqs < 250, freqs > 4000
    for start in range(0, len(frames), 4096):
        check_cancel(cancel)
        chunk = frames[start:start + 4096].astype(np.float32)
        rms.append(np.sqrt((chunk ** 2).mean(axis=1)))
        power = np.abs(np.fft.rfft(chunk * win, axis=1)) ** 2
        mel.append(power @ fb)
        total = power.sum(axis=1) + 1e-12
        centroid.append((power * freqs).sum(axis=1) / total)
        bands.append(np.stack([power[:, low_m].sum(1) / total, power[:, high_m].sum(1) / total], axis=1))
    return (np.concatenate(mel), np.concatenate(rms), np.concatenate(centroid), np.concatenate(bands))


def _tempo(flux: np.ndarray, frame_rate: float) -> tuple[float | None, float]:
    if len(flux) < frame_rate * 6:
        return None, 0.0
    env = flux - flux.mean()
    n = 1 << int(np.ceil(np.log2(2 * len(env))))
    spec = np.fft.rfft(env, n)
    ac = np.fft.irfft(spec * np.conj(spec), n)[: len(env)]
    if ac[0] <= 0:
        return None, 0.0
    ac /= ac[0]
    lags = np.arange(len(ac))
    lo, hi = int(60 * frame_rate / 200), int(60 * frame_rate / 60) + 1
    bpm = 60 * frame_rate / np.maximum(lags[lo:hi], 1)
    weight = np.exp(-0.5 * (np.log2(bpm / 120) / 0.9) ** 2)  # mild preference for 80-180 BPM
    k = int(np.argmax(ac[lo:hi] * weight)) + lo
    if 1 <= k < len(ac) - 1:  # parabolic refinement of the peak lag
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        denom = a - 2 * b + c
        k_ref = k + (0.5 * (a - c) / denom if denom else 0)
    else:
        k_ref = k
    return round(60 * frame_rate / k_ref, 1), round(float(ac[k]), 3)


def _onsets(flux: np.ndarray, frame_rate: float) -> list[float]:
    if len(flux) < 3:
        return []
    thr = flux.mean() + 1.5 * flux.std()
    min_gap = int(0.1 * frame_rate)
    out, last = [], -min_gap
    for i in range(1, len(flux) - 1):
        if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1] and i - last >= min_gap:
            out.append(round(i / frame_rate, 3))
            last = i
    return out


def _pitch(x: np.ndarray, segments: list[dict]) -> float | None:
    win = 640
    spans = [(int(s["start"] * SR), int(s["end"] * SR)) for s in segments] or [(0, len(x))]
    starts = [i for a, b in spans for i in range(a, max(a, b - win), win)]
    if not starts:
        return None
    starts = starts[:: max(1, len(starts) // 600)]
    lo, hi = SR // 400, SR // 70
    f0s = []
    for s in starts:
        fr = x[s:s + win]
        if len(fr) < win or np.sqrt((fr ** 2).mean()) < 0.01:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, "full")[win - 1:]
        if ac[0] <= 0:
            continue
        ac /= ac[0]
        k = int(np.argmax(ac[lo:hi])) + lo
        if ac[k] > 0.45:
            f0s.append(SR / k)
    return round(float(np.median(f0s)), 1) if len(f0s) >= 10 else None


def _spectrogram_jpeg(mel_db: np.ndarray, duration: float) -> bytes:
    cols = min(1400, len(mel_db))
    if cols < 2:
        cols = 2
    idx = np.linspace(0, len(mel_db), cols + 1).astype(int)
    pooled = np.stack([mel_db[a:max(a + 1, b)].mean(axis=0) for a, b in zip(idx, idx[1:])])
    top = pooled.max()
    norm = np.clip((pooled - (top - 80)) / 80, 0, 1).T[::-1]  # rows: high freq at top
    stops = np.array([[0, 0, 4], [60, 15, 110], [180, 50, 120], [250, 130, 60], [252, 250, 190]], np.float32)
    pos = norm * (len(stops) - 1)
    i0 = np.clip(pos.astype(int), 0, len(stops) - 2)
    frac = (pos - i0)[..., None]
    rgb = (stops[i0] * (1 - frac) + stops[i0 + 1] * frac).astype(np.uint8)
    spec = Image.fromarray(rgb).resize((cols, 256), Image.NEAREST)
    canvas = Image.new("RGB", (cols + 60, 256 + 24), (255, 255, 255))
    canvas.paste(spec, (60, 0))
    draw = ImageDraw.Draw(canvas)
    for hz, y in (("8k", 2), ("2k", 95), ("500", 175), ("30", 244)):
        draw.text((4, y), f"{hz} Hz", fill=(0, 0, 0))
    for k in range(11):
        x = 60 + int(k * (cols - 1) / 10)
        draw.line([(x, 256), (x, 262)], fill=(0, 0, 0))
        draw.text((max(60, x - 14), 264), fmt_time(duration * k / 10)[:5], fill=(0, 0, 0))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def transcribe(x: np.ndarray, model_size: str, progress: ProgressFn, cancel: CancelFn, p0: float, p1: float):
    from faster_whisper import WhisperModel  # heavy import, only when needed

    if model_size not in _whisper_cache:
        progress(p0, f"Loading Whisper '{model_size}' model (first run downloads it, ~75-1500 MB)")
        _whisper_cache[model_size] = WhisperModel(
            model_size, device="cpu", compute_type="int8",
            download_root=str(config_dir() / "whisper-models"))
    model = _whisper_cache[model_size]
    segments_iter, info = model.transcribe(x, beam_size=1, vad_filter=True, condition_on_previous_text=False)
    total = len(x) / SR or 1
    segments = []
    for seg in segments_iter:
        check_cancel(cancel)
        segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()})
        progress(p0 + (p1 - p0) * min(1.0, seg.end / total), f"Transcribing speech ({fmt_time(seg.end)} / {fmt_time(total)})")
    return {
        "language": info.language,
        "language_probability": round(float(info.language_probability), 3),
        "segments": segments,
        "text": " ".join(s["text"] for s in segments).strip(),
    }


def analyze_audio(path: Path, info: dict, settings: Settings, progress: ProgressFn, cancel: CancelFn,
                  p0: float = 5, p1: float = 70, standalone: bool = True):
    duration = info.get("duration_s") or 0.0
    cap_limit = settings.max_duration_min * 60
    cap = min(duration, cap_limit) if duration else cap_limit
    warnings = []
    if standalone and duration > cap_limit:
        warnings.append(f"Audio is {fmt_time(duration)} long; analysis covers the first {fmt_time(cap)}.")

    span = p1 - p0
    progress(p0, "Decoding audio")
    x = decode_audio(path, cap, cancel)
    length = len(x) / SR
    progress(p0 + span * 0.1, "Measuring loudness, tempo and spectrum")
    mel, rms, centroid, bands = _features(x, cancel)
    frame_rate = SR / HOP
    db = 20 * np.log10(rms + 1e-9)
    mel_db = 10 * np.log10(mel + 1e-10)
    flux = np.maximum(0, np.diff(mel_db, axis=0)).sum(axis=1)
    flux = np.convolve(flux, np.ones(3) / 3, mode="same")
    bpm, bpm_conf = _tempo(flux, frame_rate)
    onsets = _onsets(flux, frame_rate)
    voiced = db > -50
    per2 = max(1, int(2 * frame_rate))
    envelope = [round(float(20 * np.log10(np.sqrt((rms[i:i + per2] ** 2).mean()) + 1e-9)), 1)
                for i in range(0, len(rms), per2)]
    jumps = [round(i * 2.0, 1) for i in range(1, len(envelope)) if envelope[i] - envelope[i - 1] > 9]
    peak = float(np.abs(x).max()) if len(x) else 0.0

    features = {
        "analyzed_s": round(length, 2),
        "sample_rate_original": (info.get("audio") or {}).get("sample_rate"),
        "channels_original": (info.get("audio") or {}).get("channels"),
        "codec": (info.get("audio") or {}).get("codec"),
        "bitrate_kbps": (info.get("audio") or {}).get("bitrate_kbps"),
        "loudness_rms_dbfs": round(float(20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-9)), 1) if len(x) else None,
        "peak_dbfs": round(float(20 * np.log10(peak + 1e-9)), 1),
        "dynamic_range_db_p95_minus_p10": round(float(np.percentile(db[voiced], 95) - np.percentile(db[voiced], 10)), 1) if voiced.any() else 0,
        "silence_ratio": round(float((~voiced).mean()), 3),
        "tempo_bpm_estimate": bpm,
        "tempo_confidence_0_1": bpm_conf,
        "strong_onsets_per_s": round(len(onsets) / length, 2) if length else 0,
        "spectral_centroid_hz_mean": round(float(centroid[voiced].mean()), 0) if voiced.any() else None,
        "energy_share_below_250hz": round(float(bands[voiced, 0].mean()), 3) if voiced.any() else None,
        "energy_share_above_4khz": round(float(bands[voiced, 1].mean()), 3) if voiced.any() else None,
        "loudness_envelope_db_every_2s": envelope[:400],
        "sudden_loudness_jumps_at_s": jumps[:40],
        "note": "Audio decoded at 16 kHz mono; content above 8 kHz is not measured.",
    }

    transcript = None
    if settings.transcribe:
        try:
            transcript = transcribe(x, settings.whisper_model, progress, cancel, p0 + span * 0.25, p1 - 2)
            speech_s = sum(s["end"] - s["start"] for s in transcript["segments"])
            words = len(transcript["text"].split())
            features["speech_ratio"] = round(speech_s / length, 3) if length else 0
            features["speaking_rate_wpm"] = round(words / speech_s * 60, 0) if speech_s > 3 else None
        except Cancelled:
            raise
        except Exception as exc:  # transcription is optional: degrade, don't fail the whole run
            warnings.append(f"Speech transcription failed ({type(exc).__name__}: {exc}). "
                            "Check your internet connection for the first-run model download.")
    check_cancel(cancel)
    features["voice_median_pitch_hz"] = _pitch(x, transcript["segments"] if transcript else [])

    images = [(f"Mel spectrogram of the audio (0 to {fmt_time(length)}, 30 Hz-8 kHz, brighter = louder)",
               _spectrogram_jpeg(mel_db, length))]
    data = {"audio_features": features, "transcript": transcript, "onset_times_s": onsets[:600],
            "warnings": warnings}
    if standalone:
        data["kind"] = "audio"
        data["metadata"] = {"container": info.get("container"), "duration_s": round(duration, 3),
                            "duration": fmt_time(duration), "container_tags": info.get("tags")}
    return data, images

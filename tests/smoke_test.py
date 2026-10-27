"""End-to-end check of the local analysis (no API call).

Generates test media with FFmpeg/Pillow (and Windows text-to-speech for a voice track),
runs the analyzer on each file and asserts the measurements make sense.

    python tests/smoke_test.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mediadecoder import export, pipeline  # noqa: E402
from mediadecoder.config import Settings  # noqa: E402
from mediadecoder.media import MediaError, run_ffmpeg  # noqa: E402
from mediadecoder.motion import summarize_motion  # noqa: E402

OUT = ROOT / "tests" / "media"
OUT.mkdir(parents=True, exist_ok=True)


def ff(*args):
    code, _, err = run_ffmpeg(["-y", *args], want_stdout=False)
    assert code == 0, err[-1500:]


def make_media():
    # A detailed still with an embedded "generator" prompt chunk.
    img = Image.new("RGB", (1600, 1000))
    px = np.zeros((1000, 1600, 3), np.uint8)
    px[..., 0] = np.linspace(255, 40, 1600)[None, :]
    px[..., 1] = np.linspace(120, 20, 1000)[:, None]
    px[..., 2] = 60
    img = Image.fromarray(px)
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(1)
    for _ in range(120):
        x, y, r = rng.integers(0, 1600), rng.integers(0, 1000), rng.integers(8, 60)
        d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(int(c) for c in rng.integers(0, 255, 3)))
    d.text((60, 60), "SUMMER SALE", fill=(255, 255, 255))
    meta = PngImagePlugin.PngInfo()
    meta.add_text("parameters", "sunset gradient with confetti, poster\nSteps: 30, Sampler: DPM++ 2M, CFG scale: 6, Seed: 42")
    img.save(OUT / "still.png", pnginfo=meta)
    img.resize((3200, 2000)).save(OUT / "big.jpg", quality=92)

    # Three scenes: pan right (content moves left), a static bars shot that dips to black, a zoom in.
    big = str(OUT / "big.jpg")
    ff("-loop", "1", "-t", "3", "-i", big, "-vf", "crop=1280:720:'40+t*300':400,fps=30,format=yuv420p",
       str(OUT / "s1.mp4"))
    ff("-f", "lavfi", "-i", "smptehdbars=s=1280x720:r=30:d=2.5", "-vf", "fade=out:st=2.0:d=0.5,format=yuv420p",
       str(OUT / "s2.mp4"))
    ff("-loop", "1", "-t", "3", "-i", big, "-vf",
       "scale=1280:720,zoompan=z='1+0.004*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1280x720:fps=30,format=yuv420p",
       "-frames:v", "90", str(OUT / "s3.mp4"))
    ff("-i", str(OUT / "s1.mp4"), "-i", str(OUT / "s2.mp4"), "-i", str(OUT / "s3.mp4"),
       "-f", "lavfi", "-t", "8.5", "-i", "aevalsrc='0.6*sin(2*PI*660*t)*lt(mod(t,0.5),0.06)':s=44100",
       "-filter_complex", "[0:v]setsar=1[a];[1:v]setsar=1[b];[2:v]setsar=1[c];[a][b][c]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(OUT / "edit.mp4"))
    ff("-i", str(OUT / "s1.mp4"), "-vf", "fps=10,scale=320:-1", "-t", "2", str(OUT / "anim.gif"))

    # Speech via Windows TTS, mixed over the beat as an MP3.
    wav = OUT / "speech.wav"
    ps = (f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('Welcome back to the channel. Today we are building a "
          f"tiny robot out of cardboard and two motors.'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    ff("-i", str(wav), "-f", "lavfi", "-t", "9", "-i", "aevalsrc='0.15*sin(2*PI*110*t)*lt(mod(t,0.5),0.1)':s=44100",
       "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest", "-c:a", "libmp3lame", "-q:a", "4",
       str(OUT / "voice.mp3"))

    (OUT / "broken.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40)
    (OUT / "notes.txt").write_text("not media")


def check_motion_direction():
    rng = np.random.default_rng(0)
    base = rng.random((300, 500)).astype(np.float32) * 255
    from PIL import ImageFilter
    base = np.asarray(Image.fromarray(base.astype(np.uint8)).filter(ImageFilter.GaussianBlur(2)), np.float32)
    frames = [base[50:158, 100 + 6 * i: 292 + 6 * i] for i in range(8)]  # window moves right = pan right
    m = summarize_motion(frames, 4)
    assert "pan right" in m["label"], m
    print("motion direction OK:", m["label"])


def run(name, **kw):
    s = Settings()
    s.transcribe = kw.pop("transcribe", True)
    t = time.time()
    res = pipeline.run(OUT / name, s, lambda p, m: None, lambda: False, analyze_only=True)
    print(f"\n== {name}: {res['kind']} in {time.time() - t:.1f}s, {len(res['images_sent'])} image(s)")
    return res["analysis"]


def main():
    make_media()
    check_motion_direction()

    a = run("still.png")
    assert a["kind"] == "image" and a["metadata"]["aspect_ratio"] == "16:10" or a["metadata"]["width"] == 1600
    assert "Steps: 30" in a["embedded_text"]["parameters"]
    print("palette:", [p["hex"] for p in a["visual"]["palette"][:5]], "temp:", a["visual"]["color_temperature"])

    a = run("anim.gif")
    assert a["kind"] == "animation" and a["metadata"]["frame_count"] >= 15, a["metadata"]
    print("gif:", a["metadata"]["frame_count"], "frames;", a["segments"][0]["camera_motion"]["label"])

    a = run("edit.mp4", transcribe=False)
    assert a["kind"] == "video"
    for sc in a["scenes"]:
        print(f"  scene {sc['index']}: {sc['start']:.2f}-{sc['end']:.2f}s  in={sc['transition_in']:<38} motion={sc['camera_motion']['label']}")
    print("  pacing:", a["pacing"])
    print("  tempo:", a["audio_features"]["tempo_bpm_estimate"], "BPM")
    assert len(a["scenes"]) == 3, a["transitions"]
    assert abs(a["scenes"][1]["start"] - 3.0) < 0.15 and abs(a["scenes"][2]["start"] - 5.5) < 0.6
    assert "pan right" in a["scenes"][0]["camera_motion"]["label"]
    assert "zoom in" in a["scenes"][2]["camera_motion"]["label"]
    assert abs(a["audio_features"]["tempo_bpm_estimate"] - 120) < 4

    a = run("voice.mp3")
    print("  transcript:", (a.get("transcript") or {}).get("text"), "| warnings:", a["warnings"])
    print("  pitch:", a["audio_features"]["voice_median_pitch_hz"], "Hz; tempo:", a["audio_features"]["tempo_bpm_estimate"])
    assert a["transcript"] and "robot" in a["transcript"]["text"].lower()

    for bad in ("broken.mp4", "notes.txt"):
        try:
            run(bad)
            raise AssertionError(f"{bad} should fail")
        except MediaError as exc:
            print(f"  {bad}: rejected -> {exc}")

    # Exporters with a minimal recipe.
    res = {"file": {"name": "x.png"}, "recipe": {
        "title": "T", "summary": "S", "likely_origin": "O", "confidence": "low", "style_keywords": ["a"],
        "steps": [{"number": 1, "title": "Gen", "produces": "img", "tool": "Midjourney", "tool_type": "ai_image",
                   "time_range": "", "prompt": "p --ar 16:9", "negative_prompt": "", "command": "ffmpeg -i a b",
                   "settings": "", "evidence": "e", "estimated": True}]}, "meta": {"model": "m"}}
    assert "STEP 1" in export.to_text(res) and "```powershell" in export.to_markdown(res)
    json.loads(export.to_json(res))
    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()

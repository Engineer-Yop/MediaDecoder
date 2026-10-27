# MediaDecoder

Drop in a finished image, GIF, video or audio file and get back the **estimated recipe** behind it:
numbered steps, each with a copy-ready AI prompt and/or a runnable command (FFmpeg, ImageMagick,
Real-ESRGAN), the tool it's meant for, and the evidence behind it. Export to `.txt`, `.md` or `.json`.

All analysis runs on your PC. The AI step uses **your Claude subscription** (Pro or Max) through
the Claude Code CLI, running Opus 5.5 by default. There is **no API key and no pay-as-you-go
credit**; each run counts toward your plan's normal usage limits. The only other network traffic
is a one-time Whisper model download, the first time the app transcribes speech.

## 1. Tech stack

| Piece | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Has the best media, audio and AI libraries, and packages into a single Windows program. |
| UI | PySide6 (Qt 6) | A native-feeling desktop window with drag-and-drop, worker threads and the clipboard, all built in. |
| Frame / audio extraction | FFmpeg via `imageio-ffmpeg` | The static `ffmpeg.exe` ships inside the app, so users install nothing extra. Used for scene scores, black detection, frame grabs and audio decoding. |
| Image analysis | Pillow + NumPy | Reads EXIF/XMP/PNG text chunks (AI generators store prompts there), measures the palette, tone, sharpness and vignette. |
| Camera motion | NumPy phase correlation (`motion.py`) | Measures pan, tilt, zoom and shake from low-res frames, so the prompts can name the actual camera move. |
| Transcription | `faster-whisper` (CTranslate2, int8 CPU) | Runs Whisper locally and fast, with no GPU needed. |
| Music features | NumPy STFT, mel bands, onset autocorrelation | Gives BPM, loudness, spectrum and onsets, plus a spectrogram image the vision model can read. |
| AI | Claude Code CLI (`claude -p`) with `claude-opus-5-5` | Runs on your Claude subscription with no credits. It accepts images over stream-json input and enforces the recipe's JSON schema (`--json-schema`). Tools are disabled, so the model can only analyze what it's sent. |
| Sign-in | `claude auth login` / `claude auth status` | Signs in once with your Claude account. The app stores no secrets, and it strips any `ANTHROPIC_API_KEY` from the environment so a stray key can never switch you onto paid billing. |
| Packaging | PyInstaller + Inno Setup 6 | Produces a normal `MediaDecoder.exe` plus a single `MediaDecoder-Setup-1.0.0.exe` installer with Start-menu and desktop shortcuts and an uninstaller. |

## 2. Project structure

```
MediaDecoder/
├─ main.py                     entry point (GUI, or --cli for headless runs)
├─ requirements.txt
├─ build.cmd                   builds the .exe (PyInstaller) and the installer (Inno Setup)
├─ build.ps1                   the same build as a PowerShell script
├─ installer.iss               Inno Setup script
├─ assets/make_icon.py         draws assets/icon.ico at build time
├─ mediadecoder/
│  ├─ config.py                settings (%APPDATA%\MediaDecoder\settings.json)
│  ├─ media.py                 file-type checks, size limit, FFmpeg runner (cancellable), stream probe
│  ├─ image_analysis.py        stills + animated GIF/WEBP: metadata, provenance hints, visual stats
│  ├─ video_analysis.py        scene/cut/fade detection, camera motion, pacing, frame sampling, subtitles
│  ├─ audio_analysis.py        loudness, BPM, onsets, spectrum, pitch, spectrogram, Whisper
│  ├─ motion.py                phase-correlation pan/tilt/zoom/shake estimation
│  ├─ ai.py                    finds claude.exe, checks sign-in, prompt + JSON schema, runs `claude -p`
│  ├─ pipeline.py              check sign-in -> validate -> analyze -> call Claude
│  ├─ export.py                .txt / .md / .json renderers
│  └─ ui.py                    main window, drop zone, step cards, settings dialog, worker thread
├─ samples/                    example outputs (one image, one video) in every export format
└─ tests/
   ├─ smoke_test.py            generates real test media and checks every analyzer (no API)
   ├─ mock_cli_test.py         drives the CLI engine with a fake claude (no usage) + renders the UI
   └─ fake_claude.py           the stand-in CLI used by mock_cli_test.py
```

## 3. Setup

**Requirements:** Windows 10 or 11 (64-bit), Python 3.12 (`winget install Python.Python.3.12`),
and a Claude Pro or Max subscription. FFmpeg is bundled through `imageio-ffmpeg`, so you don't need
to install it.

**One-time Claude setup.** Install the Claude Code CLI (PowerShell):

```powershell
irm https://claude.ai/install.ps1 | iex
```

Then sign in with the Claude account that has your subscription. Choose the Claude.ai
subscription option, not "API usage":

```bash
claude auth login
```

The app also finds the `claude.exe` that ships with the Claude desktop app, and its Settings
screen has a **Sign in** button that runs the same login for you.

> Keep the project in a **short path** such as `C:\Users\Geof\Desktop\ZZZ\MediaDecoder`. Windows' 260-character path
> limit breaks `pip` and PyInstaller inside deeply nested folders.

### Run in development

```bash
cd C:\Users\Geof\Desktop\ZZZ\MediaDecoder
```
```bash
py -3.12 -m venv .venv
```
```bash
.venv\Scripts\python -m pip install -r requirements.txt
```
```bash
.venv\Scripts\python main.py
```

Open **Settings** and check that "Claude account" shows ✔ Signed in. If it doesn't, click
**Sign in**, finish in the browser, then click **Check again**. After that, drop a file onto the
window or click the drop zone to browse.

Headless use (handy for scripting):

```bash
.venv\Scripts\python main.py --cli clip.mp4 --out clip_recipe.md
```
```bash
.venv\Scripts\python main.py --cli clip.mp4 --analyze-only --out clip_measurements.json
```

### Tests

```bash
.venv\Scripts\python tests\smoke_test.py
```
```bash
.venv\Scripts\python tests\mock_cli_test.py
```

The smoke test generates a PNG with an embedded prompt, a 3-scene MP4 (a pan, a static shot with a
fade, a zoom) with a 120 BPM click, a GIF, a TTS voice MP3 and a corrupt file. It then checks the
cuts, the camera-motion labels, the BPM, the transcript and the error messages. The first run
downloads the Whisper `base` model (about 145 MB).

### Build the .exe and installer

1. Optional, for the single-file installer: install Inno Setup 6 with `winget install JRSoftware.InnoSetup`.
2. Build: double-click `build.cmd`, or run it from a terminal. The first run creates `.venv` and
   installs the dependencies.

```bash
C:\Users\Geof\Desktop\ZZZ\MediaDecoder\build.cmd
```

`build.ps1` does the same thing. Windows blocks `.ps1` scripts unless you allow them, so
`build.cmd` is the easier option.

Output:
- `dist\MediaDecoder\MediaDecoder.exe` is the app itself (a folder of about 440 MB that you can zip and share).
- `installer\MediaDecoder-Setup-1.1.0.exe` is the installer, if Inno Setup is present.

The installer doesn't include Claude Code. Each PC that runs MediaDecoder needs the one-time
Claude setup above.

## 4. How the analysis works

| Media | Measured locally | Sent to Claude |
|---|---|---|
| Image | Size, aspect ratio, EXIF (camera, lens, exposure), XMP software history and Lightroom/Camera Raw sliders, PNG text chunks (A1111/ComfyUI prompts), C2PA/AI-source markers, 8-colour palette with hex values, brightness/contrast/clipping, saturation, warmth, light direction, vignette, centre-vs-edge sharpness (depth of field) | The image at up to 1568 px, plus a detail crop for large images |
| GIF / animated WEBP | Frame count, delays, effective fps, loop, unique-frame ratio, seamless-loop check, hard cuts, per-segment camera motion | Up to 24 key frames |
| Video | Probe (codec, resolution, fps, HDR, rotation, tags), a **full-length** scene-score pass (hard cuts, gradual dissolves, dips to black), fade in/out, camera motion per scene, pacing (cuts per minute, shot lengths, % of cuts on audio onsets), embedded subtitles | A frame at each scene start, mid-scene frames for long shots, a frame every N s, and the final frame (capped at *Max frames*, scene starts kept first). Each frame is labelled with its timestamp and scene number. |
| Audio (or a video's soundtrack) | Loudness, peak, dynamic range, silence, BPM with confidence, onsets, spectral balance, loudness envelope, sudden drops/jumps, Whisper transcript with timestamps, speaking rate, voice pitch | A mel spectrogram image with a time axis |

Long files are analyzed up to **Max duration** (10 minutes by default); the app warns when it
truncates. Claude returns JSON that must match a strict schema (`ai.py`). Every step carries
`estimated: true` unless its values come from the file's own metadata. The UI and every export
label the steps as estimated.

**Errors are shown in the window:** unsupported extension, an empty file, a file over the size
limit, a corrupted or unreadable file, Claude Code not installed, not signed in, signed in with
an API key instead of the subscription, the plan's usage limit reached, a model your plan can't
use, the time limit exceeded, and an unreadable response. The sign-in check runs before the slow
analysis, so a sign-in problem shows up immediately. Analysis runs on a worker thread with a progress bar and a
Cancel button. Cancel kills FFmpeg and stops the stream.

## 5. Sample output

`samples/` holds two examples in the exact format the app exports:

- `sample_image_result.md` / `.txt` / `.json`: a golden-hour café portrait (5 steps: generate,
  open-model alternative, upscale, Lightroom grade, export).
- `sample_video_result.md` / `.txt` / `.json`: a 15 s vertical ad (11 steps: a look reference,
  five shots in scene order with time ranges, voice-over, music, then assembly, captions and the
  final mix with FFmpeg). The FFmpeg commands in steps 9 to 11 were run in PowerShell on test clips
  to confirm they work.

These are hand-written examples of the format, not live model output. For real Opus 5.5 output
from the app, see `real_run_test_image.md` and `real_run_test_video.md`. Both come from the
smoke test's synthetic files (a drawn poster PNG and a 3-shot MP4 with a 120 BPM click). Rebuild the `.md` and `.txt`
files from the JSON with `python samples\render_samples.py`.

## 6. Settings

| Setting | Default | Notes |
|---|---|---|
| Claude CLI path | auto | Leave blank to auto-detect `claude.exe`. |
| Model | `claude-opus-5-5` | Any model your plan includes. |
| Effort | medium | Higher is more thorough, uses more of your plan's usage and takes longer. |
| Extra frame every | 5 s | In addition to the scene-change frames. |
| Max frames sent | 40 | 4–90. This is the main lever on how much of your plan's usage a run takes: a 768 px frame is roughly 800 input tokens. |
| Cut sensitivity | 0.30 | The FFmpeg scene score that counts as a hard cut. |
| Max duration analyzed | 10 min | |
| Max file size | 2048 MB | |
| Transcribe / Whisper model | on / base | `small` and `medium` are more accurate but slower. |
| Claude time limit | 20 min | The app gives up on the Claude step after this long. |

Settings are stored in `%APPDATA%\MediaDecoder\settings.json`, the Whisper models in
`%APPDATA%\MediaDecoder\whisper-models`, and the windowed app's log in
`%APPDATA%\MediaDecoder\log.txt`. Claude Code keeps its own sign-in under `%USERPROFILE%\.claude`.

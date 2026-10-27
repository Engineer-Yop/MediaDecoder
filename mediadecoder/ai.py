"""The single network step: send measured data + frames to Claude and get back a recipe.

Runs through the Claude Code CLI (`claude -p`) signed in with the user's Claude subscription,
so it draws on the plan's usage limits instead of pay-as-you-go API credits.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .config import Settings
from .media import CancelFn, Cancelled, MediaError, ProgressFn

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
INSTALL_HINT = ("Install Claude Code (PowerShell: irm https://claude.ai/install.ps1 | iex), "
                "then click 'Sign in' in Settings.")

TOOL_TYPES = ["ai_image", "ai_video", "ai_audio", "ai_voice", "ai_text", "image_editor", "video_editor",
              "audio_editor", "command_line", "3d", "capture", "other"]

_STEP = {
    "type": "object",
    "properties": {
        "number": {"type": "integer"},
        "title": {"type": "string"},
        "produces": {"type": "string"},
        "tool": {"type": "string"},
        "tool_type": {"type": "string", "enum": TOOL_TYPES},
        "time_range": {"type": "string"},
        "prompt": {"type": "string"},
        "negative_prompt": {"type": "string"},
        "command": {"type": "string"},
        "settings": {"type": "string"},
        "evidence": {"type": "string"},
        "estimated": {"type": "boolean"},
    },
    "required": ["number", "title", "produces", "tool", "tool_type", "time_range", "prompt",
                 "negative_prompt", "command", "settings", "evidence", "estimated"],
    "additionalProperties": False,
}
RECIPE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "likely_origin": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "style_keywords": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": _STEP},
    },
    "required": ["title", "summary", "likely_origin", "confidence", "style_keywords", "steps"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are a senior creative technologist who reverse-engineers finished media into a \
reproducible production recipe. You receive measurements extracted locally from one media file (metadata, \
embedded generator text, colour/lighting statistics, scene cuts with timestamps, camera-motion estimates, \
audio features, a speech transcript) plus sampled frames or a spectrogram. Reconstruct the most plausible \
step-by-step process that someone could follow to recreate or closely imitate the file with today's AI \
generators and editing tools.

You cannot know the real process. Every step is a reconstruction: set "estimated": true unless the step's key \
values are read directly from the file's own embedded metadata (for example a Stable Diffusion "parameters" \
chunk, Camera Raw settings in XMP, EXIF camera settings) - then set it false and say so in "evidence". \
Measured facts (resolution, fps, cut times, BPM estimate) are inputs, not proof of how the file was made.

How to write the steps:
- Order them in real production order. For video: first any shared setup (look reference, character sheet), then \
one generation or capture step per scene in the exact order the scenes appear, each with its "time_range" \
(e.g. "00:03.20-00:07.85"). If there are more than ~15 scenes, group consecutive similar shots into one step \
but keep order and list every time range. Then the edit: assembly with exact cut points and transition types, \
speed changes, on-screen text (quote the exact text you can read, with timing, font style, position, colour), \
captions, colour grade, then audio (music, voice-over, SFX, mix), then the final export.
- Prompts must be concrete and copy-paste ready, never vague. An image/video prompt names: subject and \
action, setting, composition and framing, camera angle and lens (e.g. "35mm, eye level, shallow depth of \
field"), lighting (direction, quality, colour temperature), colour palette (use the measured hex colours), \
style/medium, mood, and the aspect ratio. Video prompts add camera movement (use the measured motion), \
subject motion, shot duration and pacing.
- Match prompt syntax to the named tool: Midjourney uses parameters like "--ar 9:16 --style raw --v 7"; \
Stable Diffusion/Flux get natural-language prompts plus a negative prompt and sampler/steps/CFG in \
"settings"; Runway/Kling/Veo/Sora/Luma get a shot description with camera move and duration; Suno/Udio get \
style tags, BPM, structure tags like [Verse] [Chorus] and lyrics if sung; ElevenLabs/TTS get the exact \
script (from the transcript) plus a voice description (pitch, pace, accent, energy).
- Commands must run as written in Windows PowerShell or cmd: single-line FFmpeg (or ImageMagick, Blender CLI) \
commands with real values from the measurements (resolution, fps, codec, cut timestamps, durations), \
consistent placeholder filenames across steps (scene01.mp4, music.wav, final.mp4), no bash-only syntax.
- For manual editor work (Photoshop, Lightroom, Premiere, CapCut, After Effects, DaVinci) put a numbered \
list of concrete actions with values in "settings" (e.g. "Exposure +0.3, Highlights -40, Grain 15") and \
leave "command" empty.
- If the media looks like a real camera photo or recording, include the capture step (camera, lens, \
settings, lighting setup) and the edit, and also give an AI-generator prompt that would imitate it.
- Use an empty string for any field that does not apply. "evidence" briefly cites which measurement or \
visual cue supports the step. Never identify real people by name - describe them generically.
- Do not pad: every step must produce something the next steps use."""


def _image_block(jpeg: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(jpeg).decode("ascii")}}


def build_content(analysis: dict, images: list[tuple[str, bytes]]) -> list[dict]:
    kind = analysis.get("kind", "media")
    content: list[dict] = [{
        "type": "text",
        "text": (f"Media type: {kind}. File name: {analysis['file']['name']}.\n"
                 "Locally extracted measurements (JSON):\n"
                 + json.dumps(analysis, ensure_ascii=False, separators=(",", ":"), default=str)),
    }]
    for label, jpeg in images:
        content.append({"type": "text", "text": label})
        content.append(_image_block(jpeg))
    content.append({"type": "text", "text": (
        "Reconstruct the creation process of this file as ordered steps following your instructions. "
        + ("Cover every scene in order using the scene list and frame timestamps. " if kind == "video" else "")
        + ("Use the transcript verbatim for any voice-over or dialogue step. " if analysis.get("transcript") else "")
    )})
    return content


def find_claude(override: str = "") -> str | None:
    """Locate claude.exe: the Settings override, PATH, the native installer location, then the
    copy bundled with the Claude desktop app (newest version first)."""
    candidates: list[str] = []
    if override:
        candidates.append(override)
    on_path = shutil.which("claude")
    if on_path:
        candidates.append(on_path)
    candidates.append(str(Path.home() / ".local" / "bin" / "claude.exe"))
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        bundled = glob.glob(os.path.join(local, "Claude-Data", "*", "claude-code", "*", "*", "claude.exe"))
        candidates += sorted(bundled, key=os.path.getmtime, reverse=True)
    # A native .exe gets arguments verbatim; an npm .cmd shim goes through cmd.exe, which can
    # mangle the JSON arguments, so prefer any .exe found.
    existing = [c for c in candidates if c and os.path.isfile(c)]
    exes = [c for c in existing if c.lower().endswith(".exe")]
    return (exes or existing or [None])[0]


def _clean_env() -> dict:
    """Environment for the CLI. API-key variables are removed on purpose: if they leaked through,
    the CLI would bill pay-as-you-go credits instead of using the Claude subscription."""
    drop = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
    return {k: v for k, v in os.environ.items()
            if k not in drop and not k.startswith(("CLAUDE_CODE_", "CLAUDECODE"))}


def auth_status(exe: str) -> dict:
    try:
        r = subprocess.run([*_base_cmd(exe), "auth", "status", "--json"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=_clean_env(), timeout=60,
                           creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"Could not run the Claude CLI ({exe}): {exc}")
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"loggedIn": False, "error": (r.stderr or r.stdout).strip()[:300]}


def open_sign_in(exe: str) -> None:
    """Open a console window running `claude auth login` (it opens the browser)."""
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    subprocess.Popen([exe, "auth", "login"], env=_clean_env(), creationflags=flags)


def preflight(settings: Settings) -> str:
    """Fail fast, before the slow local analysis, if Claude can't be used."""
    exe = find_claude(settings.claude_path)
    if not exe:
        raise MediaError(f"Claude Code was not found on this PC. {INSTALL_HINT}")
    status = auth_status(exe)
    if not status.get("loggedIn"):
        raise MediaError("Claude Code is not signed in. Open Settings and click 'Sign in' "
                         "(use the Claude account that has your Pro/Max subscription).")
    if status.get("authMethod") in ("api_key", "apiKey"):
        raise MediaError("Claude Code is signed in with an API key, which would use paid credits. "
                         "Run 'claude auth logout', then sign in again with your Claude subscription account.")
    return exe


def _friendly_cli_error(text: str) -> MediaError:
    low = text.lower()
    if "not logged in" in low or "/login" in low or "oauth" in low and "expired" in low:
        return MediaError("Claude Code is not signed in. Open Settings and click 'Sign in'.")
    if "limit" in low and ("usage" in low or "reached" in low or "reset" in low):
        return MediaError(f"Your Claude plan's usage limit was reached: {text.strip()[:300]}")
    if "credit balance" in low:
        return MediaError("Claude Code is using an API key with no credits. Sign in with your subscription instead.")
    if "model" in low and ("not found" in low or "not available" in low or "invalid" in low):
        return MediaError(f"The selected model isn't available on your plan: {text.strip()[:300]}")
    return MediaError(f"Claude returned an error: {text.strip()[:500]}")


def _base_cmd(exe: str) -> list[str]:
    return [exe]  # tests swap this for a fake CLI


def _parse_recipe(result_event: dict, last_text: str) -> dict:
    recipe = result_event.get("structured_output")
    if isinstance(recipe, dict):
        return recipe
    for candidate in (result_event.get("result") or "", last_text):
        text = candidate.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                continue
    raise MediaError("Claude returned an unreadable response. Please try again.")


def generate_recipe(analysis: dict, images: list[tuple[str, bytes]], settings: Settings, exe: str,
                    progress: ProgressFn, cancel: CancelFn, p0: float = 72, p1: float = 99) -> tuple[dict, dict]:
    cmd = [*_base_cmd(exe), "-p",
           "--model", settings.model, "--effort", settings.effort,
           "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--json-schema", json.dumps(RECIPE_SCHEMA, separators=(",", ":")),
           "--system-prompt", SYSTEM_PROMPT,
           "--tools", "", "--no-session-persistence"]
    message = {"type": "user", "message": {"role": "user", "content": build_content(analysis, images)}}
    payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    progress(p0, f"Sending {len(images)} image(s) and measurements to {settings.model} (Claude subscription)")

    workdir = tempfile.mkdtemp(prefix="mediadecoder_")  # empty cwd: no project files or CLAUDE.md
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=workdir, env=_clean_env(), creationflags=_NO_WINDOW)
    except OSError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise MediaError(f"Could not start the Claude CLI: {exc}. {INSTALL_HINT}")

    events: list[dict] = []
    err_chunks: list[bytes] = []

    def write_in():
        try:
            proc.stdin.write(payload)
            proc.stdin.close()
        except OSError:
            pass  # the process exited early; its error is read from stdout/stderr

    def read_out():
        for raw in iter(proc.stdout.readline, b""):
            try:
                events.append(json.loads(raw))
            except ValueError:
                pass

    def read_err():
        err_chunks.append(proc.stderr.read())

    threads = [threading.Thread(target=f, daemon=True) for f in (write_in, read_out, read_err)]
    for t in threads:
        t.start()
    started = time.time()
    deadline = started + settings.timeout_min * 60
    try:
        while proc.poll() is None:
            if cancel():
                proc.kill()
                raise Cancelled()
            if time.time() > deadline:
                proc.kill()
                raise MediaError(f"Claude did not finish within {settings.timeout_min:g} minutes. "
                                 "Try fewer frames or lower effort.")
            elapsed = time.time() - started
            progress(min(p1 - 1, p0 + 2 + (p1 - p0 - 3) * (1 - 1 / (1 + elapsed / 60))),
                     f"{settings.model} is analyzing… {int(elapsed)} s")
            time.sleep(0.5)
        for t in threads:
            t.join(timeout=5)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    last_text = ""
    for e in events:
        if e.get("type") == "assistant":
            last_text = "".join(c.get("text", "") for c in e.get("message", {}).get("content", [])
                                if c.get("type") == "text") or last_text
    if result is None:
        stderr = b"".join(err_chunks).decode("utf-8", errors="replace")
        raise _friendly_cli_error(stderr or last_text or f"the CLI exited with code {proc.returncode}")
    if result.get("is_error") or result.get("subtype") != "success":
        raise _friendly_cli_error(str(result.get("result") or last_text or result.get("subtype")))

    recipe = _parse_recipe(result, last_text)
    for i, step in enumerate(recipe.get("steps", []), start=1):
        step["number"] = i
    usage = result.get("usage") or {}
    # modelUsage can also list a small helper model Claude Code uses for housekeeping; report the
    # model that did the analysis (the requested one, else the one that wrote the most).
    model_usage = result.get("modelUsage") or {}
    main_model = next((m for m in model_usage if m.startswith(settings.model)), None) or max(
        model_usage, key=lambda m: (model_usage[m] or {}).get("outputTokens", 0), default=settings.model)
    meta = {"model": main_model, "models_used": sorted(model_usage),
            "engine": "Claude subscription (Claude Code CLI)",
            "input_tokens": int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0))
            + int(usage.get("cache_creation_input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0))}
    return recipe, meta

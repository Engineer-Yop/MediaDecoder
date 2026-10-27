"""Settings, stored in %APPDATA%\\MediaDecoder\\settings.json.

There is no API key: the AI step runs through the Claude Code CLI signed in with your
Claude subscription (Pro / Max), so it uses your plan's usage, never pay-as-you-go credits.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "MediaDecoder"
APP_VERSION = "1.1.0"

MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
WHISPER_MODELS = ["tiny", "base", "small", "medium"]


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    claude_path: str = ""              # blank = find claude.exe automatically
    frame_interval_s: float = 5.0      # extra frame every N seconds, on top of scene-change frames
    max_duration_min: float = 10.0     # analysis cap for long videos / audio
    max_frames: int = 40               # frames sent to the vision model per request
    scene_threshold: float = 0.30      # ffmpeg scene score that counts as a cut
    transcribe: bool = True
    whisper_model: str = "base"
    max_file_mb: int = 2048
    timeout_min: float = 20.0          # give up on the Claude step after this long

    @classmethod
    def load(cls) -> "Settings":
        path = config_dir() / "settings.json"
        settings = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                for key, value in data.items():
                    if key in known:
                        setattr(settings, key, type(getattr(settings, key))(value))
            except (ValueError, TypeError, OSError):
                pass  # corrupt settings file: fall back to defaults
        settings.max_frames = max(4, min(int(settings.max_frames), 90))
        return settings

    def save(self) -> None:
        path = config_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

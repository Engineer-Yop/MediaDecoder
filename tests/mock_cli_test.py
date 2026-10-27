"""Exercise the Claude-CLI engine and the GUI without using any Claude usage.

tests/fake_claude.py stands in for claude.exe: it checks what the app sends (stream-json
stdin with images, --json-schema, no API key in the environment) and answers like the real
CLI. Then the main window is rendered offscreen and saved as tests/media/ui_screenshot.png.
Run tests/smoke_test.py first (it creates tests/media/edit.mp4).

    python tests/mock_cli_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MEDIA = ROOT / "tests" / "media"

RECIPE = {
    "title": "Three-shot product teaser with beat-synced cuts",
    "summary": "An 8.5 s 16:9 teaser: a fast pan across a confetti poster, a bars insert, then a slow push-in, cut on a 120 BPM click.",
    "likely_origin": "Stills animated with pan/zoom keyframes and assembled in FFmpeg or CapCut",
    "confidence": "medium",
    "style_keywords": ["warm gradient", "confetti", "graphic poster", "punchy cuts"],
    "steps": [
        {"number": 1, "title": "Generate the poster still", "produces": "poster.png (3200x2000)",
         "tool": "Midjourney v7", "tool_type": "ai_image", "time_range": "",
         "prompt": "flat graphic poster, warm sunset gradient from #C35B45 red to deep #513D41 plum, scattered multicolour confetti dots, bold white sans-serif headline \"SUMMER SALE\" top-left --ar 16:10 --style raw --v 7",
         "negative_prompt": "", "command": "", "settings": "", "evidence": "Palette and headline read from frames 1-2.",
         "estimated": True},
        {"number": 2, "title": "Animate shot 1: fast pan right", "produces": "scene01.mp4 (3.0 s)",
         "tool": "FFmpeg", "tool_type": "command_line", "time_range": "00:00.00-00:03.00", "prompt": "",
         "negative_prompt": "",
         "command": "ffmpeg -loop 1 -t 3 -i poster.png -vf \"crop=1280:720:'40+t*300':400,fps=30,format=yuv420p\" -c:v libx264 scene01.mp4",
         "settings": "", "evidence": "Measured pan right at 0.25 frame-widths/s.", "estimated": True},
    ],
}


def main():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-must-not-leak"  # the app must strip this
    log = MEDIA / "fake_claude_log.json"
    recipe_file = MEDIA / "fake_recipe.json"
    recipe_file.write_text(json.dumps(RECIPE), encoding="utf-8")
    os.environ["FAKE_CLAUDE_LOG"] = str(log)
    os.environ["FAKE_CLAUDE_RECIPE"] = str(recipe_file)

    from mediadecoder import ai, export, pipeline
    from mediadecoder.config import Settings

    ai._base_cmd = lambda exe: [sys.executable, str(ROOT / "tests" / "fake_claude.py")]
    s = Settings()
    s.transcribe = False
    s.claude_path = sys.executable  # any existing file; _base_cmd decides what actually runs
    result = pipeline.run(MEDIA / "edit.mp4", s, lambda p, m: None, lambda: False)
    sent = json.loads(log.read_text(encoding="utf-8"))
    args = sent["args"]
    for flag in ("-p", "--input-format", "--output-format", "--json-schema", "--system-prompt", "--model", "--effort"):
        assert flag in args, flag
    assert args[args.index("--model") + 1] == "claude-opus-5-5"
    assert args[args.index("--tools") + 1] == ""
    assert not sent["env_has_api_key"], "ANTHROPIC_API_KEY leaked to the CLI"
    assert sent["images"] == len(result["images_sent"]) and sent["schema_keys"][0] == "confidence"
    assert len(result["recipe"]["steps"]) == 2
    print(f"CLI call OK: {sent['images']} images, model={args[args.index('--model') + 1]}, no API key in env")
    print("recipe parsed:", result["recipe"]["title"], "| meta", result["meta"])
    (MEDIA / "mock_recipe.md").write_text(export.to_markdown(result), encoding="utf-8")

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from mediadecoder import ui

    app = QApplication([])
    app.setStyle("Fusion")
    app.setPalette(app.style().standardPalette())
    app.setStyleSheet(ui.STYLE)
    app.setFont(QFont("Segoe UI", 10))
    win = ui.MainWindow()
    win.resize(1040, 900)
    win.show()
    win.on_done(result)
    dlg = ui.SettingsDialog(win, s)
    dlg.show()
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    win.grab().save(str(MEDIA / "ui_screenshot.png"))
    dlg.grab().save(str(MEDIA / "settings_screenshot.png"))
    print("screenshots saved")
    print("MOCK CLI + UI TEST PASSED")


if __name__ == "__main__":
    main()

"""MediaDecoder entry point.

    python main.py                      # open the app
    python main.py photo.jpg            # open the app and analyze a file
    python main.py --cli clip.mp4 --out recipe.md      # headless, full run (needs Claude sign-in)
    python main.py --cli clip.mp4 --analyze-only --out data.json   # headless, no API call
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from pathlib import Path


def run_cli(args) -> int:
    from mediadecoder import export, pipeline
    from mediadecoder.config import Settings
    from mediadecoder.media import MediaError

    settings = Settings.load()
    if args.model:
        settings.model = args.model

    def progress(pct, msg):
        print(f"[{pct:5.1f}%] {msg}", file=sys.stderr, flush=True)

    try:
        result = pipeline.run(Path(args.cli), settings, progress, lambda: False,
                              analyze_only=args.analyze_only)
    except MediaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.analyze_only:
        import json
        text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    else:
        suffix = Path(args.out).suffix.lower() if args.out else ".md"
        text = {".txt": export.to_text, ".json": export.to_json}.get(suffix, export.to_markdown)(result)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


def _redirect_missing_console() -> None:
    """A windowed .exe has no stdout/stderr; libraries that print (tqdm, tracebacks) would crash."""
    if sys.stdout is None or sys.stderr is None:
        from mediadecoder.config import config_dir

        log = open(config_dir() / "log.txt", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def main() -> int:
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    _redirect_missing_console()
    parser = argparse.ArgumentParser(prog="MediaDecoder")
    parser.add_argument("file", nargs="?", help="media file to open in the app")
    parser.add_argument("--cli", metavar="FILE", help="analyze FILE without the GUI")
    parser.add_argument("--analyze-only", action="store_true", help="with --cli: skip the API call")
    parser.add_argument("--out", help="with --cli: output path (.md, .txt, .json)")
    parser.add_argument("--model", help="with --cli: override the model")
    args = parser.parse_args()
    if args.cli:
        return run_cli(args)
    from mediadecoder.ui import run_gui
    return run_gui(args.file)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

"""Renders the sample results through the app's own exporters (.md and .txt)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from mediadecoder import export  # noqa: E402
from mediadecoder.ai import RECIPE_SCHEMA  # noqa: E402

required = set(RECIPE_SCHEMA["properties"]["steps"]["items"]["required"])
for name in ("sample_image_result", "sample_video_result"):
    result = json.loads((HERE / f"{name}.json").read_text(encoding="utf-8"))
    for step in result["recipe"]["steps"]:
        assert set(step) == required, (name, step["number"], set(step) ^ required)
    (HERE / f"{name}.md").write_text(export.to_markdown(result), encoding="utf-8")
    (HERE / f"{name}.txt").write_text(export.to_text(result), encoding="utf-8")
    print("rendered", name)

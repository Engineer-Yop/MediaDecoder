"""Draws assets/icon.ico (a frame with a step list) so the build needs no binary assets."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=48, fill=(47, 111, 237, 255))
d.rounded_rectangle([44, 52, 212, 150], radius=14, fill=(255, 255, 255, 255))
d.polygon([(108, 76), (108, 128), (150, 102)], fill=(47, 111, 237, 255))
for i, w in enumerate((150, 120, 136)):
    y = 170 + i * 22
    d.ellipse([46, y, 60, y + 14], fill=(255, 255, 255, 255))
    d.rounded_rectangle([70, y + 2, 70 + w - 40, y + 12], radius=5, fill=(214, 226, 252, 255))
out = Path(__file__).with_name("icon.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"wrote {out}")

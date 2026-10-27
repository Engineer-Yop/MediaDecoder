# "Summer Sale" sunset-gradient confetti poster (still.png)

**Source file:** `still.png`  
**Likely origin:** Procedurally generated (Python/Pillow or similar) with a hand-written Stable Diffusion "parameters" PNG text chunk. It is probably not a real diffusion output. (confidence: medium)  
**Style:** flat vector, confetti dots, four-corner gradient, sunset palette, random circles, minimal poster, procedural graphic, high saturation, summer sale

> All steps are ESTIMATED reconstructions inferred from the finished file. The real creation process cannot be known for certain.

A 1600x1000 flat-graphic poster: a four-corner gradient (orange top-left, hot red bottom-left, muted green top-right, dark violet bottom-right) covered with about 130 opaque, hard-edged circles in random colours, plus a tiny white "SUMMER SALE" label near the top-left corner. The PNG has an A1111-style Stable Diffusion "parameters" chunk ("sunset gradient with confetti, poster / Steps: 30, Sampler: DPM++ 2M, CFG scale: 6, Seed: 42"). The pixels themselves look procedural, not diffused. The circles are perfect, with no noise or texture. Colours are uniform. The file is only 59 KB, which suits flat fills, not a diffusion render. The most likely recipe: draw it with a script, then write the SD-style text chunk into the file. A real Stable Diffusion route with the embedded settings is included as an alternative.

## Step 1: Four-corner sunset gradient background `ESTIMATED`

- **Produces:** background.png (1600x1000 RGB)
- **Tool:** ImageMagick 7

**Command:**

```powershell
magick -size 1600x1000 xc: -sparse-color bilinear "0,0 #FF7A45 1599,0 #2E7A40 0,999 #FF1E40 1599,999 #241A3A" -depth 8 background.png
```

**Settings / actions:**

```text
Bilinear interpolation between 4 corner colours, no noise, no vignette. Corner colours were sampled by eye from the visible gaps between circles.
```

*Evidence:* Background shifts smoothly from orange (top-left) to red-pink (bottom-left), green (top-right) and dark violet (bottom-right). Light bias is top>bottom (+0.115) and left>right (+0.064). Vignette ratio is about 1.03, so there is no vignette. Warm overall (R-B +59.9).

## Step 2: Scatter random confetti circles, add label, embed SD-style metadata `ESTIMATED`

- **Produces:** still.png (final, about 59 KB)
- **Tool:** Python 3 + Pillow

**Command:**

```powershell
python -I make_still.py background.png still.png
```

**Settings / actions:**

```text
Save as make_still.py in its own folder:
import sys, random
from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo
random.seed(42)
img = Image.open(sys.argv[1]).convert('RGB')
d = ImageDraw.Draw(img)
for _ in range(130):
    x = random.randint(0, 1600); y = random.randint(0, 1000)
    r = random.randint(8, 60)
    c = tuple(random.randint(0, 255) for _ in range(3))
    d.ellipse((x-r, y-r, x+r, y+r), fill=c)
try: f = ImageFont.truetype('arial.ttf', 11)
except OSError: f = ImageFont.load_default()
d.text((59, 58), 'SUMMER SALE', fill=(235,235,235), font=f)
meta = PngInfo()
meta.add_text('parameters', 'sunset gradient with confetti, poster\nSteps: 30, Sampler: DPM++ 2M, CFG scale: 6, Seed: 42')
img.save(sys.argv[2], pnginfo=meta, optimize=True)

Notes:
1) The circles are fully opaque, have no stroke and no anti-alias blur, and later circles overlap earlier ones.
2) Radius range is about 8-60 px (the smallest dots are about 16 px wide, the largest about 120 px).
3) The label is 'SUMMER SALE': small, about 11 px, light grey/white, plain sans-serif, top-left at about x=59, y=58, drawn under nothing.
4) Seed 42 matches the embedded seed, but the exact layout won't reproduce.
```

*Evidence:* The circles are perfect and hard-edged with random RGB colours (palette includes #79CD71, #CB1E46, #521E4D). The PNG is only 59 KB at 1.6 MP, which is typical of flat fills. There are no diffusion artefacts or texture. The tiny 'SUMMER SALE' text reads clearly near (59,64). The 'parameters' chunk text was read directly from the file. Its values (Steps 30, DPM++ 2M, CFG 6, Seed 42) are copied verbatim, but my claim that they were written in afterwards is an inference.

## Step 3: Alternative: imitate with Stable Diffusion using the embedded settings `ESTIMATED`

- **Produces:** still_sd.png (diffusion version, 1600x1000)
- **Tool:** Stable Diffusion WebUI (A1111/Forge) with an SDXL checkpoint

**Prompt:**

```text
sunset gradient with confetti, poster, flat vector graphic, smooth four-corner gradient from orange #FF7A45 top-left to hot red #FF1E40 bottom-left, muted green #2E7A40 top-right and deep violet #241A3A bottom-right, scattered overlapping solid circles of random bright colours (lime #79CD71, crimson #CB1E46, plum #521E4D, teal, cobalt blue, yellow), hard edges, no shading, no texture, tiny white sans-serif text 'SUMMER SALE' in the top-left corner, minimal sale poster, 16:10
```

**Negative prompt:**

```text
photo, 3d, shading, gradients inside circles, blur, bokeh, noise, grain, texture, realistic, people, large text, watermark, frame
```

**Settings / actions:**

```text
Steps: 30, Sampler: DPM++ 2M (Karras), CFG scale: 6, Seed: 42. These four values are read directly from the PNG's parameters chunk. Generate at 1216x760 (SDXL-friendly 16:10), then Hires fix x1.316 to 1600x1000, denoise 0.35, upscaler 4x-UltraSharp. The original prompt had no negative prompt; the one above is my addition. The model is unlikely to render the tiny text cleanly; add it afterwards in an editor (Arial 11 px, #EBEBEB, at x=59 y=58).
```

*Evidence:* Prompt base, steps, sampler, CFG and seed come verbatim from the embedded 'parameters' text. The checkpoint, resolution path and colour details are estimates based on the measured palette and the frame.

---
*Generated by MediaDecoder 1.1.0 with claude-opus-5-5.*


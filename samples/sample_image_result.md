# Golden-hour café window portrait, film-look grade

**Source file:** `cafe_window_portrait.jpg`  
**Likely origin:** AI-generated (no EXIF camera data, no C2PA) then colour-graded; a real 85 mm photo is also plausible (confidence: medium)  
**Style:** golden hour, window light, shallow depth of field, warm film grade, editorial lifestyle, Kodak Portra look

> All steps are ESTIMATED reconstructions inferred from the finished file. The real creation process cannot be known for certain.

A 4:5 vertical portrait of a barista leaning on a café counter, lit by low warm sun through a window on the left, shot (or generated) with a shallow-depth-of-field 85 mm look, then graded warm with lifted shadows, soft film grain and a light vignette. Measured: 3456x4320, palette dominated by amber #D9A066 and espresso brown #3B2A22, warm temperature (R-B +31), left side 0.12 brighter than right, centre 3.4x sharper than edges, corners 0.78 of centre brightness.

## Step 1: Generate the base portrait `ESTIMATED`

- **Produces:** base_portrait.png - 4:5 vertical portrait with the final composition and lighting
- **Tool:** Midjourney v7

**Prompt:**

```text
editorial lifestyle portrait of a young barista with curly dark hair and a rust-coloured linen apron, leaning on a wooden café counter, holding a ceramic cup, soft smile, looking slightly off camera, warm low golden-hour sunlight streaming through a large window from the left, long soft shadows, dust motes in the light beam, blurred espresso machine and hanging pendant lamps in the background, 85mm lens at f/1.8, eye-level medium close-up, rule-of-thirds framing with the face on the upper-left third, shallow depth of field with creamy bokeh, colour palette amber #D9A066, espresso brown #3B2A22, cream #EFE3D0, muted teal accents #4E6E6A, Kodak Portra 400 film look, natural skin texture --ar 4:5 --style raw --v 7 --stylize 150
```

**Settings / actions:**

```text
Generate 4 variations, pick the one whose window light falls from the left and whose face sits on the upper-left third; upscale with 'Upscale (Subtle)'.
```

*Evidence:* No camera EXIF or lens data; left/right brightness bias +0.12 and warm R-B +31 indicate a warm key from the left; centre-vs-edge sharpness 3.4 implies shallow depth of field.

## Step 2: Alternative: generate locally with Flux / Stable Diffusion `ESTIMATED`

- **Produces:** base_portrait.png - same shot from an open model
- **Tool:** Flux.1 [dev] in ComfyUI (or SDXL in Automatic1111)

**Prompt:**

```text
Editorial lifestyle photograph, 4:5 vertical. A young barista with curly dark hair and a rust linen apron leans on a wooden café counter holding a ceramic cup, soft smile, gaze slightly off camera. Warm low golden-hour sun pours through a large window on the left, long soft shadows, visible dust in the light beam. Background: out-of-focus espresso machine and pendant lamps with creamy bokeh. Shot on an 85mm lens at f/1.8, eye level, medium close-up, face on the upper-left third. Amber, espresso brown, cream and muted teal palette, Kodak Portra 400 colour, natural skin texture, fine grain.
```

**Negative prompt:**

```text
cartoon, illustration, 3d render, oversaturated, harsh flash, plastic skin, extra fingers, deformed hands, text, watermark, cold blue light
```

**Settings / actions:**

```text
Resolution 1728x2160 (4:5); sampler Euler, scheduler simple, 28 steps, guidance 3.5 (Flux) / CFG 6 (SDXL); seed random; then 2x upscale in step 3.
```

*Evidence:* Same visual cues as step 1; offered for users without Midjourney.

## Step 3: Upscale to the delivered resolution `ESTIMATED`

- **Produces:** portrait_upscaled.png - 3456x4320
- **Tool:** Real-ESRGAN (command line)

**Command:**

```powershell
realesrgan-ncnn-vulkan.exe -i base_portrait.png -o portrait_upscaled.png -n realesrgan-x4plus -s 2
```

*Evidence:* Delivered size 3456x4320 (14.9 MP) is exactly 2x a typical 1728x2160 4:5 generation.

## Step 4: Warm film grade `ESTIMATED`

- **Produces:** portrait_graded.tif - final colour and tone
- **Tool:** Adobe Lightroom Classic (or Camera Raw)

**Settings / actions:**

```text
1. Basic: Temp +12, Tint +4, Exposure +0.15, Contrast -10, Highlights -35, Shadows +25, Whites -10, Blacks +18 (lifted, measured shadows p5 = 0.11).
2. Tone Curve (Point): raise the black point to output 18, slight S-curve (input 64 -> 58, input 192 -> 200).
3. Colour Mixer: Orange Saturation +8, Orange Luminance +6; Blue Hue -15, Blue Saturation -20 (pushes cool tones to muted teal).
4. Colour Grading: Shadows hue 200 / sat 8, Highlights hue 40 / sat 14, Balance +20.
5. Effects: Grain Amount 18, Size 25, Roughness 50; Post-Crop Vignette -14, Midpoint 40, Feather 70 (measured corners 0.78 of centre).
```

*Evidence:* Lifted blacks (0% clipped black), soft highlight roll-off (p95 0.86, 0.1% clipped white), warm/teal palette split and a measured vignette.

## Step 5: Export for Instagram `ESTIMATED`

- **Produces:** cafe_window_portrait.jpg - 4:5 JPEG, sRGB
- **Tool:** ImageMagick

**Command:**

```powershell
magick portrait_graded.tif -colorspace sRGB -resize 3456x4320 -unsharp 0x0.8+0.6+0.02 -quality 92 -sampling-factor 4:2:0 -strip cafe_window_portrait.jpg
```

*Evidence:* Output is a 3456x4320 baseline JPEG without an embedded camera profile.

---
*Generated by MediaDecoder 1.0.0 with claude-opus-5-5.*

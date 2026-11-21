---
name: personal-handwriting
description: >-
  Build a personal handwritten glyph library from real pen-on-paper photos,
  check character coverage against target drafts, guide minimal missing-character
  capture, and programmatically render realistic multi-page handwritten documents
  and PDFs (with tables, equations, and custom templates) without AI image
  generation artifacts. Use when the user wants to digitize their own handwriting,
  create or expand a personal glyph library, or render handwritten assignments,
  notes, or lab reports.
---

# Personal Handwriting Studio

Turn real pen-on-paper handwriting photos into a reusable personal glyph library and programmatically render realistic multi-page handwritten documents and PDFs — with zero AI-generated font artifacts.

## Overview

This skill orchestrates a closed-loop, 100% programmatic handwriting pipeline:

1. **Workspace Init**: Scaffold a local personal workspace (kept out of git via `.gitignore`).
2. **Coverage Pre-Check**: Scan a target draft (`pages.json` or plain text) against `glyphs/manifest.json` and produce the exact minimal list (or printable A4 writing sheet) of missing characters.
3. **Photo Preprocess & Guided Ingest**: Decode `.DNG` / `.HEIC` / `.JPG` / `.PNG` photos with illumination normalization (removing shadows and paper bleed-through), slice characters via cross-platform row-guided connected-component clustering, and run a **mandatory visual contact-sheet audit** before committing glyphs.
4. **Realistic Layout & PDF Rendering**: Compose pages onto blank paper or custom templates using 2× supersampled stroke-width normalization (~0.6 mm gel-pen consistency), sliding-window multi-variant deduplication, natural baseline wave, wobbly hand-drawn tables/equations, and 100% character coverage verification.

## Dependencies

- **Runtime**: `uv` (recommended) or Python 3.10+ with `pillow` and `numpy`. `scripts/handwrite.py` includes PEP 723 inline metadata, so `uv run scripts/handwrite.py ...` auto-provisions dependencies on macOS, Linux, and Windows.
- **Optional**:
  - `ffmpeg` (or `pillow-heif`) for decoding `.HEIC` photos.
  - macOS `swift` only if using the optional free-form Apple Vision OCR extractor (see [references/macos_vision_ocr.md](./references/macos_vision_ocr.md)).

## Quick Start

```bash
# 1. Initialize a personal workspace (with the optional USST lab report template example)
uv run scripts/handwrite.py init --workspace ./my_handwriting --with-example

# 2. Check which characters from a draft are missing in your glyph library
uv run scripts/handwrite.py check \
  --content ./my_handwriting/content/sample_pages.json \
  --manifest ./my_handwriting/glyphs/manifest.json \
  --output ./my_handwriting/coverage_report.json \
  --grid-dir ./my_handwriting/missing_grid

# 3. Preprocess a photo of your handwritten sheet (DNG / HEIC / JPG / PNG)
uv run scripts/handwrite.py preprocess \
  --input ./my_handwriting/photos/sheet1.jpg \
  --output-dir ./my_handwriting/preprocessed

# 4. Dry-run slice & visually audit before committing to manifest.json
uv run scripts/handwrite.py ingest \
  --input ./my_handwriting/preprocessed/sheet1_ink.png \
  --rows-spec ./my_handwriting/content/rows_spec.json \
  --manifest ./my_handwriting/glyphs/manifest.json \
  --contact-dir ./my_handwriting/contact_sheets \
  --dry-run

# 5. After visually inspecting the debug overlay & contact sheet, commit glyphs (omit --dry-run)
uv run scripts/handwrite.py ingest \
  --input ./my_handwriting/preprocessed/sheet1_ink.png \
  --rows-spec ./my_handwriting/content/rows_spec.json \
  --manifest ./my_handwriting/glyphs/manifest.json \
  --contact-dir ./my_handwriting/contact_sheets

# 6. Render realistic multi-page handwritten PNGs + combined PDF
uv run scripts/handwrite.py render \
  --content ./my_handwriting/content/sample_pages.json \
  --manifest ./my_handwriting/glyphs/manifest.json \
  --assets-dir ./my_handwriting \
  --output-dir ./my_handwriting/output \
  --pdf-name report.pdf
```

## Standard Agent Workflow

### Stage 1: Coverage Pre-Check Before Asking the User to Write
- Convert the user's draft into a structured `pages.json` (see [references/page_schema.md](./references/page_schema.md)) or pass a plain `.txt` draft directly to `check`.
- Run `uv run scripts/handwrite.py check ...` to compute exact missing characters sorted by frequency.
- Ask the user to handwrite **only** the missing characters on blank white paper with a black gel pen (recommend writing 2–4 variants for high-frequency characters, digits `0–9`, and punctuation so rendered pages look natural), and take a flat, evenly lit photo.

### Stage 2: Preprocess, Row-Guided Ingest & Mandatory Visual Audit
1. Run `preprocess` to decode `.DNG` / `.HEIC` / `.JPG` / `.PNG` into `<base>_clean.png` and `<base>_ink.png` (transparent RGBA). Use `--crop x0,y0,x1,y1` (normalized `0.0–1.0`) if the photo includes desk edges or shadows.
2. Create a `rows_spec.json` listing the characters written on each row (use `"□"` or `"SKIP"` to ignore crossed-out mistakes or stray blobs; see `examples/usst_lab_report/sample_rows_spec.json`).
3. **Always run `ingest` with `--dry-run` first.** Inspect both `<base>_debug.png` (bounding boxes over ink) and `<base>_sheet.png` (labeled single-character contact sheet) using your file-reading/vision tool.
4. Confirm zero misalignments, ink blobs, or clipped strokes. Only then re-run `ingest` without `--dry-run` to commit glyphs into `manifest.json` using cross-platform case-safe filenames (`upper_<X>_k.png`, `lower_<x>_k.png`, `sym_u<4hex>_k.png`, `<ch>_k.png`).

### Stage 3: Render & Page-by-Page QA
1. Run `render`. The script enforces a 100% character coverage pre-flight check (`verify_all_chars_covered`) and fails fast if any character is missing.
2. Inspect every rendered `page_<N>.png` visually:
   - Check vertical fill percentage in stdout; if `[WARNING] ... overflowed bottom margin` appears, reduce `--font-size` (typical range `28–34`), tighten `line_h_ratio` (`1.38–1.50`), or split content onto an additional continuation page.
   - Verify cover fields align cleanly on underlines, tables do not clip cell text, and stroke weights look uniform.

## Utility Scripts Reference

| Subcommand | Description | Key Flags |
| :--- | :--- | :--- |
| `init` | Create a clean personal workspace | `--workspace`, `--with-example` |
| `check` | Compute glyph coverage & optional A4 missing-char grid | `--content`, `--manifest`, `--output`, `--grid-dir` |
| `preprocess` | Decode photo & extract shadow-free RGBA ink | `--input`, `--output-dir`, `--crop`, `--hi`, `--lo` |
| `ingest` | Row-guided slicing into RGBA glyphs + audit sheets | `--input`, `--rows-spec`, `--manifest`, `--contact-dir`, `--dry-run`, `--source-id` |
| `contact-sheet` | Generate a labeled contact sheet from `manifest.json` | `--manifest`, `--output`, `--chars` |
| `render` | Render `pages.json` onto templates & export PDF | `--content`, `--manifest`, `--output-dir`, `--assets-dir`, `--pdf-name`, `--font-size`, `--messiness`, `--seed` |

## Common Mistakes

1. **Skipping `--dry-run` visual audit during `ingest`**: A single crossed-out blotch or merged pair of adjacent strokes can shift an entire row's labels. Always inspect `<base>_debug.png` and `<base>_sheet.png` before committing.
2. **Using AI image generation or system font fallbacks**: Never generate handwritten pages with diffusion models or substitute system fonts for missing characters. Always capture genuine missing glyphs via `check` → `ingest`.
3. **Case-insensitive filesystem collisions**: On macOS (APFS) and Windows (NTFS), `A_0.png` and `a_0.png` overwrite each other. Always use `handwrite.py` (which enforces `upper_A_0.png` / `lower_a_0.png` / `sym_uXXXX_0.png`).
4. **Calling macOS `sips` inside sandboxes**: `sips` can silently output solid-black images in sandboxed environments. `handwrite.py preprocess` handles `.DNG` in pure Python and `.HEIC` via `pillow-heif` or `ffmpeg`.
5. **Committing personal handwriting to Git**: Keep personal workspaces (`glyphs/`, `photos/`, `output/`) outside the skill repo or inside gitignored paths.

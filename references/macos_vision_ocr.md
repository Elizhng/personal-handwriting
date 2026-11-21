# Optional: macOS Apple Vision OCR for Free-Form Handwriting

By default, `personal-handwriting` uses **cross-platform row-guided slicing** (`uv run scripts/handwrite.py ingest`), which works identically on macOS, Linux, and Windows with zero OCR dependencies.

If you are on **macOS** and already have many pages of free-form handwritten notes (not written in neat row order) that you want to mine for glyphs, you can optionally run Apple's native Vision OCR helper (`scripts/optional_macos_ocr/vision_ocr.swift`).

## Usage (macOS Only)

1. Preprocess your raw photos first to produce `*_clean.png` and `*_ink.png`:
   ```bash
   uv run scripts/handwrite.py preprocess \
     --input ./my_handwriting/photos/note1.DNG \
     --output-dir ./my_handwriting/preprocessed
   ```
2. Run the Swift Vision OCR script on the `preprocessed/` directory:
   ```bash
   swift scripts/optional_macos_ocr/vision_ocr.swift \
     ./my_handwriting/preprocessed \
     ./my_handwriting/ocr_results.json
   ```
3. The output JSON contains per-line recognized text, confidence scores, and per-character bounding boxes (`[x0, y0, x1, y1]` in top-left pixel coordinates) for `zh-Hans` and `en-US`. You can inspect high-confidence lines or convert clean lines into a `rows_spec.json` for `ingest`.

> **Note**: Vision OCR can occasionally misread cursive handwriting or merge adjacent punctuation. Always audit extracted glyphs with `contact-sheet` before using them in final documents.

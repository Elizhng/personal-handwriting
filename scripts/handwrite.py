#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pillow>=10.0.0",
#   "numpy>=1.24.0",
# ]
# ///
"""
Personal Handwriting Studio CLI (`handwrite.py`)

A cross-platform, 100% programmatic real-handwriting engine:
  - init          Scaffold a personal workspace (optionally with example templates)
  - check         Check character coverage & generate missing-character sheets
  - preprocess    Decode DNG/HEIC/JPG/PNG & extract shadow-free transparent RGBA ink
  - ingest        Row-guided character slicing + mandatory visual audit sheets
  - contact-sheet Generate a labeled contact sheet from manifest.json
  - render        Render structured pages.json onto templates & export PDF
"""
import argparse
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

Image.init()

SUP_MAP = {"²": "2", "¹": "1", "⁻": "-", "³": "3"}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)


# ---------------------------------------------------------------------------
# Shared Utilities
# ---------------------------------------------------------------------------

def is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def safe_char_prefix(ch: str) -> str:
    """Case-insensitive-FS-safe filename stem (APFS / NTFS safe)."""
    if is_cjk(ch) or (ch.isascii() and ch.isdigit()):
        return ch
    if ch.isascii() and ch.isupper():
        return f"upper_{ch}"
    if ch.isascii() and ch.islower():
        return f"lower_{ch}"
    return f"sym_u{ord(ch):04x}"


def load_ui_font(size: int = 24):
    """Cross-platform CJK-capable font loader for labels, grids, and contact sheets."""
    candidates = [
        # macOS
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # Windows
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


def load_manifest(manifest_path: str) -> dict:
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "chars" not in data:
            data["chars"] = {}
        return data
    return {"version": 1, "chars": {}}


def save_manifest(manifest_path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(manifest_path)), exist_ok=True)
    data["total_unique_chars"] = len(data["chars"])
    data["total_variants"] = sum(len(v) for v in data["chars"].values())
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_texts_from_content(content_path: str) -> list[str]:
    """Extract all text strings from either a pages.json file or a plain .txt/.md file."""
    if content_path.lower().endswith(".json"):
        with open(content_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        texts = []
        for p in data.get("pages", []):
            for b in p.get("blocks", []):
                for k in (
                    "text", "caption", "left_num", "left_den", "op",
                    "right_num", "right_den", "prefix", "num", "den", "suffix",
                ):
                    if k in b and isinstance(b[k], str):
                        texts.append(b[k])
                for list_k in ("labels", "headers"):
                    if list_k in b and isinstance(b[list_k], list):
                        texts.extend(str(x) for x in b[list_k])
                if "fields" in b and isinstance(b["fields"], list):
                    for fld in b["fields"]:
                        texts.append(str(fld.get("text", "")))
                for row_k in ("rows", "data_rows"):
                    if row_k in b and isinstance(b[row_k], list):
                        for r in b[row_k]:
                            if isinstance(r, list):
                                texts.extend(str(x) for x in r)
        return texts
    with open(content_path, "r", encoding="utf-8") as f:
        return [f.read()]


def extract_embedded_jpeg_from_dng(dng_path: str) -> bytes:
    """Extract the full-resolution JFIF JPEG embedded inside an Apple ProRAW / .DNG file."""
    with open(dng_path, "rb") as f:
        data = f.read()
    i = data.find(b"\xff\xd8\xff\xe0")
    if i == -1:
        i = data.find(b"\xff\xd8\xff\xe1")
    if i == -1:
        raise ValueError(f"No embedded JPEG found in DNG file: {dng_path}")
    nxt = data.find(b"\xff\xd8\xff\xe0", i + 100000)
    if nxt != -1:
        eoi = data.rfind(b"\xff\xd9", i, nxt)
    else:
        eoi = data.find(b"\xff\xd9", i + 4)
    if eoi == -1:
        raise ValueError(f"No JPEG EOI marker found in DNG file: {dng_path}")
    return data[i : eoi + 2]


def open_photo_upright(photo_path: str) -> Image.Image:
    """Open .DNG, .HEIC, .JPG, or .PNG and apply EXIF orientation transpose."""
    ext = os.path.splitext(photo_path)[1].lower()
    if ext == ".dng":
        jpg_bytes = extract_embedded_jpeg_from_dng(photo_path)
        return ImageOps.exif_transpose(Image.open(io.BytesIO(jpg_bytes)))
    if ext in (".heic", ".heif"):
        try:
            import pillow_heif  # type: ignore
            pillow_heif.register_heif_opener()
            return ImageOps.exif_transpose(Image.open(photo_path))
        except Exception:
            pass
        ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
        if ffmpeg and os.path.exists(ffmpeg):
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                tmp_jpg = tf.name
            try:
                subprocess.run(
                    [ffmpeg, "-y", "-i", photo_path, "-frames:v", "1", "-q:v", "2", tmp_jpg],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                img = ImageOps.exif_transpose(Image.open(tmp_jpg))
                img.load()
                return img
            finally:
                if os.path.exists(tmp_jpg):
                    os.remove(tmp_jpg)
        raise RuntimeError(
            f"Cannot decode HEIC file '{photo_path}'. Install `pillow-heif` or `ffmpeg` (never use macOS `sips` inside sandboxes)."
        )
    return ImageOps.exif_transpose(Image.open(photo_path))


def normalize_illumination_to_rgba(
    img: Image.Image,
    crop_norm: tuple[float, float, float, float] | None = None,
    hi: float = 0.80,
    lo: float = 0.44,
    max_dim: int = 4032,
) -> tuple[np.ndarray, Image.Image]:
    """
    Remove uneven lighting, shadows, and paper bleed-through.
    Returns (rgba_uint8_array, clean_rgb_image).
    """
    gray = img.convert("L")
    if crop_norm is not None:
        w0, h0 = gray.size
        x0, y0, x1, y1 = crop_norm
        gray = gray.crop((int(w0 * x0), int(h0 * y0), int(w0 * x1), int(h0 * y1)))

    w, h = gray.size
    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        gray = gray.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))), Image.Resampling.LANCZOS)
        w, h = gray.size

    small = gray.resize((max(1, w // 16), max(1, h // 16)), Image.Resampling.BILINEAR)
    bg_small = small.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(4))
    bg = np.asarray(bg_small.resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32)

    g = np.asarray(gray, dtype=np.float32)
    reflectance = np.clip(g / np.maximum(bg, 30.0), 0.0, 1.0)
    alpha = np.clip((hi - reflectance) / max(1e-5, hi - lo) * 255.0, 0.0, 255.0).astype(np.uint8)

    shade = np.clip(reflectance / max(1e-5, hi) * 38.0, 12.0, 42.0).astype(np.uint8)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 0] = shade
    rgba[:, :, 1] = np.clip(shade.astype(np.int16) + 2, 0, 255).astype(np.uint8)
    rgba[:, :, 2] = np.clip(shade.astype(np.int16) + 4, 0, 255).astype(np.uint8)
    rgba[:, :, 3] = alpha

    clean_rgb = Image.fromarray(255 - alpha, mode="L").convert("RGB")
    return rgba, clean_rgb


def label_connected_components_8(mask_bool: np.ndarray):
    """Fast scanline run-length 8-connected components on a 2D boolean mask."""
    H, W = mask_bool.shape
    parent = [0]
    runs = []
    prev_row_runs = []

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    for y in range(H):
        row = mask_bool[y]
        if not row.any():
            prev_row_runs = []
            continue
        padded = np.empty(W + 2, dtype=np.uint8)
        padded[0] = 0
        padded[1:-1] = row
        padded[-1] = 0
        diffs = np.diff(padded.view(np.int8))
        starts = np.flatnonzero(diffs == 1)
        ends = np.flatnonzero(diffs == -1)

        cur_runs = []
        p_idx = 0
        n_prev = len(prev_row_runs)
        for sx, ex in zip(starts.tolist(), ends.tolist()):
            while p_idx < n_prev and prev_row_runs[p_idx][1] < sx:
                p_idx += 1
            lbl = 0
            k = p_idx
            while k < n_prev and prev_row_runs[k][0] <= ex:
                plbl = prev_row_runs[k][2]
                if lbl == 0:
                    lbl = plbl
                else:
                    union(lbl, plbl)
                k += 1
            if lbl == 0:
                lbl = len(parent)
                parent.append(lbl)
            runs.append((y, sx, ex, lbl))
            cur_runs.append((sx, ex, lbl))
        prev_row_runs = cur_runs

    if not runs:
        return np.zeros((H, W), dtype=np.int32), []

    remap = {}
    next_id = 1
    label_img = np.zeros((H, W), dtype=np.int32)
    stats = {}
    for y, sx, ex, plbl in runs:
        root = find(plbl)
        cid = remap.get(root)
        if cid is None:
            cid = next_id
            remap[root] = cid
            next_id += 1
            stats[cid] = [sx, y, ex, y + 1, ex - sx]
        else:
            st = stats[cid]
            if sx < st[0]:
                st[0] = sx
            if y < st[1]:
                st[1] = y
            if ex > st[2]:
                st[2] = ex
            if y + 1 > st[3]:
                st[3] = y + 1
            st[4] += ex - sx
        label_img[y, sx:ex] = cid

    comps = [
        {"id": cid, "bbox": (x0, y0, x1, y1), "w": x1 - x0, "h": y1 - y0, "area": area}
        for cid, (x0, y0, x1, y1, area) in stats.items()
    ]
    return label_img, comps


# ---------------------------------------------------------------------------
# Subcommand 1: init
# ---------------------------------------------------------------------------

def cmd_init(args) -> None:
    ws = os.path.abspath(args.workspace)
    for sub in ("glyphs", "photos", "preprocessed", "content", "contact_sheets", "missing_grid", "output", "templates"):
        os.makedirs(os.path.join(ws, sub), exist_ok=True)

    manifest_path = os.path.join(ws, "glyphs", "manifest.json")
    if not os.path.exists(manifest_path):
        save_manifest(manifest_path, {"version": 1, "chars": {}})

    if args.with_example:
        ex_dir = os.path.join(REPO_ROOT, "examples", "usst_lab_report")
        if os.path.isdir(ex_dir):
            tpl_src = os.path.join(ex_dir, "templates")
            for fn in os.listdir(tpl_src):
                shutil.copy2(os.path.join(tpl_src, fn), os.path.join(ws, "templates", fn))
            for fn in ("sample_pages.json", "sample_rows_spec.json"):
                src = os.path.join(ex_dir, fn)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(ws, "content", fn))
            print(f"[OK] Copied example USST lab report templates & sample JSONs into {ws}")

    print(f"[OK] Workspace initialized at: {ws}")


# ---------------------------------------------------------------------------
# Subcommand 2: check
# ---------------------------------------------------------------------------

def generate_missing_grid_sheets(missing_chars: list[str], grid_dir: str) -> list[str]:
    os.makedirs(grid_dir, exist_ok=True)
    if not missing_chars:
        return []
    cjk_m = [c for c in missing_chars if is_cjk(c)]
    sym_m = [c for c in missing_chars if not is_cjk(c)]
    chars = cjk_m + sym_m

    W, H = 1654, 2339  # A4 200 DPI
    cols, rows = 12, 14
    per_page = cols * rows
    n_pages = math.ceil(len(chars) / per_page)

    margin_x, margin_top, margin_bottom = 105, 155, 95
    cell_w = (W - 2 * margin_x) / cols
    cell_h = (H - margin_top - margin_bottom) / rows
    label_h = int(cell_h * 0.24)

    font_title = load_ui_font(30)
    font_hint = load_ui_font(19)
    font_label = load_ui_font(24)

    out_paths = []
    page_imgs = []
    for p_idx in range(n_pages):
        img = Image.new("RGB", (W, H), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        title = f"Missing Handwriting Glyphs ({len(chars)} chars, Page {p_idx + 1}/{n_pages})"
        draw.text((margin_x, 42), title, fill=(25, 25, 25), font=font_title)
        draw.text(
            (margin_x, 86),
            "Write each character neatly inside its box with a black gel pen; photograph flat under even lighting.",
            fill=(90, 90, 90),
            font=font_hint,
        )
        # Corner fiducials
        for fx, fy in ((62, 112), (W - 62, 112), (62, H - 62), (W - 62, H - 62)):
            draw.rectangle([fx - 14, fy - 14, fx + 14, fy + 14], fill=(20, 20, 20))

        chunk = chars[p_idx * per_page : (p_idx + 1) * per_page]
        for idx, ch in enumerate(chunk):
            r, c = divmod(idx, cols)
            x0 = int(round(margin_x + c * cell_w))
            y0 = int(round(margin_top + r * cell_h))
            x1 = int(round(margin_x + (c + 1) * cell_w))
            y1 = int(round(margin_top + (r + 1) * cell_h))
            draw.rectangle([x0, y0, x1, y1], outline=(190, 190, 190), width=2)
            draw.line([x0, y0 + label_h, x1, y0 + label_h], fill=(215, 215, 215), width=1)
            draw.text((x0 + 8, y0 + 3), ch, fill=(70, 70, 70), font=font_label)

        out_png = os.path.join(grid_dir, f"missing_grid_p{p_idx + 1}.png")
        img.save(out_png)
        out_paths.append(out_png)
        page_imgs.append(img)

    if page_imgs:
        pdf_path = os.path.join(grid_dir, "missing_grid.pdf")
        page_imgs[0].save(pdf_path, save_all=True, append_images=page_imgs[1:], resolution=200.0)
        out_paths.append(pdf_path)
    return out_paths


def cmd_check(args) -> None:
    texts = extract_texts_from_content(args.content)
    manifest = load_manifest(args.manifest)
    have_chars = set(manifest["chars"].keys())

    ordered = []
    seen = set()
    freq: Counter = Counter()
    for ch in "".join(texts):
        if ch.isspace():
            continue
        mapped = SUP_MAP.get(ch, ch)
        freq[mapped] += 1
        if mapped not in seen:
            seen.add(mapped)
            ordered.append(mapped)

    covered = [c for c in ordered if c in have_chars]
    missing = [c for c in ordered if c not in have_chars]
    missing_by_freq = sorted(missing, key=lambda c: (-freq[c], ordered.index(c)))
    recommended_multi = [c for c in missing_by_freq if freq[c] >= 3 or c.isdigit() or c in "，。、；：()（）"]

    report = {
        "content_file": os.path.abspath(args.content),
        "manifest_file": os.path.abspath(args.manifest),
        "total_unique_chars": len(ordered),
        "covered_count": len(covered),
        "missing_count": len(missing),
        "coverage_pct": round(100.0 * len(covered) / max(1, len(ordered)), 2),
        "missing_chars": missing_by_freq,
        "recommended_multi_variant": recommended_multi,
        "missing_freq": {c: freq[c] for c in missing_by_freq},
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md_path = os.path.splitext(args.output)[0] + ".md"
    lines = [
        f"# Glyph Coverage Report ({report['covered_count']}/{report['total_unique_chars']}, {report['coverage_pct']}%)",
        "",
        f"- **Missing characters ({len(missing_by_freq)})**: `{''.join(missing_by_freq)}`",
        f"- **Write 2–4 variants for high-freq chars ({len(recommended_multi)})**: `{''.join(recommended_multi)}`",
        "",
        "## Suggested Row Copy List (14 chars/row)",
        "",
    ]
    for i in range(0, len(missing_by_freq), 14):
        lines.append(f"- Row {i // 14 + 1}: `{''.join(missing_by_freq[i : i + 14])}`")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[CHECK] Coverage: {report['covered_count']}/{report['total_unique_chars']} ({report['coverage_pct']}%), missing: {len(missing_by_freq)}")
    print(f"[OK] Report written to: {args.output} and {md_path}")

    if args.grid_dir and missing_by_freq:
        grid_files = generate_missing_grid_sheets(missing_by_freq, args.grid_dir)
        print(f"[OK] Generated {len(grid_files)} missing-character grid file(s) in: {args.grid_dir}")


# ---------------------------------------------------------------------------
# Subcommand 3: preprocess
# ---------------------------------------------------------------------------

def parse_crop_arg(crop_str: str | None) -> tuple[float, float, float, float] | None:
    if not crop_str:
        return None
    parts = [float(x.strip()) for x in crop_str.split(",")]
    if len(parts) != 4:
        raise ValueError("--crop must be 4 comma-separated floats: x0,y0,x1,y1")
    return (parts[0], parts[1], parts[2], parts[3])


def cmd_preprocess(args) -> None:
    os.makedirs(args.output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.input))[0]
    img = open_photo_upright(args.input)
    crop_norm = parse_crop_arg(args.crop)
    rgba, clean = normalize_illumination_to_rgba(
        img, crop_norm=crop_norm, hi=args.hi, lo=args.lo, max_dim=args.max_dim
    )
    clean_path = os.path.join(args.output_dir, f"{base}_clean.png")
    ink_path = os.path.join(args.output_dir, f"{base}_ink.png")
    clean.save(clean_path)
    Image.fromarray(rgba, mode="RGBA").save(ink_path)
    ink_pct = float((rgba[:, :, 3] > 20).mean() * 100.0)
    h, w = rgba.shape[:2]
    print(f"[OK] Preprocessed {base} ({w}x{h}, ink={ink_pct:.2f}%) -> {clean_path}, {ink_path}")


# ---------------------------------------------------------------------------
# Subcommand 4: ingest (Cross-platform row-guided slicing + visual audit)
# ---------------------------------------------------------------------------

def find_strip_peaks(proj: np.ndarray, expected_n: int) -> list[int]:
    H = len(proj)
    k = max(5, min(21, H // max(1, expected_n * 6)))
    sm = np.convolve(proj.astype(np.float32), np.ones(k) / k, mode="same")
    min_sep = max(28, int(H / (expected_n * 1.65)))
    order = np.argsort(-sm)
    peaks = []
    for y in order.tolist():
        if sm[y] < 1.0:
            break
        if all(abs(y - p) >= min_sep for p in peaks):
            peaks.append(y)
            if len(peaks) == expected_n:
                break
    peaks.sort()
    if len(peaks) != expected_n:
        # Fallback to evenly spaced peaks if a strip has blank rows
        step = H / float(expected_n)
        return [int(round((i + 0.5) * step)) for i in range(expected_n)]
    return peaks


def cluster_row_components(comps: list[dict], target_count: int, row_h: float) -> list[dict]:
    kept = []
    for c in comps:
        if c["area"] < 6:
            continue
        kept.append({
            "cids": [c["id"]],
            "x0": c["bbox"][0],
            "y0": c["bbox"][1],
            "x1": c["bbox"][2],
            "y1": c["bbox"][3],
            "area": c["area"],
        })
    kept.sort(key=lambda item: item["x0"])

    def merge_two(a: dict, b: dict) -> dict:
        return {
            "cids": a["cids"] + b["cids"],
            "x0": min(a["x0"], b["x0"]),
            "y0": min(a["y0"], b["y0"]),
            "x1": max(a["x1"], b["x1"]),
            "y1": max(a["y1"], b["y1"]),
            "area": a["area"] + b["area"],
        }

    # First merge horizontally overlapping components (e.g. top/bottom radicals, i/j dots)
    merged = True
    while merged and len(kept) > target_count:
        merged = False
        best_idx = -1
        best_score = 1e9
        for i in range(len(kept) - 1):
            a, b = kept[i], kept[i + 1]
            gap = b["x0"] - a["x1"]
            combined_w = max(a["x1"], b["x1"]) - min(a["x0"], b["x0"])
            if gap <= 0:
                score = gap - 1000.0
            else:
                # Penalize creating an overly wide cluster
                width_pen = max(0.0, combined_w - row_h * 1.15) * 2.5
                score = gap + width_pen
            if score < best_score:
                best_score = score
                best_idx = i
        if best_idx >= 0:
            kept[best_idx] = merge_two(kept[best_idx], kept[best_idx + 1])
            del kept[best_idx + 1]
            merged = True
    return kept


def parse_row_tokens(row_entry) -> list[str]:
    if isinstance(row_entry, list):
        return [str(x) for x in row_entry if str(x).strip()]
    return [ch for ch in str(row_entry) if not ch.isspace()]


def render_contact_sheet_image(items: list[tuple[str, np.ndarray]], title: str) -> Image.Image:
    cols = 10
    cell_w, cell_h, hdr_h = 130, 148, 56
    rows = max(1, math.ceil(len(items) / cols))
    img = Image.new("RGB", (cols * cell_w, hdr_h + rows * cell_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font_hdr = load_ui_font(24)
    font_lbl = load_ui_font(20)
    draw.text((16, 14), title, fill=(20, 20, 20), font=font_hdr)

    for idx, (lbl, glyph_rgba) in enumerate(items):
        r, c = divmod(idx, cols)
        x0, y0 = c * cell_w, hdr_h + r * cell_h
        draw.rectangle([x0, y0, x0 + cell_w - 1, y0 + cell_h - 1], outline=(215, 215, 215), width=1)
        draw.text((x0 + 6, y0 + 4), lbl, fill=(30, 90, 200), font=font_lbl)
        g_img = Image.fromarray(glyph_rgba, mode="RGBA")
        box_w, box_h = cell_w - 18, cell_h - 38
        scale = min(box_w / max(1, g_img.width), box_h / max(1, g_img.height), 1.5)
        nw, nh = max(2, int(round(g_img.width * scale))), max(2, int(round(g_img.height * scale)))
        g_r = g_img.resize((nw, nh), Image.Resampling.LANCZOS)
        px = x0 + (cell_w - nw) // 2
        py = y0 + 30 + (box_h - nh) // 2
        img.paste(g_r, (px, py), g_r)
    return img


def cmd_ingest(args) -> None:
    with open(args.rows_spec, "r", encoding="utf-8") as f:
        spec = json.load(f)

    source_id = args.source_id or spec.get("source_id") or os.path.splitext(os.path.basename(args.input))[0]
    base = os.path.splitext(os.path.basename(args.input))[0]

    # Load RGBA ink image directly if already *_ink.png, otherwise preprocess on the fly
    raw_img = open_photo_upright(args.input)
    if raw_img.mode == "RGBA" and args.input.lower().endswith("_ink.png"):
        rgba = np.asarray(raw_img)
        clean_rgb = Image.fromarray(255 - rgba[:, :, 3], mode="L").convert("RGB")
    else:
        crop_norm = parse_crop_arg(args.crop) if args.crop else (tuple(spec["crop"]) if "crop" in spec else None)
        rgba, clean_rgb = normalize_illumination_to_rgba(raw_img, crop_norm=crop_norm, hi=args.hi, lo=args.lo)

    H, W = rgba.shape[:2]
    alpha = rgba[:, :, 3]
    debug_img = clean_rgb.copy()
    debug_draw = ImageDraw.Draw(debug_img)
    font_dbg = load_ui_font(22)

    columns = spec.get("columns")
    if not columns:
        columns = [{"x_range": [0.0, 1.0], "rows": spec["rows"]}]

    extracted: list[tuple[str, np.ndarray, float]] = []  # (ch, crop_rgba, rel_height)
    mismatches = []

    for col_idx, col_cfg in enumerate(columns):
        xr0, xr1 = col_cfg.get("x_range", [0.0, 1.0])
        cx0, cx1 = int(round(W * xr0)), int(round(W * xr1))
        col_rows = [parse_row_tokens(r) for r in col_cfg["rows"]]
        n_rows = len(col_rows)
        if n_rows == 0:
            continue

        col_alpha = alpha[:, cx0:cx1]
        bin_mask = col_alpha >= 30
        label_img, comps = label_connected_components_8(bin_mask)

        col_w = cx1 - cx0
        strips = [
            (0, col_w // 3, col_w / 6.0),
            (col_w // 3, (2 * col_w) // 3, col_w / 2.0),
            ((2 * col_w) // 3, col_w, 5.0 * col_w / 6.0),
        ]
        strip_peaks = [find_strip_peaks(bin_mask[:, sx0:sx1].sum(axis=1), n_rows) for sx0, sx1, _ in strips]
        strip_xs = [m for _, _, m in strips]

        def row_y_at(r_i: int, x_local: float) -> float:
            if x_local <= strip_xs[1]:
                t = (x_local - strip_xs[0]) / max(1.0, strip_xs[1] - strip_xs[0])
                return strip_peaks[0][r_i] * (1.0 - t) + strip_peaks[1][r_i] * t
            t = (x_local - strip_xs[1]) / max(1.0, strip_xs[2] - strip_xs[1])
            return strip_peaks[1][r_i] * (1.0 - t) + strip_peaks[2][r_i] * t

        rows_comps: list[list[dict]] = [[] for _ in range(n_rows)]
        for c in comps:
            x0, y0, x1, y1 = c["bbox"]
            xc, yc = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
            best_r = min(range(n_rows), key=lambda r_i: abs(yc - row_y_at(r_i, xc)))
            rows_comps[best_r].append(c)

        if n_rows > 1:
            row_gaps = [float(np.median([strip_peaks[s][r + 1] - strip_peaks[s][r] for s in range(3)])) for r in range(n_rows - 1)]
            est_row_h = float(np.median(row_gaps)) * 0.72
        else:
            est_row_h = float(H) * 0.5

        for r_i, tokens in enumerate(col_rows):
            clusters = cluster_row_components(rows_comps[r_i], len(tokens), est_row_h)
            if len(clusters) != len(tokens):
                mismatches.append(f"col {col_idx} row {r_i + 1}: expected {len(tokens)} chars, found {len(clusters)} clusters")
            for k_idx, cl in enumerate(clusters):
                tok = tokens[k_idx] if k_idx < len(tokens) else "?"
                gx0, gy0, gx1, gy1 = cl["x0"] + cx0, cl["y0"], cl["x1"] + cx0, cl["y1"]
                is_skip = tok in ("□", "SKIP", "?")
                box_color = (220, 60, 60) if is_skip else (20, 150, 60)
                debug_draw.rectangle([gx0 - 2, gy0 - 2, gx1 + 2, gy1 + 2], outline=box_color, width=2)
                debug_draw.text((gx0, max(0, gy0 - 24)), tok, fill=box_color, font=font_dbg)
                if is_skip:
                    continue
                pad = 3
                sy0, sy1 = max(0, cl["y0"] - pad), min(H, cl["y1"] + pad)
                sx0, sx1 = max(0, cl["x0"] - pad), min(col_w, cl["x1"] + pad)
                sub_lbl = label_img[sy0:sy1, sx0:sx1]
                keep_mask = np.isin(sub_lbl, cl["cids"])
                crop = rgba[sy0:sy1, sx0 + cx0 : sx1 + cx0].copy()
                crop[~keep_mask, 3] = 0
                rel_h = round(clamp((cl["y1"] - cl["y0"]) / max(1.0, est_row_h), 0.35, 1.25), 3)
                extracted.append((tok, crop, rel_h))

    os.makedirs(args.contact_dir, exist_ok=True)
    debug_path = os.path.join(args.contact_dir, f"{base}_debug.png")
    sheet_path = os.path.join(args.contact_dir, f"{base}_sheet.png")
    debug_img.save(debug_path)
    sheet_items = [(f"{i}:{ch}", g) for i, (ch, g, _) in enumerate(extracted)]
    sheet_img = render_contact_sheet_image(sheet_items, f"Audit Sheet: {source_id} ({len(extracted)} glyphs)")
    sheet_img.save(sheet_path)

    print(f"[AUDIT] Debug overlay -> {debug_path}")
    print(f"[AUDIT] Contact sheet -> {sheet_path}")

    if mismatches:
        for m_txt in mismatches:
            print(f"[ERROR] Row mismatch: {m_txt}", file=sys.stderr)
        print("[ERROR] Inspect the debug overlay, adjust --crop or rows_spec.json (use '□'/'SKIP'), and retry.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"[DRY-RUN] Extracted {len(extracted)} glyphs cleanly. Visually inspect the audit images, then re-run without --dry-run to commit.")
        return

    glyphs_dir = os.path.dirname(os.path.abspath(args.manifest))
    os.makedirs(glyphs_dir, exist_ok=True)
    manifest = load_manifest(args.manifest)
    added = 0
    for ch, crop_rgba, rel_h in extracted:
        mapped = SUP_MAP.get(ch, ch)
        variants = manifest["chars"].setdefault(mapped, [])
        v_idx = len(variants)
        prefix = safe_char_prefix(mapped)
        fname = f"{prefix}_{v_idx}.png"
        while os.path.exists(os.path.join(glyphs_dir, fname)):
            v_idx += 1
            fname = f"{prefix}_{v_idx}.png"
        Image.fromarray(crop_rgba, mode="RGBA").save(os.path.join(glyphs_dir, fname))
        variants.append({
            "id": f"{prefix}_{v_idx}",
            "file": fname,
            "source_img": source_id,
            "rel_height": rel_h,
            "rel_y_offset": 0.0,
        })
        added += 1

    save_manifest(args.manifest, manifest)
    print(f"[OK] Committed {added} glyphs into {args.manifest} (total unique={len(manifest['chars'])}, total variants={manifest['total_variants']})")


# ---------------------------------------------------------------------------
# Subcommand 5: contact-sheet
# ---------------------------------------------------------------------------

def cmd_contact_sheet(args) -> None:
    manifest = load_manifest(args.manifest)
    glyphs_dir = os.path.dirname(os.path.abspath(args.manifest))
    target_chars = list(args.chars) if args.chars else list(manifest["chars"].keys())
    items = []
    for ch in target_chars:
        for v in manifest["chars"].get(ch, []):
            fp = os.path.join(glyphs_dir, v["file"])
            if os.path.exists(fp):
                arr = np.asarray(Image.open(fp).convert("RGBA"))
                items.append((f"{ch} ({v['id']})", arr))
    if not items:
        print("[ERROR] No matching glyphs found in manifest.", file=sys.stderr)
        sys.exit(1)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    img = render_contact_sheet_image(items, f"Glyph Library Audit ({len(items)} variants)")
    img.save(args.output)
    print(f"[OK] Contact sheet ({len(items)} glyphs) written to: {args.output}")


# ---------------------------------------------------------------------------
# Subcommand 6: render
# ---------------------------------------------------------------------------

def draw_wobbly_line(draw, x1, y1, x2, y2, rng, messiness=0.7, width=3, color=(26, 28, 33)):
    dist = math.hypot(x2 - x1, y2 - y1)
    steps = max(4, int(dist / 26))
    pts = []
    amp = 1.5 * messiness
    for i in range(steps + 1):
        t = i / steps
        px = x1 + (x2 - x1) * t
        py = y1 + (y2 - y1) * t
        if 0 < i < steps:
            px += rng.uniform(-amp, amp)
            py += rng.uniform(-amp, amp)
        pts.append((px, py))
    draw.line(pts, fill=color, width=width, joint="curve")


class RealGlyphLibrary:
    def __init__(self, manifest_path: str):
        self.glyphs_dir = os.path.dirname(os.path.abspath(manifest_path))
        data = load_manifest(manifest_path)
        self.chars = data["chars"]
        self._cache: dict[str, np.ndarray] = {}

    def get_variants(self, ch: str) -> list[dict]:
        mapped = SUP_MAP.get(ch, ch)
        if mapped not in self.chars or not self.chars[mapped]:
            raise KeyError(f"Character {ch!r} (mapped={mapped!r}) missing from glyph library!")
        return self.chars[mapped]

    def load_alpha_mask(self, variant_meta: dict) -> np.ndarray:
        fname = variant_meta["file"]
        if fname not in self._cache:
            p = os.path.join(self.glyphs_dir, fname)
            arr = np.asarray(Image.open(p).convert("RGBA"))
            alpha = arr[:, :, 3].copy()
            nz_y, nz_x = np.nonzero(alpha >= 15)
            if len(nz_y) > 0:
                alpha = alpha[int(nz_y.min()) : int(nz_y.max()) + 1, int(nz_x.min()) : int(nz_x.max()) + 1]
            self._cache[fname] = alpha
        return self._cache[fname]


class RealHandwritingRenderer:
    def __init__(self, library: RealGlyphLibrary, assets_dir: str, base_size: int = 34, messiness: float = 0.72, seed: int = 42):
        self.lib = library
        self.assets_dir = assets_dir
        self.base_size = base_size
        self.messiness = clamp(messiness, 0.0, 1.0)
        self.rng = random.Random(seed)
        self._recent_variants: dict[str, list[str]] = {}

    def resolve_path(self, path_str: str) -> str:
        if os.path.isabs(path_str) and os.path.exists(path_str):
            return path_str
        cand = os.path.join(self.assets_dir, path_str)
        if os.path.exists(cand):
            return cand
        return path_str

    def pick_variant(self, ch: str) -> dict:
        variants = self.lib.get_variants(ch)
        if len(variants) == 1:
            return variants[0]
        recent = self._recent_variants.setdefault(ch, [])
        max_hist = min(3, len(variants) - 1)
        exclude = set(recent[-max_hist:]) if max_hist > 0 else set()
        pool = [v for v in variants if v["id"] not in exclude] or variants
        weights = [max(0.45, 1.0 - 0.05 * i) for i in range(len(pool))]
        chosen = self.rng.choices(pool, weights=weights, k=1)[0]
        recent.append(chosen["id"])
        if len(recent) > 6:
            del recent[:-6]
        return chosen

    def char_box_metrics(self, raw_ch: str, variant_meta: dict, size: float):
        is_sup = raw_ch in SUP_MAP
        ch = SUP_MAP.get(raw_ch, raw_ch)
        rel_h = float(variant_meta.get("rel_height", 1.0))
        rel_y = float(variant_meta.get("rel_y_offset", 0.0))

        if is_sup:
            sub_sz = size * 0.56
            if ch == "-":
                return sub_sz * 0.22, sub_sz * 0.75, 0.14 * size
            return sub_sz * 0.82, sub_sz * 0.80, -0.05 * size
        if ch == "一":
            th = 0.18 * size
            return th, 1.05 * size, 0.46 * size - 0.5 * th
        if ch == "二":
            th = 0.54 * size
            return th, 1.05 * size, 0.50 * size - 0.5 * th
        if ch == "三":
            th = 0.72 * size
            return th, 1.05 * size, 0.50 * size - 0.5 * th
        if ch in ("口", "小", "工", "七", "十", "土", "上", "下"):
            th = size * clamp(rel_h, 0.68, 0.96)
            return th, 1.10 * size, 0.52 * size - 0.5 * th
        if is_cjk(ch):
            th = size * clamp(rel_h, 0.84, 1.12)
            y_off = clamp(rel_y, -0.06, 0.06) * size
            return th, 1.16 * size, 0.50 * size - 0.5 * th + y_off
        if ch.isdigit() or (ch.isascii() and ch.isupper()) or ch in ("Δ", "℃"):
            th = size * (0.76 if ch in ("Δ", "℃") else 0.80)
            return th, 1.02 * size, 0.88 * size - th
        if ch == "α":
            return size * 0.60, 0.85 * size, 0.88 * size - size * 0.60
        if ch == "∂":
            return size * 0.78, 0.85 * size, 0.88 * size - size * 0.78
        if ch == "ρ":
            th = size * 0.78
            return th, 0.80 * size, 0.88 * size - th * 0.72
        if ch == "*":
            return size * 0.42, 0.55 * size, 0.04 * size
        if ch == "≠":
            th = size * 0.62
            return th, 0.85 * size, 0.50 * size - 0.5 * th
        if ch == "→":
            th = size * 0.42
            return th, 1.15 * size, 0.52 * size - 0.5 * th
        if ch.isascii() and ch.islower():
            if ch in ("d", "l", "k", "i", "b", "h", "t"):
                th = size * 0.80
                return th, 0.90 * size, 0.88 * size - th
            if ch in ("g", "p", "q", "y"):
                th = size * 0.82
                return th, 0.85 * size, 0.88 * size - th * 0.68
            th = size * 0.57
            return th, 0.88 * size, 0.88 * size - th
        if ch in ("，", "。", "、", "."):
            factor = 0.28 if ch == "，" else (0.22 if ch in ("。", "、") else 0.16)
            th = size * factor
            return th, 0.45 * size, 0.88 * size - th
        if ch in ("“", "”"):
            return size * 0.32, 0.50 * size, 0.08 * size
        if ch == "·":
            th = size * 0.18
            return th, 0.35 * size, 0.48 * size - 0.5 * th
        if ch in ("-", "~"):
            th = size * 0.22
            return th, 0.72 * size, 0.52 * size - 0.5 * th
        if ch == "—":
            th = size * 0.20
            return th, 1.25 * size, 0.52 * size - 0.5 * th
        if ch == "=":
            th = size * 0.34
            return th, 0.85 * size, 0.52 * size - 0.5 * th
        if ch in ("+", "<", "≤", "±"):
            th = size * 0.62
            return th, 0.85 * size, 0.54 * size - 0.5 * th
        if ch in ("：", "；", ":", ";"):
            th = size * 0.48
            return th, 0.48 * size, 0.62 * size - 0.5 * th
        if ch in ("(", ")", "（", "）", "/", "？", "?", "%"):
            th = size * 0.86
            return th, 0.85 * size, 0.50 * size - 0.5 * th
        th = size * clamp(rel_h, 0.45, 0.90)
        return th, 0.95 * size, 0.50 * size - 0.5 * th

    def compute_draw_dims(self, raw_ch: str, variant_meta: dict, size: float):
        alpha = self.lib.load_alpha_mask(variant_meta)
        gh, gw = alpha.shape
        th, max_w, y_top = self.char_box_metrics(raw_ch, variant_meta, size)
        scale = th / max(1, gh)
        dw, dh = gw * scale, th
        if dw > max_w:
            fit = max_w / dw
            dw *= fit
            dh *= fit
            y_top += 0.5 * (th - dh)
        ch = SUP_MAP.get(raw_ch, raw_ch)
        if ch == "—" and dw < 0.75 * size:
            dw = 0.85 * size
        return max(2, int(round(dw))), max(2, int(round(dh))), y_top

    def char_advance(self, raw_ch: str, size: float, prev_ch: str | None = None) -> float:
        if raw_ch.isspace():
            return size * 0.30
        variant = self.lib.get_variants(raw_ch)[0]
        dw, _, _ = self.compute_draw_dims(raw_ch, variant, size)
        ch = SUP_MAP.get(raw_ch, raw_ch)
        if raw_ch in SUP_MAP:
            return max(dw + 0.05 * size, size * 0.25)
        if ch in ("，", "。", "、"):
            return max(dw + 0.14 * size, size * 0.44)
        if ch in ("：", "；", ":", ";"):
            return max(dw + 0.16 * size, size * 0.42)
        if ch == ".":
            return max(dw + 0.08 * size, size * 0.22)
        if ch in ("（", "）", "(", ")"):
            return max(dw + 0.08 * size, size * 0.34)
        if ch.isascii() and prev_ch and prev_ch.isascii() and not prev_ch.isspace():
            return dw + 0.06 * size
        if ch.isascii():
            return dw + 0.09 * size
        return max(dw + 0.11 * size, size * 0.80 if is_cjk(ch) else dw + 0.10 * size)

    def measure_text_width(self, text: str, size: float) -> float:
        w, prev = 0.0, None
        for ch in text:
            w += self.char_advance(ch, size, prev)
            prev = ch
        return w

    def draw_char(self, canvas: Image.Image, raw_ch: str, x: float, y_base: float, size: float, prev_ch=None, dark_bias=0) -> float:
        if raw_ch.isspace():
            return size * 0.30
        m = self.messiness
        variant = self.pick_variant(raw_ch)
        alpha_arr = self.lib.load_alpha_mask(variant)

        c_size = size * (1.0 + self.rng.uniform(-0.055 * m, 0.055 * m))
        dw, dh, y_top = self.compute_draw_dims(raw_ch, variant, c_size)

        # 2x supersampled stroke-width normalization (~0.6mm gel-pen consistency)
        sw, sh = max(2, dw * 2), max(2, dh * 2)
        a_hi = Image.fromarray(alpha_arr, mode="L").resize((sw, sh), Image.Resampling.LANCZOS)
        a_np = np.asarray(a_hi, dtype=np.float32)
        mask = a_np > 110.0
        area = int(mask.sum())
        if area > 12 and raw_ch not in (".", "。", "、", "·", "，", "：", "；", ":", ";"):
            pad_m = np.pad(mask, 1, mode="constant", constant_values=False)
            b = mask & (~pad_m[:-2, 1:-1] | ~pad_m[2:, 1:-1] | ~pad_m[1:-1, :-2] | ~pad_m[1:-1, 2:])
            w_est = 2.0 * area / max(int(b.sum()), 1)
            if w_est > 5.4:
                eroded = np.asarray(a_hi.filter(ImageFilter.MinFilter(3)), dtype=np.float32)
                t = clamp((w_est - 5.2) / 2.5, 0.0, 0.75)
                a_np = a_np * (1.0 - t) + eroded * t
                if w_est > 6.8:
                    eroded2 = np.asarray(Image.fromarray(a_np.astype(np.uint8), mode="L").filter(ImageFilter.MinFilter(3)), dtype=np.float32)
                    t2 = clamp((w_est - 6.6) / 2.5, 0.0, 0.60)
                    a_np = a_np * (1.0 - t2) + eroded2 * t2
            elif w_est < 4.5:
                dilated = np.asarray(a_hi.filter(ImageFilter.MaxFilter(3)), dtype=np.float32)
                t = clamp((4.5 - w_est) / 2.6, 0.0, 0.68)
                a_np = np.clip(a_np * (1.0 - t) + dilated * t, 0.0, 255.0)
            a_np = np.clip(a_np * 1.10, 0.0, 255.0)
        else:
            a_np = np.clip(a_np * 1.12, 0.0, 255.0)

        a_final = np.asarray(
            Image.fromarray(np.clip(a_np, 0, 255).astype(np.uint8), mode="L").resize((dw, dh), Image.Resampling.LANCZOS),
            dtype=np.uint8,
        )
        v = max(10, self.rng.randint(18, 36) - dark_bias)
        rgba = np.zeros((dh, dw, 4), dtype=np.uint8)
        rgba[:, :, 0] = v
        rgba[:, :, 1] = v + 2
        rgba[:, :, 2] = v + 5
        rgba[:, :, 3] = a_final
        glyph_img = Image.fromarray(rgba, mode="RGBA")

        angle = self.rng.uniform(-3.8 * m, 3.8 * m)
        if raw_ch == "/":
            angle -= 14.0
        if abs(angle) > 0.25:
            glyph_img = glyph_img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)

        cx_shift = (glyph_img.width - dw) / 2.0
        cy_shift = (glyph_img.height - dh) / 2.0
        dy = self.rng.uniform(-2.2 * m, 2.2 * m)
        canvas.alpha_composite(glyph_img, (int(round(x - cx_shift)), int(round(y_base + y_top + dy - cy_shift))))

        adv = self.char_advance(raw_ch, size, prev_ch)
        jitter = self.rng.uniform(-0.045 * m, 0.055 * m) * size
        return max(adv + jitter, size * 0.18)

    def draw_line_text(self, canvas, text: str, x_start: float, y_base: float, size: float, dark_bias: int = 0) -> float:
        x = float(x_start)
        m = self.messiness
        wave_freq = self.rng.uniform(0.004, 0.008)
        wave_phase = self.rng.uniform(0, 6.28)
        wave_amp = 3.2 * m
        slope = self.rng.uniform(-0.0028 * m, 0.0024 * m)
        prev = None
        for ch in text:
            dx = x - x_start
            y_cur = y_base + math.sin(dx * wave_freq + wave_phase) * wave_amp + dx * slope
            x += self.draw_char(canvas, ch, x, y_cur, size, prev_ch=prev, dark_bias=dark_bias)
            prev = ch
        return x

    def wrap_text(self, text: str, max_width: float, size: float, first_line_indent_px: int = 0) -> list[str]:
        no_start = set("，。、；：！？）】》”.,;:!?)~")
        no_end = set("（【《(“")
        raw_tokens = re.findall(r"[A-Za-z0-9./~+-]+(?:℃|⁻¹|²|¹|³)?|\s+|.", text)
        tokens = []
        for tok in raw_tokens:
            if tokens and tok and tok[0] in no_start and not tok.isspace():
                tokens[-1] += tok
            elif tokens and tokens[-1][-1] in no_end and not tok.isspace():
                tokens[-1] += tok
            else:
                tokens.append(tok)

        lines, cur, cur_w, prev_tail = [], "", float(first_line_indent_px), None
        for tok in tokens:
            if not cur and tok.isspace():
                continue
            tw, p = 0.0, prev_tail
            for ch in tok:
                tw += self.char_advance(ch, size, p)
                p = ch
            if cur and (cur_w + tw > max_width):
                lines.append(cur.rstrip())
                if tok.isspace():
                    cur, cur_w, prev_tail = "", 0.0, None
                else:
                    cur, cur_w, prev_tail = tok, self.measure_text_width(tok, size), tok[-1]
            else:
                cur += tok
                cur_w += tw
                prev_tail = tok[-1] if tok else prev_tail
        if cur.strip():
            lines.append(cur.rstrip())
        return lines

    def render_page(self, page_spec: dict, out_path: str) -> str:
        tpl_path = self.resolve_path(page_spec["template"])
        base = Image.open(tpl_path).convert("RGBA")
        W, H = base.size

        margin_left = int(W * float(page_spec.get("margin_left_ratio", 0.132)))
        margin_right = int(W * float(page_spec.get("margin_right_ratio", 0.862)))
        content_w = margin_right - margin_left
        is_first = page_spec["page_number"] == 1
        y = int(H * float(page_spec.get("y_start_ratio", 0.128 if is_first else 0.106)))
        bot_limit = int(H * float(page_spec.get("bot_limit_ratio", 0.935)))
        line_h = int(self.base_size * float(page_spec.get("line_h_ratio", 1.54)))
        m = self.messiness
        draw = ImageDraw.Draw(base)

        for blk in page_spec["blocks"]:
            btype = blk["type"]
            if btype == "cover_fields":
                meta_path = os.path.join(os.path.dirname(tpl_path), "cover_fields.json")
                cover_meta = json.load(open(meta_path, "r", encoding="utf-8")) if os.path.exists(meta_path) else {}
                for fld in blk.get("fields", []):
                    fmeta = cover_meta.get(fld.get("field_key", ""), {})
                    x_start = float(fld.get("x_start", fmeta.get("x_start", 480)))
                    x_end = float(fld.get("x_end", fmeta.get("x_end", 1370)))
                    y_ul = float(fld.get("y_underline", fmeta.get("y_underline", 764)))
                    sz = float(fld.get("size", self.base_size))
                    txt = fld["text"]
                    tw = self.measure_text_width(txt, sz)
                    if fld.get("align", "center") == "center":
                        tx = x_start + max(0.0, (x_end - x_start - tw) * 0.5) + self.rng.uniform(-10, 10) * m
                    else:
                        tx = x_start + float(fld.get("x_offset", 42)) + self.rng.uniform(-6, 6) * m
                    self.draw_line_text(base, txt, tx, y_ul - sz * 1.12 + self.rng.uniform(-2.0, 2.0) * m, sz, dark_bias=4)

            elif btype == "spacer":
                y += int(line_h * float(blk.get("lines", 1.0)))

            elif btype == "title":
                sz = int(self.base_size * 1.22)
                tw = self.measure_text_width(blk["text"], sz)
                tx = margin_left + max(0, (content_w - tw) / 2) + self.rng.uniform(-12, 12) * m
                self.draw_line_text(base, blk["text"], tx, y, sz, dark_bias=6)
                y += int(line_h * 1.28)

            elif btype == "heading":
                y += int(line_h * 0.24)
                self.draw_line_text(base, blk["text"], margin_left, y, int(self.base_size * 1.08), dark_bias=4)
                y += int(line_h * 1.10)

            elif btype == "paragraph":
                sz = self.base_size
                indent_px = int(blk.get("indent", 0) * sz * 1.05)
                for idx, ln in enumerate(self.wrap_text(blk["text"], content_w, sz, first_line_indent_px=indent_px)):
                    x0 = margin_left + (indent_px if idx == 0 else 0) + self.rng.uniform(-5, 5) * m
                    self.draw_line_text(base, ln, x0, y, sz)
                    y += int(line_h * (1.0 + self.rng.uniform(-0.035, 0.045) * m))
                y += int(line_h * 0.14)

            elif btype == "two_col_list":
                col2_x = margin_left + int(content_w * 0.53)
                for left_txt, right_txt in blk["rows"]:
                    if left_txt:
                        self.draw_line_text(base, left_txt, margin_left + 10, y, self.base_size)
                    if right_txt:
                        self.draw_line_text(base, right_txt, col2_x, y, self.base_size)
                    y += int(line_h * 1.04)
                y += int(line_h * 0.18)

            elif btype == "equation_fraction":
                sz = int(self.base_size * 0.96)
                y += int(line_h * 0.16)
                ln_w, ld_w = self.measure_text_width(blk["left_num"], sz), self.measure_text_width(blk["left_den"], sz)
                l_bar_w = max(ln_w, ld_w) + 24
                op_w = self.measure_text_width(blk["op"], sz)
                rn_w, rd_w = self.measure_text_width(blk["right_num"], sz), self.measure_text_width(blk["right_den"], sz)
                r_bar_w = max(rn_w, rd_w) + 24
                cx = margin_left + (content_w - (l_bar_w + 40 + op_w + r_bar_w)) * 0.46
                bar_y = y + sz + 6
                self.draw_line_text(base, blk["left_num"], cx + (l_bar_w - ln_w) / 2, y, sz)
                draw_wobbly_line(draw, cx, bar_y, cx + l_bar_w, bar_y, self.rng, m, width=3)
                self.draw_line_text(base, blk["left_den"], cx + (l_bar_w - ld_w) / 2, bar_y + 8, sz)
                op_x = cx + l_bar_w + 20
                self.draw_line_text(base, blk["op"], op_x, y + sz * 0.48, sz)
                rx = op_x + op_w + 20
                self.draw_line_text(base, blk["right_num"], rx + (r_bar_w - rn_w) / 2, y, sz)
                draw_wobbly_line(draw, rx, bar_y, rx + r_bar_w, bar_y, self.rng, m, width=3)
                self.draw_line_text(base, blk["right_den"], rx + (r_bar_w - rd_w) / 2, bar_y + 8, sz)
                y += int(line_h * 1.88)

            elif btype in ("equation_inline_frac", "equation_lgp"):
                sz = int(self.base_size * 0.96)
                y += int(line_h * 0.16)
                pre_w = self.measure_text_width(blk["prefix"], sz)
                num_w, den_w = self.measure_text_width(blk["num"], sz), self.measure_text_width(blk["den"], sz)
                bar_w = max(num_w, den_w) + 24
                suf_w = self.measure_text_width(blk["suffix"], sz)
                cx = margin_left + (content_w - (pre_w + 36 + bar_w + suf_w)) * 0.42
                bar_y = y + sz + 6
                x_end = self.draw_line_text(base, blk["prefix"], cx, y + sz * 0.48, sz)
                fx = x_end + 18
                self.draw_line_text(base, blk["num"], fx + (bar_w - num_w) / 2, y, sz)
                draw_wobbly_line(draw, fx, bar_y, fx + bar_w, bar_y, self.rng, m, width=3)
                self.draw_line_text(base, blk["den"], fx + (bar_w - den_w) / 2, bar_y + 8, sz)
                self.draw_line_text(base, blk["suffix"], fx + bar_w + 18, y + sz * 0.48, sz)
                y += int(line_h * 1.88)

            elif btype == "image_with_labels":
                fig = Image.open(self.resolve_path(blk["image"])).convert("RGBA")
                target_w = int(content_w * blk.get("width_ratio", 0.34))
                target_h = int(fig.height * (target_w / fig.width))
                fig_r = fig.resize((target_w, target_h), Image.Resampling.LANCZOS)
                y += int(line_h * 0.20)
                base.alpha_composite(fig_r, (margin_left + 26, int(y)))
                lx, ly, sz = margin_left + target_w + 68, y + int(target_h * 0.16), int(self.base_size * 0.94)
                for lbl in blk["labels"]:
                    for ln in self.wrap_text(lbl, margin_right - lx, sz):
                        self.draw_line_text(base, ln, lx, ly, sz)
                        ly += int(line_h * 1.08)
                    ly += int(line_h * 0.22)
                y = max(y + target_h, ly) + int(line_h * 0.28)

            elif btype == "image_center":
                fig = Image.open(self.resolve_path(blk["image"])).convert("RGBA")
                target_w = int(content_w * blk.get("width_ratio", 0.76))
                target_h = int(fig.height * (target_w / fig.width))
                fig_r = fig.resize((target_w, target_h), Image.Resampling.LANCZOS)
                y += int(line_h * 0.18)
                ix = margin_left + (content_w - target_w) // 2
                base.alpha_composite(fig_r, (ix, int(y)))
                if blk.get("box_border"):
                    draw.rectangle([ix, int(y), ix + target_w, int(y) + target_h], outline=(185, 185, 185), width=2)
                y += target_h + int(line_h * 0.20)
                if blk.get("caption"):
                    sz = int(self.base_size * 0.92)
                    tw = self.measure_text_width(blk["caption"], sz)
                    self.draw_line_text(base, blk["caption"], margin_left + (content_w - tw) / 2, y, sz)
                    y += int(line_h * 1.05)

            elif btype == "table":
                y += int(line_h * 0.20)
                headers = blk["headers"]
                n_cols = len(headers)
                data_rows = blk.get("data_rows", [])
                n_rows = len(data_rows) if data_rows else int(blk.get("rows", 4))
                ratios = blk.get("col_ratios") or [1.0 / n_cols] * n_cols
                col_xs = [float(margin_left)]
                for r_w in ratios:
                    col_xs.append(col_xs[-1] + content_w * r_w)
                row_h = int(line_h * float(blk.get("row_h_ratio", 1.12)))
                total_h = row_h * (n_rows + 1)

                for r in range(n_rows + 2):
                    draw_wobbly_line(draw, margin_left, y + r * row_h, margin_right, y + r * row_h, self.rng, m, width=3)
                for cx in col_xs:
                    draw_wobbly_line(draw, cx, y, cx, y + total_h, self.rng, m, width=3)

                sz = int(self.base_size * 0.80)
                for c, htxt in enumerate(headers):
                    cw = col_xs[c + 1] - col_xs[c]
                    tw = self.measure_text_width(htxt, sz)
                    cur_sz = sz if tw <= cw - 12 else sz * (cw - 12) / tw
                    tw2 = self.measure_text_width(htxt, cur_sz)
                    self.draw_line_text(base, htxt, col_xs[c] + max(6, (cw - tw2) / 2), y + int((row_h - cur_sz) * 0.44), cur_sz)

                cell_sz = int(self.base_size * 0.84)
                for r_idx, r_vals in enumerate(data_rows):
                    ry_top = y + (r_idx + 1) * row_h
                    for c, val in enumerate(r_vals[:n_cols]):
                        if not val:
                            continue
                        cw = col_xs[c + 1] - col_xs[c]
                        tw = self.measure_text_width(val, cell_sz)
                        cur_sz = cell_sz if tw <= cw - 14 else cell_sz * (cw - 14) / tw
                        tw2 = self.measure_text_width(val, cur_sz)
                        vx = col_xs[c] + max(6, (cw - tw2) / 2) + self.rng.uniform(-4, 4) * m
                        vy = ry_top + int((row_h - cur_sz) * 0.44) + self.rng.uniform(-2, 2) * m
                        self.draw_line_text(base, val, vx, vy, cur_sz)
                y += total_h + int(line_h * 0.35)

        if page_spec.get("is_cover"):
            print(f"[OK] Page {page_spec['page_number']} (cover) rendered -> {out_path}")
        elif y > bot_limit:
            print(f"[WARNING] Page {page_spec['page_number']} overflowed bottom margin ({y} > {bot_limit}).")
        else:
            print(f"[OK] Page {page_spec['page_number']} rendered ({int(y / bot_limit * 100)}% height) -> {out_path}")

        base.convert("RGB").save(out_path, quality=95)
        return out_path


def cmd_render(args) -> None:
    os.makedirs(args.output_dir, exist_ok=True)
    with open(args.content, "r", encoding="utf-8") as f:
        data = json.load(f)

    lib = RealGlyphLibrary(args.manifest)
    texts = extract_texts_from_content(args.content)
    missing = sorted({SUP_MAP.get(ch, ch) for ch in "".join(texts) if not ch.isspace() and SUP_MAP.get(ch, ch) not in lib.chars})
    if missing:
        print(f"[ERROR] Missing {len(missing)} character(s) in {args.manifest}: {''.join(missing)}", file=sys.stderr)
        sys.exit(1)

    print(f"[VERIFIED] 100% of characters covered in {args.manifest} ({len(lib.chars)} unique glyphs).")
    assets_dir = args.assets_dir or os.path.dirname(os.path.dirname(os.path.abspath(args.content)))
    renderer = RealHandwritingRenderer(lib, assets_dir, base_size=args.font_size, messiness=args.messiness, seed=args.seed)

    page_paths = []
    for p in data["pages"]:
        out_png = os.path.join(args.output_dir, f"page_{p['page_number']}.png")
        renderer.render_page(p, out_png)
        page_paths.append(out_png)

    if page_paths:
        pdf_path = os.path.join(args.output_dir, args.pdf_name)
        imgs = [Image.open(p).convert("RGB") for p in page_paths]
        imgs[0].save(pdf_path, save_all=True, append_images=imgs[1:], resolution=200.0)
        print(f"[PDF] Combined {len(imgs)}-page handwritten document -> {pdf_path}")


# ---------------------------------------------------------------------------
# CLI Argument Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="handwrite.py", description="Personal Handwriting Studio CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize a clean personal handwriting workspace")
    p_init.add_argument("--workspace", required=True, help="Target workspace directory")
    p_init.add_argument("--with-example", action="store_true", help="Copy example USST lab report templates & sample JSON")
    p_init.set_defaults(func=cmd_init)

    p_check = sub.add_parser("check", help="Check glyph coverage against a draft and list missing characters")
    p_check.add_argument("--content", required=True, help="Path to pages.json or plain text draft")
    p_check.add_argument("--manifest", required=True, help="Path to glyphs/manifest.json")
    p_check.add_argument("--output", required=True, help="Output JSON report path (companion .md also saved)")
    p_check.add_argument("--grid-dir", default=None, help="Optional directory to save printable A4 missing-char grids")
    p_check.set_defaults(func=cmd_check)

    p_pre = sub.add_parser("preprocess", help="Decode photo (DNG/HEIC/JPG/PNG) & extract shadow-free RGBA ink")
    p_pre.add_argument("--input", required=True, help="Input photo path")
    p_pre.add_argument("--output-dir", required=True, help="Directory for <base>_clean.png and <base>_ink.png")
    p_pre.add_argument("--crop", default=None, help="Optional normalized crop box x0,y0,x1,y1 (0.0~1.0)")
    p_pre.add_argument("--hi", type=float, default=0.80, help="Upper reflectance threshold (default: 0.80)")
    p_pre.add_argument("--lo", type=float, default=0.44, help="Lower reflectance threshold (default: 0.44)")
    p_pre.add_argument("--max-dim", type=int, default=4032, help="Max image dimension after resize (default: 4032)")
    p_pre.set_defaults(func=cmd_preprocess)

    p_ing = sub.add_parser("ingest", help="Row-guided slicing of handwritten photo into glyph library + visual audit")
    p_ing.add_argument("--input", required=True, help="Input photo or preprocessed *_ink.png")
    p_ing.add_argument("--rows-spec", required=True, help="JSON specifying rows/columns of characters")
    p_ing.add_argument("--manifest", required=True, help="Target glyphs/manifest.json")
    p_ing.add_argument("--contact-dir", required=True, help="Directory for debug overlay and audit contact sheet")
    p_ing.add_argument("--source-id", default=None, help="Optional source tag stored in manifest")
    p_ing.add_argument("--crop", default=None, help="Optional normalized crop x0,y0,x1,y1 if input is raw photo")
    p_ing.add_argument("--hi", type=float, default=0.80)
    p_ing.add_argument("--lo", type=float, default=0.44)
    p_ing.add_argument("--dry-run", action="store_true", help="Generate audit images without modifying manifest.json")
    p_ing.set_defaults(func=cmd_ingest)

    p_cs = sub.add_parser("contact-sheet", help="Render a visual audit contact sheet from manifest.json")
    p_cs.add_argument("--manifest", required=True, help="Path to glyphs/manifest.json")
    p_cs.add_argument("--output", required=True, help="Output PNG path")
    p_cs.add_argument("--chars", default=None, help="Optional subset of characters to include")
    p_cs.set_defaults(func=cmd_contact_sheet)

    p_ren = sub.add_parser("render", help="Render structured pages.json onto templates and export PDF")
    p_ren.add_argument("--content", required=True, help="Path to pages.json")
    p_ren.add_argument("--manifest", required=True, help="Path to glyphs/manifest.json")
    p_ren.add_argument("--output-dir", required=True, help="Directory for rendered PNGs and PDF")
    p_ren.add_argument("--assets-dir", default=None, help="Base directory for resolving relative template/image paths")
    p_ren.add_argument("--pdf-name", default="handwritten_output.pdf", help="Output combined PDF filename")
    p_ren.add_argument("--font-size", type=int, default=32, help="Base handwriting height in px (default: 32)")
    p_ren.add_argument("--messiness", type=float, default=0.72, help="Irregularity 0.0~1.0 (default: 0.72)")
    p_ren.add_argument("--seed", type=int, default=42, help="Random seed")
    p_ren.set_defaults(func=cmd_render)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

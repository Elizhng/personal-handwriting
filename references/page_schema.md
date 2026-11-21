# Page JSON Schema (`pages.json` & `cover_fields.json`)

This document describes the structured JSON format consumed by `uv run scripts/handwrite.py render`.

## Top-Level Structure

```json
{
  "pages": [
    {
      "page_number": 1,
      "is_cover": true,
      "template": "templates/page_1_cover.png",
      "blocks": [ ... ]
    },
    {
      "page_number": 2,
      "template": "templates/page_2_content.png",
      "margin_left_ratio": 0.132,
      "margin_right_ratio": 0.862,
      "y_start_ratio": 0.115,
      "bot_limit_ratio": 0.935,
      "line_h_ratio": 1.48,
      "blocks": [ ... ]
    }
  ]
}
```

### Page Fields

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `page_number` | int | required | 1-based page index (`page_<N>.png`). |
| `template` | string | required | Path to blank template PNG (resolved relative to `--assets-dir` or absolute). |
| `is_cover` | bool | `false` | If `true`, skips vertical overflow check. |
| `margin_left_ratio` | float | `0.132` | Left content margin as fraction of page width. |
| `margin_right_ratio` | float | `0.862` | Right content margin as fraction of page width. |
| `y_start_ratio` | float | `0.128` (p1) / `0.106` | Top starting Y as fraction of page height. |
| `bot_limit_ratio` | float | `0.935` | Bottom margin limit as fraction of page height. |
| `line_h_ratio` | float | `1.54` | Line height multiplier relative to `--font-size`. |

---

## Supported Block Types

### 1. `cover_fields`
Places handwritten text on cover underlines using coordinates from `cover_fields.json` (in the same directory as the cover template) or explicit `x_start`/`x_end`/`y_underline`.

```json
{
  "type": "cover_fields",
  "fields": [
    { "field_key": "course_name", "text": "物理化学实验", "size": 38, "align": "center" },
    { "x_start": 487, "x_end": 657, "y_underline": 969, "text": "2026级1班", "size": 30, "align": "center" }
  ]
}
```

### 2. `title` & `heading`
- `title`: Centered large title (`1.22 × base_size`).
- `heading`: Left-aligned section header (`1.08 × base_size`).

```json
{ "type": "heading", "text": "一、实验目的" }
```

### 3. `paragraph`
Token-aware wrapped paragraph (keeps numbers/units unbroken, prevents leading closing punctuation).
- `indent`: Optional first-line indent in character widths (e.g., `2`).

```json
{ "type": "paragraph", "indent": 2, "text": "测定不同温度下液体的饱和蒸气压，并求算平均摩尔气化焓。" }
```

### 4. `two_col_list`
Two-column aligned list (ideal for equipment & reagents).

```json
{
  "type": "two_col_list",
  "rows": [
    ["1. 恒温水浴", "2. 数字式真空测压仪"],
    ["3. 平衡管", "4. 蒸馏水"]
  ]
}
```

### 5. `equation_fraction` & `equation_inline_frac` (alias: `equation_lgp`)
Hand-drawn wobbly fraction bars with centered numerators and denominators.

```json
{
  "type": "equation_fraction",
  "left_num": "d ln P",
  "left_den": "dT",
  "op": "=",
  "right_num": "ΔH",
  "right_den": "RT²"
}
```

```json
{
  "type": "equation_inline_frac",
  "prefix": "lg P = -",
  "num": "ΔH",
  "den": "2.303RT",
  "suffix": " + C"
}
```

### 6. `table`
Hand-drawn wobbly grid table with auto-shrinking centered headers and optional `data_rows`.

```json
{
  "type": "table",
  "headers": ["序号", "温度(℃)", "P(kPa)", "备注"],
  "col_ratios": [0.15, 0.25, 0.30, 0.30],
  "row_h_ratio": 1.12,
  "data_rows": [
    ["1", "25.0", "3.17", "正常"],
    ["2", "30.0", "4.24", "正常"]
  ]
}
```
If `data_rows` is omitted, set `"rows": 6` to draw an empty table with 6 blank rows.

### 7. `image_center`, `image_with_labels`, `spacer`
- `image_center`: `{ "type": "image_center", "image": "path/to/fig.png", "width_ratio": 0.72, "caption": "图1 示意图", "box_border": false }`
- `image_with_labels`: `{ "type": "image_with_labels", "image": "path/to/fig.png", "width_ratio": 0.34, "labels": ["1. 部件A", "2. 部件B"] }`
- `spacer`: `{ "type": "spacer", "lines": 0.6 }`

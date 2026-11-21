# Personal Handwriting Studio (`personal-handwriting`)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-Antigravity%20%7C%20Claude%20Code%20%7C%20Codex%20%7C%20Cursor-green)](./SKILL.md)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-yellow)](./scripts/handwrite.py)

> 将你自己的真实手写照片转化为可复用的个人单字库，通过纯程序化排版渲染出高度逼真的多页手写文档与 PDF（支持自定义纸张模板、手绘抖动表格、分式公式、多变体防重复与笔宽归一化）——**零 AI 生图伪影，100% 真实个人字迹**。
>
> Turn real pen-on-paper photos of your own handwriting into a personal glyph library and programmatically render realistic multi-page handwritten documents and PDFs — with zero AI image-generation artifacts.

---

## 核心特性 / Features

- **100% 真实字迹，绝无 AI 乱码**：全部文字由你本人的透明底 RGBA 单字库程序化拼排，支持单字多变体滑动窗口去重（相邻绝不重复同一变体）。
- **2× 超采样笔宽归一化**：自动统一不同批次照片、汉字、数字、英文字母与符号的笔画粗细（模拟 0.6 mm 黑中性笔质感），叠加自然基线微波动、字符倾角与字距扰动。
- **极简缺字预检**：自动扫描目标文稿与个人字库的差集，仅列出缺失字（可选导出 A4 补字网格），无需从零重写几千字。
- **跨平台免 OCR 采字**：默认采用三段带微倾斜追踪 + 8-连通域聚类的行序引导切字（macOS / Linux / Windows 全支持），内置 `.DNG` 纯 Python 解码与 `.HEIC` 自动解码；另附可选 macOS Apple Vision OCR 脚本。
- **强制视觉审计防脏数据**：每次入库前支持 `--dry-run` 生成边界框调试图与单字拼图，先审后入库。
- **零配置单文件 CLI**：`scripts/handwrite.py` 内置 PEP 723 依赖声明，安装 `uv` 后开箱即用。

---

## 安装到你的 AI Agent / Installation

本仓库遵循跨平台标准 Agent Skill 规范，支持 **Antigravity、Claude Code、Codex、Cursor**：

### 方式 1：使用 `npx skills`（推荐）
```bash
npx skills add Elizhng/personal-handwriting
```

### 方式 2：手动克隆到对应 Agent 的 Skills 目录
```bash
# Antigravity (项目级 / 全局)
git clone https://github.com/Elizhng/personal-handwriting.git .agents/skills/personal-handwriting
# 或全局: ~/.gemini/config/skills/personal-handwriting

# Claude Code (项目级 / 全局)
git clone https://github.com/Elizhng/personal-handwriting.git .claude/skills/personal-handwriting
# 或全局: ~/.claude/skills/personal-handwriting

# Codex
git clone https://github.com/Elizhng/personal-handwriting.git .agents/skills/personal-handwriting
```

---

## 快速上手 / Quick Start

前置依赖：安装 [uv](https://docs.astral.sh/uv/)（或 Python 3.10+、`pillow`、`numpy`）。

```bash
# 1. 初始化你的个人工作区（附带空白上理工实验报告模板示例）
uv run scripts/handwrite.py init --workspace ./my_handwriting --with-example

# 2. 对目标文稿做缺字预检（支持 pages.json 或纯文本 .txt）
uv run scripts/handwrite.py check \
  --content ./my_handwriting/content/sample_pages.json \
  --manifest ./my_handwriting/glyphs/manifest.json \
  --output ./my_handwriting/coverage_report.json \
  --grid-dir ./my_handwriting/missing_grid

# 3. 白纸黑笔按行写完缺字并拍照，做去阴影与透明墨迹提取
uv run scripts/handwrite.py preprocess \
  --input ./my_handwriting/photos/my_sheet.jpg \
  --output-dir ./my_handwriting/preprocessed

# 4. 试切并生成调试图 + 单字拼图（先视觉核查，无误后再去掉 --dry-run 入库）
uv run scripts/handwrite.py ingest \
  --input ./my_handwriting/preprocessed/my_sheet_ink.png \
  --rows-spec ./my_handwriting/content/sample_rows_spec.json \
  --manifest ./my_handwriting/glyphs/manifest.json \
  --contact-dir ./my_handwriting/contact_sheets \
  --dry-run

# 5. 渲染多页手写图与合并 PDF
uv run scripts/handwrite.py render \
  --content ./my_handwriting/content/sample_pages.json \
  --manifest ./my_handwriting/glyphs/manifest.json \
  --assets-dir ./my_handwriting \
  --output-dir ./my_handwriting/output \
  --pdf-name demo_report.pdf
```

---

## 仓库结构 / Repository Structure

```text
personal-handwriting/
├── SKILL.md                          # Agent Skill 核心工作流指令
├── README.md                         # 项目说明文档
├── LICENSE                           # MIT 协议
├── scripts/
│   ├── handwrite.py                  # 统一 PEP 723 CLI 引擎
│   └── optional_macos_ocr/
│       └── vision_ocr.swift          # 可选：macOS Apple Vision OCR 采字工具
├── references/
│   ├── page_schema.md                # pages.json 与 cover_fields.json 字段规范
│   └── macos_vision_ocr.md           # macOS OCR 自由稿采字指南
└── examples/
    └── usst_lab_report/              # 空白上理工实验报告模板与脱敏示例 JSON
```

---

## 隐私与安全声明 / Privacy Notice

- 本仓库**不包含**任何个人手写字库、个人照片或真实作业内容。
- `.gitignore` 默认忽略 `glyphs/`、`photos/`、`output/`、`contact_sheets/` 等目录，避免你在本地使用时误将个人笔迹上传到公开仓库。
- 本工具旨在帮助用户数字化个人笔迹、整理学习笔记与无障碍排版，请遵守所在学校或机构的学术规范。

## License

[MIT](./LICENSE)

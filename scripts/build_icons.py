"""把 SVG 图标渲染成多尺寸 PNG，再打包成多分辨率 .ico。

用法：
    python scripts/build_icons.py

输入：assets/icon_drafts/draft_b_chip_terminal.svg（选定方案）
输出：
    assets/icons/app_icon.ico       — 多分辨率 (16/32/48/64/128/256)
    assets/icons/app_icon_256.png   — 256x256 单图，README/官网用

依赖：PySide6.QtSvg（已有），Pillow（pip install Pillow）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
SVG_PATH = ROOT / "assets" / "icon_drafts" / "draft_b_chip_terminal.svg"
OUT_DIR = ROOT / "assets" / "icons"
ICO_SIZES = [16, 32, 48, 64, 128, 256]


def render_svg_to_png(svg_path: Path, size: int, out_path: Path) -> None:
    """QSvgRenderer + QPainter → 透明背景 PNG。"""
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise RuntimeError(f"SVG 无效：{svg_path}")
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    if not image.save(str(out_path), "PNG"):
        raise RuntimeError(f"PNG 保存失败：{out_path}")


def main() -> int:
    # QApplication 是 QSvgRenderer 间接所需的（Qt 资源加载链）
    _app = QApplication.instance() or QApplication(sys.argv)

    if not SVG_PATH.exists():
        print(f"ERROR: 找不到源 SVG: {SVG_PATH}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 渲染各尺寸 PNG 到临时位置
    png_paths: list[Path] = []
    for size in ICO_SIZES:
        png = OUT_DIR / f"_tmp_{size}.png"
        render_svg_to_png(SVG_PATH, size, png)
        png_paths.append(png)
        print(f"  rendered {size:>3}x{size}")

    # 2. 256x256 单图保留为最终资产
    ref_png = OUT_DIR / "app_icon_256.png"
    render_svg_to_png(SVG_PATH, 256, ref_png)
    print(f"  saved {ref_png.name}")

    # 3. Pillow 打包多分辨率 .ico
    #    传 sizes 参数给 .save 让 PIL 包含所有尺寸，但实际位图来源 256.png
    #    （PIL 用最大尺寸自动 downscale）。为了最佳质量，单独 prepare 各尺寸图。
    ico_path = OUT_DIR / "app_icon.ico"
    images = [Image.open(p) for p in png_paths]
    # PIL ICO 的多分辨率 API：base.save(ico_path, format='ICO', sizes=[(s,s),...])
    # PIL 内部会用 base 自动 downscale；为了保证我们 SVG 渲染的高质量小图
    # 用 append_images 把它们直接喂进去。
    # 但 ICO 多分辨率官方推荐：sizes 参数 + base image (256)。PIL 会用 NEAREST
    # downscale，质量不如 SVG 直接渲染。所以下面 hack：
    base = images[-1]  # 256
    # PIL append_images for ICO 只在 PIL ≥ 9.x 支持稳定
    base.save(
        ico_path, format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=images[:-1],
    )
    print(f"  saved {ico_path.name} ({len(ICO_SIZES)} resolutions)")

    # 4. 清理临时 PNG
    for p in png_paths:
        p.unlink(missing_ok=True)

    print(f"\nDone. ICO: {ico_path}, PNG: {ref_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

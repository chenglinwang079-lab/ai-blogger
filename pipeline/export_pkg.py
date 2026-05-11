"""导出：发布包"""

import json
import logging
import re
import shutil
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


def _generate_thumbnail(title: str, output_path: Path, config: dict) -> None:
    """生成缩略图：渐变背景 + 居中标题文字。"""
    width, height = 1280, 720

    # 渐变背景（深蓝 → 深紫）
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / height
        r = int(15 + 25 * ratio)
        g = int(10 + 5 * ratio)
        b = int(50 + 30 * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # 字体
    font_path = None
    fonts_dir = config["paths"].get("fonts_dir", "assets/fonts")
    # 尝试查找系统中文字体
    for candidate in [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]:
        if Path(candidate).exists():
            font_path = candidate
            break

    font = ImageFont.truetype(font_path, 56) if font_path else ImageFont.load_default()

    # 标题自动换行（每行约 14 个中文字符）
    wrapped = textwrap.fill(title, width=14)
    bbox = draw.textbbox((0, 0), wrapped, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = (height - th) // 2

    # 文字阴影
    draw.text((x + 2, y + 2), wrapped, fill=(0, 0, 0), font=font)
    draw.text((x, y), wrapped, fill="white", font=font)

    img.save(str(output_path), "PNG")


def export_package(script_id: str, config: dict) -> str:
    """导出发布资料包到 dist/<script_id>/（覆盖已有文件）。

    prediction.json 从 cheat/predictions/<script_id>.json 直接拷贝。
    """
    from pipeline import validate_script_id
    validate_script_id(script_id)
    output_dir = Path(config["paths"]["output_dir"]) / script_id
    cheat_root = Path(config["paths"]["cheat_root"])

    output_dir.mkdir(parents=True, exist_ok=True)

    # 拷贝 prediction.json
    pred_src = cheat_root / "predictions" / f"{script_id}.json"
    if pred_src.exists():
        shutil.copy2(pred_src, output_dir / "prediction.json")

    # 读取 manifest 生成 title.txt / description.txt
    title = ""
    manifest_path = cheat_root / "scripts" / script_id / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        title = manifest.get("title", "")

        (output_dir / "title.txt").write_text(title, encoding="utf-8")

        # description 从 final.md 提取
        final_path = cheat_root / "scripts" / script_id / "final.md"
        if final_path.exists():
            content = final_path.read_text(encoding="utf-8")
            (output_dir / "description.txt").write_text(content, encoding="utf-8")

    # thumbnail.png — 标题文字 + 渐变背景
    thumb_path = output_dir / "thumbnail.png"
    _generate_thumbnail(title, thumb_path, config)

    # tags.txt — 从标题和 prediction 提取
    tags = {"AI", "人工智能"}
    if title:
        for seg in re.split(r'[，。！？、\s]+', title):
            seg = seg.strip()
            if 2 <= len(seg) <= 8:
                tags.add(seg)
    pred_src = cheat_root / "predictions" / f"{script_id}.json"
    if pred_src.exists():
        pred = json.loads(pred_src.read_text(encoding="utf-8"))
        for key in ("drivers", "risks"):
            for item in pred.get(key, []):
                if isinstance(item, str) and 2 <= len(item) <= 8:
                    tags.add(item)
    tags_path = output_dir / "tags.txt"
    tags_path.write_text(",".join(sorted(tags)[:8]), encoding="utf-8")

    # 更新 manifest
    from pipeline import update_manifest
    script_dir = cheat_root / "scripts" / script_id
    update_manifest(script_dir, "export")

    # 完整性检查
    required = ["final.mp4", "audio.wav", "title.txt", "description.txt", "tags.txt", "thumbnail.png", "prediction.json"]
    missing = [f for f in required if not (output_dir / f).exists()]
    if missing:
        logger.warning(f"发布包缺失: {missing}")
    else:
        logger.info(f"发布包完整，共 {len(required)} 项")
    for fname in required:
        fpath = output_dir / fname
        if fpath.exists():
            logger.debug(f"  {fname}: {fpath.stat().st_size / 1024:.1f} KB")

    return str(output_dir)

"""共享图片工具：字体解析 + 文本换行。"""

from pathlib import Path

from PIL import ImageDraw, ImageFont

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

_FONT_MAP = {
    "Microsoft YaHei": "C:/Windows/Fonts/msyh.ttc",
    "SimHei": "C:/Windows/Fonts/simhei.ttf",
    "SimSun": "C:/Windows/Fonts/simsun.ttc",
    "FangSong": "C:/Windows/Fonts/simfang.ttf",
}


def resolve_font_path(config: dict) -> str | None:
    """从 config 解析字体文件路径。优先 assets/fonts/，其次系统字体。

    4 级 fallback：
    1. assets/fonts/{font_name}.{ttf|ttc|otf}
    2. assets/fonts/ 下任意字体
    3. 系统字体映射（按 config.video.subtitle_font 名称）
    4. 任意系统中文字体
    """
    fonts_dir = Path(config["paths"].get("fonts_dir", "assets/fonts"))
    if not fonts_dir.is_absolute():
        fonts_dir = _PROJECT_ROOT / fonts_dir
    font_name = config.get("video", {}).get("subtitle_font", "Microsoft YaHei")

    # 1. assets/fonts/ 下按名称查找
    for ext in (".ttf", ".ttc", ".otf"):
        candidate = fonts_dir / f"{font_name}{ext}"
        if candidate.exists():
            return str(candidate)

    # 2. assets/fonts/ 下任意字体
    if fonts_dir.exists():
        for f in fonts_dir.iterdir():
            if f.suffix.lower() in (".ttf", ".ttc", ".otf"):
                return str(f)

    # 3. 按名称映射系统字体
    if font_name in _FONT_MAP and Path(_FONT_MAP[font_name]).exists():
        return _FONT_MAP[font_name]

    # 4. 任意系统中文字体
    for sys_font in _FONT_MAP.values():
        if Path(sys_font).exists():
            return sys_font

    return None


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """按像素宽度自动换行。"""
    lines = []
    current = ""
    for ch in text:
        test = current + ch
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines

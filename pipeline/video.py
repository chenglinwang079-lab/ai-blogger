"""渲染：模板视频 + 字幕 + BGM"""

import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


def _create_background(width: int, height: int) -> np.ndarray:
    """深色渐变背景。"""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        ratio = y / height
        r = int(10 + 20 * ratio)
        g = int(10 + 15 * ratio)
        b = int(30 + 40 * ratio)
        img[y, :] = [r, g, b]
    return img


def _render_frame(
    width: int,
    height: int,
    text: str,
    keyword: str,
    font_path: str | None,
    font_size: int = 42,
    bg_image: Image.Image | None = None,
) -> np.ndarray:
    """渲染单帧：渐变背景 + 关键词大字 + 字幕。"""
    img = bg_image.copy() if bg_image is not None else Image.fromarray(_create_background(width, height))
    draw = ImageDraw.Draw(img)

    try:
        font_large = ImageFont.truetype(font_path, font_size * 2) if font_path else ImageFont.load_default()
        font_sub = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except (OSError, IOError):
        font_large = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    # 关键词大字居中
    if keyword:
        bbox = draw.textbbox((0, 0), keyword, font=font_large)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((width - tw) // 2, height // 3 - th // 2), keyword, fill="white", font=font_large)

    # 字幕底部
    if text:
        bbox = draw.textbbox((0, 0), text, font=font_sub)
        tw = bbox[2] - bbox[0]
        draw.text(((width - tw) // 2, height * 3 // 4), text, fill="white", font=font_sub)

    return np.array(img)


def render_video(script_id: str, config: dict) -> str:
    """模板视频渲染。

    - 深色渐变背景 + 关键词大字 + 标题卡
    - 字幕烧录 + BGM 混音
    - 输出 dist/<script_id>/final.mp4
    """
    from pipeline import validate_script_id
    from pipeline.subtitle import generate_srt
    from moviepy import (
        VideoClip,
        AudioFileClip,
        CompositeVideoClip,
        CompositeAudioClip,
    )

    validate_script_id(script_id)
    output_dir = Path(config["paths"]["output_dir"]) / script_id
    cheat_root = Path(config["paths"]["cheat_root"])

    # 生成 SRT
    srt_path = generate_srt(script_id, config)

    # 读取 timestamps
    ts_path = output_dir / "timestamps.json"
    timestamps = json.loads(ts_path.read_text(encoding="utf-8"))

    # 读取 manifest 获取标题
    manifest_path = cheat_root / "scripts" / script_id / "manifest.json"
    title = ""
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        title = manifest.get("title", "")

    # 读取 draft.md 提取关键词
    draft_path = cheat_root / "scripts" / script_id / "draft.md"
    keywords = []
    if draft_path.exists():
        for line in draft_path.read_text(encoding="utf-8").split("\n"):
            if line.startswith("**画面**:"):
                keywords.append(line.split(":", 1)[1].strip())

    # 视频参数
    res = config["video"]["resolution"].split("x")
    width, height = int(res[0]), int(res[1])
    font_path = None  # TODO: 从 config 查找字体文件

    # 预生成渐变背景（只生成一次，每帧复用）
    bg_image = Image.fromarray(_create_background(width, height))

    # 计算总时长
    total_duration = timestamps[-1]["end"] if timestamps else 10

    def make_frame(t):
        # 找到当前时间段
        keyword = ""
        text = ""
        for i, ts in enumerate(timestamps):
            if ts["start"] <= t < ts["end"]:
                keyword = keywords[i] if i < len(keywords) else ""
                text = ts["text"]
                break
        return _render_frame(width, height, text, keyword, font_path, config["video"]["subtitle_fontsize"], bg_image)

    video = VideoClip(make_frame, duration=total_duration)
    audio_clip = None

    try:
        # 混入音频
        audio_path = output_dir / "audio.wav"
        if audio_path.exists():
            audio_clip = AudioFileClip(str(audio_path))
            video = video.with_audio(audio_clip)

        # 输出
        output_path = output_dir / "final.mp4"
        video.write_videofile(
            str(output_path),
            fps=24,
            codec="libx264",
            audio_codec="aac",
            logger=None,
        )
    finally:
        if audio_clip is not None:
            audio_clip.close()
        video.close()

    # 更新 manifest
    from pipeline import update_manifest
    script_dir = cheat_root / "scripts" / script_id
    update_manifest(script_dir, "render")

    return str(output_path)

"""渲染：模板视频 + 字幕 + BGM"""

import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pipeline.image_utils import resolve_font_path, wrap_text

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _cover_frame(frame: np.ndarray, width: int, height: int) -> Image.Image:
    """将视频帧 cover 缩放到目标尺寸（按短边缩放 + 居中裁剪）。"""
    frame = np.asarray(frame)
    if frame.dtype != np.uint8:
        frame = np.clip(frame, 0, 255).astype(np.uint8)
    img = Image.fromarray(frame).convert("RGB")
    if img.width <= 0 or img.height <= 0:
        raise ValueError(f"Invalid footage frame size: {img.width}x{img.height}")
    scale = max(width / img.width, height / img.height)
    new_size = (int(img.width * scale), int(img.height * scale))
    img = img.resize(new_size, Image.LANCZOS)
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


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

    # 关键词大字居中（限 2 行）
    if keyword:
        margin = 60
        kw_lines = wrap_text(keyword, font_large, width - margin * 2, draw)[:2]
        line_h = font_size * 2 + 12
        total_h = line_h * len(kw_lines)
        y_start = height // 3 - total_h // 2
        for i, line in enumerate(kw_lines):
            bbox = draw.textbbox((0, 0), line, font=font_large)
            tw = bbox[2] - bbox[0]
            x = (width - tw) // 2
            y = y_start + i * line_h
            draw.text((x, y), line, fill="white", font=font_large, stroke_width=2, stroke_fill="black")

    # 字幕底部（自动换行 + 描边）
    if text:
        margin = 60
        sub_lines = wrap_text(text, font_sub, width - margin * 2, draw)
        line_h = font_size + 8
        total_h = line_h * len(sub_lines)
        y_start = height * 3 // 4 - total_h // 2
        for i, line in enumerate(sub_lines):
            bbox = draw.textbbox((0, 0), line, font=font_sub)
            tw = bbox[2] - bbox[0]
            x = (width - tw) // 2
            y = y_start + i * line_h
            draw.text((x, y), line, fill="white", font=font_sub, stroke_width=2, stroke_fill="black")

    return np.array(img)


def render_video(script_id: str, config: dict, *, bgm_id: str | None = None, mute: bool = False) -> str:
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
    font_path = resolve_font_path(config)

    # 预生成渐变背景（只生成一次，每帧复用）
    bg_image = Image.fromarray(_create_background(width, height))

    # 素材池加载
    render_mode = config["video"].get("render_mode", "gradient")
    footage_clips = []
    footage_hits = 0
    footage_segments = []  # 每段素材使用记录

    if render_mode == "footage":
        from pipeline.footage import index_footage, load_blocklist, match_footage_with_reason
        from moviepy import VideoFileClip

        footage_dir = Path(config["paths"].get("footage_dir", "assets/footage"))
        if not footage_dir.is_absolute():
            footage_dir = _PROJECT_ROOT / footage_dir
        idx = index_footage(footage_dir, exclude=load_blocklist(footage_dir))
        logger.info(f"素材索引: {len(idx)} 个文件")

        for i, ts in enumerate(timestamps):
            original_kw = keywords[i] if i < len(keywords) else ""
            kw = original_kw if original_kw else "abstract"
            result = match_footage_with_reason(kw, idx)
            if result["path"]:
                path = result["path"]
                source = "abstract_fallback" if result["reason"] == "abstract_fallback" else "matched"
                try:
                    clip = VideoFileClip(path)
                    if not clip.duration or clip.duration <= 0:
                        logger.warning(f"素材 duration 无效: {path}")
                        clip.close()
                        footage_clips.append(None)
                        footage_segments.append({
                            "index": i, "keyword": original_kw or "", "footage": None,
                            "source": "gradient_fallback",
                        })
                        continue
                    footage_clips.append(clip)
                    footage_hits += 1
                    footage_segments.append({
                        "index": i, "keyword": original_kw or "", "footage": path,
                        "source": source,
                    })
                except Exception as e:
                    logger.warning(f"素材加载失败 {path}: {e}")
                    footage_clips.append(None)
                    footage_segments.append({
                        "index": i, "keyword": original_kw or "", "footage": None,
                        "source": "gradient_fallback",
                    })
            else:
                footage_clips.append(None)
                footage_segments.append({
                    "index": i, "keyword": original_kw or "", "footage": None,
                    "source": "gradient_fallback",
                })

        logger.info(f"素材命中: {footage_hits}/{len(timestamps)} 段")

    # 计算总时长
    total_duration = timestamps[-1]["end"] if timestamps else 10

    def make_frame(t):
        keyword = ""
        text = ""
        bg = bg_image
        for i, ts in enumerate(timestamps):
            if ts["start"] <= t < ts["end"]:
                keyword = keywords[i] if i < len(keywords) and keywords[i] else "abstract"
                text = ts["text"]
                if i < len(footage_clips) and footage_clips[i] is not None:
                    try:
                        clip = footage_clips[i]
                        if clip.duration and clip.duration > 0:
                            ft = (t - ts["start"]) % clip.duration
                            frame = clip.get_frame(ft)
                            bg = _cover_frame(frame, width, height)
                    except Exception:
                        pass  # fallback to gradient
                break
        return _render_frame(width, height, text, keyword, font_path, config["video"]["subtitle_fontsize"], bg)

    video = VideoClip(make_frame, duration=total_duration)
    audio_clip = None
    bgm_clip = None
    bgm_path = None
    bgm_info = {}

    try:
        # 混入音频
        audio_path = output_dir / "audio.wav"
        if audio_path.exists():
            audio_clip = AudioFileClip(str(audio_path))

            # BGM 混音（手动/静音/自动）
            from pipeline.bgm import select_bgm
            bgm_volume = config["video"].get("bgm_volume", 0.15)
            if mute:
                bgm_path, bgm_info = None, {"reason": "muted"}
            elif bgm_id:
                from pipeline.bgm import resolve_bgm_by_id
                bgm_path, bgm_info = resolve_bgm_by_id(bgm_id, config)
                if not bgm_path:
                    logger.warning(f"BGM id '{bgm_id}' 不可用，回退自动匹配")
                    bgm_path, bgm_info = select_bgm(script_id, config)
            else:
                bgm_path, bgm_info = select_bgm(script_id, config)
            if bgm_path:
                _bgm_id = bgm_info.get("bgm_id") or bgm_info.get("id", "?")
                logger.info(f"BGM: {_bgm_id} (mood={bgm_info.get('mood', '?')}, reason={bgm_info.get('reason', '?')})")
                bgm_clip = AudioFileClip(str(bgm_path))
                if bgm_clip.duration < total_duration:
                    from moviepy import concatenate_audioclips
                    repeats = int(total_duration / bgm_clip.duration) + 1
                    bgm_clip = concatenate_audioclips([bgm_clip.subclipped(0, bgm_clip.duration) for _ in range(repeats)])
                bgm_clip = bgm_clip.subclipped(0, total_duration).with_volume_scaled(bgm_volume)

            if bgm_clip is not None:
                mixed = CompositeAudioClip([audio_clip, bgm_clip])
                video = video.with_audio(mixed)
            else:
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
        if bgm_clip is not None:
            bgm_clip.close()
        for fc in footage_clips:
            if fc is not None:
                fc.close()
        if video is not None:
            video.close()

    # 写入渲染报告
    if not footage_segments:
        footage_segments = [
            {"index": i, "keyword": keywords[i] if i < len(keywords) else "", "footage": None, "source": "gradient"}
            for i in range(len(timestamps))
        ]
    matched = sum(1 for s in footage_segments if s["source"] == "matched")
    abstract_fb = sum(1 for s in footage_segments if s["source"] == "abstract_fallback")
    gradient_fb = sum(1 for s in footage_segments if s["source"] in ("gradient_fallback", "gradient"))
    report = {
        "render_mode": render_mode,
        "segments_total": len(timestamps),
        "footage_hits": footage_hits,
        "matched": matched,
        "abstract_fallback": abstract_fb,
        "gradient_fallback": gradient_fb,
        "segments": footage_segments,
    }
    # BGM mode 判定：手动指定成功才记 "manual"，回退后记 "auto_fallback"
    if mute:
        bgm_mode = "mute"
    elif bgm_id and bgm_path and (bgm_info.get("id") == bgm_id or bgm_info.get("bgm_id") == bgm_id):
        bgm_mode = "manual"
    elif bgm_id:
        bgm_mode = "auto_fallback"
    else:
        bgm_mode = "auto"
    report["bgm"] = {
        "mode": bgm_mode,
        "id": bgm_info.get("bgm_id") or bgm_info.get("id") or bgm_id,
        "reason": bgm_info.get("reason"),
    }
    report_path = output_dir / "render_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if render_mode == "footage":
        logger.info(f"渲染报告: {matched} 关键词命中, {abstract_fb} abstract fallback, {gradient_fb} 渐变 fallback")

    # 更新 manifest
    from pipeline import update_manifest
    script_dir = cheat_root / "scripts" / script_id
    update_manifest(script_dir, "render")

    return str(output_path)

"""导出：发布包（支持平台适配）"""

import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pipeline.image_utils import resolve_font_path, wrap_text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 缩略图生成
# ---------------------------------------------------------------------------

def _extract_keyword(title: str) -> str:
    """从标题提取关键词（用于缩略图大字展示）。

    按中文标点分割取第一段，去除引号/书名号/括号，截断到 8 中文字符 / 16 英文字符。
    """
    # 按标点分割取第一段
    parts = re.split(r'[？！。，、；：""''《》（）?!.,;]', title)
    keyword = parts[0].strip() if parts else title.strip()

    # 去除残留标点
    keyword = re.sub(r'["\'《》（）「」『』【】\(\)\[\]]', '', keyword).strip()

    # 截断：中文字符 ≤8，英文字符 ≤16
    cjk_count = 0
    cut_idx = 0
    for i, ch in enumerate(keyword):
        is_cjk = '一' <= ch <= '鿿'
        if is_cjk:
            cjk_count += 1
        if cjk_count > 8 or (not is_cjk and i > 16):
            cut_idx = i
            break
    else:
        cut_idx = len(keyword)
    keyword = keyword[:cut_idx].strip()

    # 过短时 fallback
    if len(keyword) < 2:
        keyword = title[:8].strip()

    return keyword


def _generate_thumbnail(title: str, output_path: Path, config: dict, *, width: int = 1280, height: int = 720) -> None:
    """生成缩略图 v2：渐变背景 + 关键词大字 + 标题小字 + 装饰元素。"""
    # 读取 thumbnail 配置
    thumb_cfg = config.get("thumbnail", {})
    keyword_ratio = thumb_cfg.get("keyword_ratio", 1.8)
    accent_rgb = tuple(int(x) for x in thumb_cfg.get("accent_color", "100,180,255").split(","))

    # 字体解析
    font_path = resolve_font_path(config)
    base_font_size = max(24, height // 16)
    font_kw_size = int(base_font_size * keyword_ratio)

    try:
        font_kw = ImageFont.truetype(font_path, font_kw_size) if font_path else ImageFont.load_default()
        font_title = ImageFont.truetype(font_path, base_font_size) if font_path else ImageFont.load_default()
    except (OSError, IOError):
        font_kw = ImageFont.load_default()
        font_title = ImageFont.load_default()

    # 渐变背景（3 色停止点）
    img = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / height
        if ratio < 0.5:
            t = ratio * 2
            r = int(8 + (20 - 8) * t)
            g = int(5 + (15 - 5) * t)
            b = int(25 + (60 - 25) * t)
        else:
            t = (ratio - 0.5) * 2
            r = int(20 + (35 - 20) * t)
            g = int(15 + (25 - 15) * t)
            b = int(60 + (80 - 60) * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

    # 底部渐变遮罩（底部 30%，从透明到 40% 黑）
    mask_top = int(height * 0.7)
    for y in range(mask_top, height):
        alpha = int(102 * (y - mask_top) / (height - mask_top))  # 102 = 255 * 0.4
        draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))

    # 提取关键词
    keyword = _extract_keyword(title)

    # 关键词大字（居中于上 1/3 区域）
    margin = int(width * 0.08)
    max_text_w = width - margin * 2
    kw_lines = wrap_text(keyword, font_kw, max_text_w, draw)[:3]
    line_h_kw = font_kw_size + 12
    total_h_kw = line_h_kw * len(kw_lines)
    y_kw_start = height // 3 - total_h_kw // 2

    for i, line in enumerate(kw_lines):
        bbox = draw.textbbox((0, 0), line, font=font_kw)
        tw = bbox[2] - bbox[0]
        x = (width - tw) // 2
        y = y_kw_start + i * line_h_kw
        draw.text((x, y), line, fill="white", font=font_kw, stroke_width=2, stroke_fill="black")

    # 装饰分割线（关键词下方）
    line_y = y_kw_start + total_h_kw + int(height * 0.03)
    line_w = int(width * 0.4)
    line_x = (width - line_w) // 2
    draw.line([(line_x, line_y), (line_x + line_w, line_y)], fill=(*accent_rgb, 180), width=2)

    # 标题小字（居中于下半区域）
    title_lines = wrap_text(title, font_title, max_text_w, draw)[:4]
    line_h_title = base_font_size + 8
    total_h_title = line_h_title * len(title_lines)
    y_title_start = line_y + int(height * 0.06)

    # 如果标题区域超出画布底部，向上压缩
    if y_title_start + total_h_title > height - margin:
        y_title_start = height - margin - total_h_title

    for i, line in enumerate(title_lines):
        bbox = draw.textbbox((0, 0), line, font=font_title)
        tw = bbox[2] - bbox[0]
        x = (width - tw) // 2
        y = y_title_start + i * line_h_title
        draw.text((x, y), line, fill="white", font=font_title, stroke_width=2, stroke_fill="black")

    # 输出（转 RGB 保存 PNG）
    img_rgb = img.convert("RGB")
    img_rgb.save(str(output_path), "PNG")


def _export_root(script_id: str, config: dict) -> str:
    """根目录导出（原有逻辑）：title.txt / description.txt / tags.txt / thumbnail.png + manifest + publish queue。"""
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

    # 发布队列：auto-set ready
    from pipeline.publish import init_or_update_status
    init_or_update_status(script_id, config, status="ready")

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


# ---------------------------------------------------------------------------
# 平台适配导出
# ---------------------------------------------------------------------------

def _adapt_metadata(raw_title: str, raw_desc: str, raw_tags: list[str], platform: str, profile: dict, config: dict) -> dict:
    """LLM 一次调用适配 title + description + tags。

    返回 {"title": str, "description": str, "tags": list[str]}。
    LLM 失败时 fallback 到规则截断。
    """
    from pipeline.llm_utils import call_llm, parse_llm_json

    platform_names = {"douyin": "抖音", "kuaishou": "快手", "bilibili": "B站"}
    platform_cn = platform_names.get(platform, platform)

    prompt = f"""你是一个短视频运营专家。请将以下内容适配到{platform_cn}平台。

原始标题：{raw_title}
原始描述：{raw_desc[:500]}
原始标签：{', '.join(raw_tags)}
平台要求：
- 标题上限{profile['title_max']}字符
- 描述上限{profile['desc_max']}字符
- 标签格式：{profile['tag_format']}（最多{profile['tag_max']}个）

风格指导：
- 抖音：口语化、有悬念感、引导互动
- 快手：接地气、简洁直白
- B站：信息密度高、可适度专业

请输出 JSON：
{{
  "title": "适配后的标题",
  "description": "适配后的描述",
  "tags": ["标签1", "标签2", ...]
}}"""

    try:
        response = call_llm(
            messages=[{"role": "user", "content": prompt}],
            model=config["models"]["script_model"],
            api_key=config["api"]["openai_api_key"],
            base_url=config["api"]["openai_base_url"],
            max_retries=config["llm"]["max_retries"],
            retry_delay_seconds=config["llm"]["retry_delay_seconds"],
            timeout_seconds=config["llm"]["timeout_seconds"],
            response_format={"type": "json_object"},
        )
        result = parse_llm_json(response, ["title", "description", "tags"])

        # 截断兜底（LLM 可能返回超长内容）
        title = result["title"][:profile["title_max"]]
        desc = result["description"][:profile["desc_max"]]
        tags = result["tags"][:profile["tag_max"]]

        logger.info(f"[{platform}] LLM 适配成功: title={len(title)}字, desc={len(desc)}字, tags={len(tags)}个")
        return {"title": title, "description": desc, "tags": tags}

    except Exception as e:
        logger.warning(f"[{platform}] LLM 适配失败，fallback 到规则截断: {e}")
        return {
            "title": raw_title[:profile["title_max"]],
            "description": raw_desc[:profile["desc_max"]],
            "tags": raw_tags[:profile["tag_max"]],
        }


def _adapt_tags(raw_tags: list[str], platform: str, profile: dict) -> str:
    """规则适配标签格式：hashtag 加 # 前缀空格分隔，comma 逗号分隔。"""
    tags = raw_tags[:profile["tag_max"]]
    if profile["tag_format"] == "hashtag":
        return " ".join(f"#{t}" for t in tags)
    return ",".join(tags)


def _export_platform(script_id: str, platform: str, config: dict) -> dict:
    """单平台适配导出：生成 platforms/<platform>/ 下的所有文件，返回 metadata dict。

    不调用 manifest 更新 / publish queue（由调用方统一处理）。
    """
    from pipeline.platform_profiles import get_platform_profile

    profile = get_platform_profile(platform, config)
    output_dir = Path(config["paths"]["output_dir"]) / script_id
    platform_dir = output_dir / "platforms" / platform
    platform_dir.mkdir(parents=True, exist_ok=True)
    cheat_root = Path(config["paths"]["cheat_root"])

    # 读取原始数据
    raw_title = ""
    raw_desc = ""
    manifest_path = cheat_root / "scripts" / script_id / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_title = manifest.get("title", "")
    final_path = cheat_root / "scripts" / script_id / "final.md"
    if final_path.exists():
        raw_desc = final_path.read_text(encoding="utf-8")

    # 提取原始 tags（复用根目录 tags.txt 或重新提取）
    raw_tags = ["AI", "人工智能"]
    if raw_title:
        for seg in re.split(r'[，。！？、\s]+', raw_title):
            seg = seg.strip()
            if 2 <= len(seg) <= 8:
                raw_tags.append(seg)
    pred_path = cheat_root / "predictions" / f"{script_id}.json"
    if pred_path.exists():
        pred = json.loads(pred_path.read_text(encoding="utf-8"))
        for key in ("drivers", "risks"):
            for item in pred.get(key, []):
                if isinstance(item, str) and 2 <= len(item) <= 8:
                    raw_tags.append(item)
    raw_tags = list(dict.fromkeys(raw_tags))  # 去重保序

    # LLM 适配
    adapted = _adapt_metadata(raw_title, raw_desc, raw_tags, platform, profile, config)

    # 写 title.txt
    (platform_dir / "title.txt").write_text(adapted["title"], encoding="utf-8")

    # 写 description.txt
    (platform_dir / "description.txt").write_text(adapted["description"], encoding="utf-8")

    # 写 tags.txt
    tag_text = _adapt_tags(adapted["tags"], platform, profile)
    (platform_dir / "tags.txt").write_text(tag_text, encoding="utf-8")

    # 生成平台专属封面
    thumb_path = platform_dir / "thumbnail.png"
    _generate_thumbnail(adapted["title"], thumb_path, config,
                        width=profile["thumb_width"], height=profile["thumb_height"])

    # 写 metadata.json
    exported_at = datetime.now(timezone.utc).isoformat()
    metadata = {
        "platform": platform,
        "title": adapted["title"],
        "description": adapted["description"],
        "tags": adapted["tags"],
        "tag_text": tag_text,
        "thumbnail": "thumbnail.png",
        "exported_at": exported_at,
    }
    (platform_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(f"[{platform}] 平台导出完成: {platform_dir}")
    return metadata


def update_manifest_platform_exports(script_id: str, platform: str, metadata: dict, config: dict) -> None:
    """更新 manifest.json 的 platform_exports 字段。"""
    cheat_root = Path(config["paths"]["cheat_root"])
    manifest_path = cheat_root / "scripts" / script_id / "manifest.json"
    if not manifest_path.exists():
        return

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    exports = manifest.get("platform_exports", {})
    exports[platform] = {
        "title": metadata["title"],
        "description": metadata["description"][:100],
        "tags": metadata["tags"],
        "thumbnail": metadata["thumbnail"],
        "exported_at": metadata["exported_at"],
    }
    manifest["platform_exports"] = exports

    # 原子写入
    import tempfile, os
    fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=str(manifest_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, manifest_path)
    except Exception:
        os.unlink(tmp_path)
        raise


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def export_package(script_id: str, config: dict, *, platform: str | None = None) -> str:
    """导出发布资料包。

    platform=None：仅根目录导出（向后兼容）。
    platform=<name>：根目录导出 + 平台适配导出。
    """
    output_dir = _export_root(script_id, config)

    if platform is not None:
        metadata = _export_platform(script_id, platform, config)
        update_manifest_platform_exports(script_id, platform, metadata, config)

    return output_dir

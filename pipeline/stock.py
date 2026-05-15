"""外部素材补全器：Pexels / Pixabay 搜索下载。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

import requests

from pipeline.footage import index_footage, match_footage_with_reason
from pipeline.keyword_aliases import ALIASES as KEYWORD_ALIASES, SEARCH_QUERIES as _STOCK_KEYWORD_ALIASES

logger = logging.getLogger(__name__)

# ── 工具函数 ────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """转安全文件名/缓存 key。"""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text).strip('-')
    return text or "unknown"


def _get_api_key(env_var_name: str) -> str | None:
    """从环境变量读 API key，缺失返回 None。"""
    return os.environ.get(env_var_name) or None


def _translate_keyword(keyword: str) -> str:
    """中文关键词 → 英文搜索词。"""
    # 1. 全匹配
    if keyword in _STOCK_KEYWORD_ALIASES:
        return _STOCK_KEYWORD_ALIASES[keyword]
    # 2. 也查 footage 的别名表
    if keyword in KEYWORD_ALIASES:
        return " ".join(sorted(KEYWORD_ALIASES[keyword]))
    # 3. 分词逐词查
    parts: list[str] = []
    for seg in re.split(r'[，。！？、\s]+', keyword):
        seg = seg.strip()
        if not seg:
            continue
        if seg in _STOCK_KEYWORD_ALIASES:
            parts.append(_STOCK_KEYWORD_ALIASES[seg])
        elif seg in KEYWORD_ALIASES:
            parts.append(" ".join(sorted(KEYWORD_ALIASES[seg])))
        elif seg.isascii():
            parts.append(seg)
    result = " ".join(parts).strip()
    return result or "technology"


# ── 缓存 ────────────────────────────────────────────────────────────

def _cache_path(cache_dir: Path, provider: str, config: dict, query: str) -> Path:
    stock = config.get("stock", {})
    safe_q = _slugify(query)
    orientation = stock.get("orientation", "portrait")
    min_w = stock.get("min_width", 720)
    min_h = stock.get("min_height", 1280)
    name = f"{provider}_{orientation}_{min_w}x{min_h}_{safe_q}.json"
    return cache_dir / name


def _load_cache(cache_file: Path, cache_hours: int) -> list[dict] | None:
    """缓存有效返回 list，过期/不存在返回 None。"""
    if not cache_file.exists():
        return None
    age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
    if age_hours > cache_hours:
        return None
    try:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(cache_file: Path, data: list[dict]) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 来源记录 ────────────────────────────────────────────────────────

def _sources_path(footage_dir: Path) -> Path:
    return footage_dir / "sources.json"


def _load_sources(footage_dir: Path) -> list[dict]:
    sp = _sources_path(footage_dir)
    if not sp.exists():
        return []
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_sources(footage_dir: Path, sources: list[dict]) -> None:
    sp = _sources_path(footage_dir)
    sp.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_duplicate(sources: list[dict], dedupe_key: str) -> bool:
    return any(s.get("dedupe_key") == dedupe_key for s in sources)


# ── Pexels ──────────────────────────────────────────────────────────

def _normalize_pexels_video(video: dict, query: str) -> dict:
    video_files = video.get("video_files", [])
    return {
        "provider": "pexels",
        "source_id": str(video.get("id", "")),
        "query": query,
        "video_files_raw": video_files,  # 选择逻辑在下载时做
        "source_url": video.get("url", ""),
        "author": video.get("user", {}).get("name", ""),
        "width": video.get("width", 0),
        "height": video.get("height", 0),
        "duration": video.get("duration", 0),
    }


def _search_pexels(query: str, config: dict) -> list[dict]:
    api_key = _get_api_key(config.get("api", {}).get("pexels_api_key_env", "PEXELS_API_KEY"))
    if not api_key:
        raise ValueError("PEXELS_API_KEY 环境变量未设置")
    stock = config.get("stock", {})
    params = {
        "query": query,
        "orientation": stock.get("orientation", "portrait"),
        "per_page": 10,
    }
    resp = requests.get(
        "https://api.pexels.com/v1/videos/search",
        headers={"Authorization": api_key},
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    videos = resp.json().get("videos", [])
    return [_normalize_pexels_video(v, query) for v in videos]


def _pick_pexels_file(video_files_raw: list[dict], config: dict) -> str | None:
    """从 Pexels video_files 选最合适的下载链接。"""
    stock = config.get("stock", {})
    min_w = stock.get("min_width", 720)
    min_h = stock.get("min_height", 1280)

    candidates = []
    for vf in video_files_raw:
        w = vf.get("width", 0)
        h = vf.get("height", 0)
        link = vf.get("link")
        if not link:
            continue
        score = 0
        if h >= min_h and w >= min_w:
            score += 100
        if h >= w:  # portrait
            score += 50
        if vf.get("quality") == "hd":
            score += 30
        score += w * h / 1000000  # area bonus
        candidates.append((score, link))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ── Pixabay ─────────────────────────────────────────────────────────

def _normalize_pixabay_video(hit: dict, query: str) -> dict:
    videos = hit.get("videos", {})
    return {
        "provider": "pixabay",
        "source_id": str(hit.get("id", "")),
        "query": query,
        "videos_raw": videos,  # 档位选择在下载时做
        "source_url": hit.get("pageURL", ""),
        "author": hit.get("user", ""),
        "width": hit.get("imageWidth", 0),
        "height": hit.get("imageHeight", 0),
        "duration": hit.get("duration", 0),
    }


def _search_pixabay(query: str, config: dict) -> list[dict]:
    api_key = _get_api_key(config.get("api", {}).get("pixabay_api_key_env", "PIXABAY_API_KEY"))
    if not api_key:
        raise ValueError("PIXABAY_API_KEY 环境变量未设置")
    params = {
        "key": api_key,
        "q": query,
        "per_page": 10,
        "video_type": "film",
    }
    resp = requests.get(
        "https://pixabay.com/api/videos/",
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", [])
    return [_normalize_pixabay_video(h, query) for h in hits]


def _pick_pixabay_file(videos_raw: dict, config: dict) -> str | None:
    """从 Pixabay videos 档位选下载链接：large → medium → small → tiny。"""
    stock = config.get("stock", {})
    min_w = stock.get("min_width", 720)
    min_h = stock.get("min_height", 1280)

    for tier in ("large", "medium", "small", "tiny"):
        info = videos_raw.get(tier)
        if not info:
            continue
        w = info.get("width", 0)
        h = info.get("height", 0)
        url = info.get("url")
        if url and w >= min_w and h >= min_h:
            return url

    # fallback: 任意档位有链接就行
    for tier in ("large", "medium", "small", "tiny"):
        info = videos_raw.get(tier)
        if info and info.get("url"):
            return info["url"]
    return None


# ── 下载 ────────────────────────────────────────────────────────────

def _pick_download_url(item: dict, config: dict) -> str | None:
    """根据 provider 从 item 中选下载链接。"""
    if item["provider"] == "pexels":
        return _pick_pexels_file(item.get("video_files_raw", []), config)
    elif item["provider"] == "pixabay":
        return _pick_pixabay_file(item.get("videos_raw", {}), config)
    return None


def _download_video(item: dict, dest_dir: Path, config: dict, *, filename: str | None = None) -> str | None:
    """下载视频到 dest_dir，返回路径字符串或 None。校验用 .part → os.replace。"""
    download_url = _pick_download_url(item, config)
    if not download_url:
        return None

    provider = item["provider"]
    query_slug = _slugify(item["query"]) or "stock"
    source_id = item["source_id"]
    if filename is None:
        filename = f"{provider}-{query_slug}-{source_id}.mp4"
    final_path = dest_dir / filename
    part_path = dest_dir / (filename + ".part")

    if final_path.exists():
        return str(final_path)

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        resp = requests.get(download_url, stream=True, timeout=60)
        resp.raise_for_status()
        with open(part_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception:
        part_path.unlink(missing_ok=True)
        return None

    # 校验尺寸/时长
    try:
        try:
            from moviepy import VideoFileClip
        except ImportError:
            from moviepy.editor import VideoFileClip

        with VideoFileClip(str(part_path)) as clip:
            stock = config.get("stock", {})
            min_w = stock.get("min_width", 720)
            min_h = stock.get("min_height", 1280)
            if clip.w < min_w or clip.h < min_h:
                logger.info("尺寸不达标 %dx%d < %dx%d, 跳过", clip.w, clip.h, min_w, min_h)
                part_path.unlink(missing_ok=True)
                return None
    except Exception as e:
        logger.warning("视频校验失败: %s, 保留文件", e)
        # 校验模块不可用时保留文件

    os.replace(str(part_path), str(final_path))
    return str(final_path)


# ── 按分类下载 ──────────────────────────────────────────────────────

def download_footage_by_category(
    category: str,
    queries: list[str],
    config: dict,
    *,
    limit_per_query: int | None = None,
) -> dict:
    """按分类搜索下载素材到 assets/footage/<category>/。

    category 只允许 [a-z0-9_-]+，resolve 后必须在 footage_dir 下。
    """
    import hashlib

    if not re.match(r'^[a-z0-9_-]+$', category):
        raise ValueError(f"非法 category 名: {category!r}（只允许 [a-z0-9_-]+）")

    footage_dir = Path(config["paths"].get("footage_dir", "assets/footage"))
    if not footage_dir.is_absolute():
        footage_dir = Path(__file__).resolve().parents[1] / footage_dir
    dest_dir = (footage_dir / category).resolve()
    try:
        dest_dir.relative_to(footage_dir.resolve())
    except ValueError:
        raise ValueError(f"category 路径越界: {dest_dir}")

    stock = config.get("stock", {})
    providers = stock.get("provider_order", ["pexels", "pixabay"])
    per_query = limit_per_query if limit_per_query is not None else stock.get("max_downloads_per_keyword", 2)
    sources = _load_sources(footage_dir)
    result: dict = {"category": category, "total_downloaded": 0, "failed": [], "details": []}

    for query in queries:
        downloaded = 0
        slug = _slugify(query) or "stock"

        for prov in providers:
            if downloaded >= per_query:
                break

            env_key = config.get("api", {}).get(f"{prov}_api_key_env", f"{prov.upper()}_API_KEY")
            if not _get_api_key(env_key):
                result["failed"].append({"query": query, "provider": prov, "reason": f"{env_key} 未设置"})
                continue

            try:
                if prov == "pexels":
                    items = _search_pexels(query, config)
                elif prov == "pixabay":
                    items = _search_pixabay(query, config)
                else:
                    result["failed"].append({"query": query, "provider": prov, "reason": "未知 provider"})
                    continue
            except Exception as e:
                result["failed"].append({"query": query, "provider": prov, "reason": str(e)})
                continue

            for item in items:
                if downloaded >= per_query:
                    break

                dedupe_key = f"{item['provider']}:{item['source_id']}"
                if _is_duplicate(sources, dedupe_key):
                    continue

                short_hash = hashlib.md5(dedupe_key.encode()).hexdigest()[:8]
                filename = f"{category}-{prov}-{slug}-{short_hash}.mp4"
                path = _download_video(item, dest_dir, config, filename=filename)
                if not path:
                    continue

                sources.append({
                    "path": path, "provider": item["provider"],
                    "source_id": item["source_id"], "query": query,
                    "source_url": item.get("source_url", ""),
                    "author": item.get("author", ""),
                    "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "dedupe_key": dedupe_key, "category": category,
                })
                _save_sources(footage_dir, sources)

                result["details"].append({
                    "query": query, "provider": prov,
                    "url": item.get("source_url", ""),
                    "local_path": path, "status": "ok",
                })
                downloaded += 1
                result["total_downloaded"] += 1

    return result


# ── 主流程 ──────────────────────────────────────────────────────────

def extract_missing_keywords(script_id: str, config: dict) -> list[dict]:
    """解析脚本画面行，返回需要补素材的关键词列表。"""
    paths_conf = config.get("paths", {})
    cheat_root = Path(paths_conf.get("cheat_root", "cheat"))
    footage_dir = Path(paths_conf.get("footage_dir", "assets/footage"))
    script_dir = cheat_root / "scripts" / script_id

    # 优先 final.md，回退 draft.md
    md_file = None
    for name in ("final.md", "draft.md"):
        candidate = script_dir / name
        if candidate.exists():
            md_file = candidate
            break
    if md_file is None:
        return []

    text = md_file.read_text(encoding="utf-8")
    pattern = re.compile(r"(?:\*\*画面\*\*|画面|visual_keyword)\s*[:：]\s*(.+)")
    keywords: list[str] = []
    for line in text.splitlines():
        m = pattern.search(line)
        if m:
            kw = m.group(1).strip()
            if kw and kw not in keywords:
                keywords.append(kw)

    if not keywords:
        return []

    index = index_footage(footage_dir)
    missing = []
    for kw in keywords:
        result = match_footage_with_reason(kw, index)
        if result["reason"] in ("abstract_fallback", "none"):
            missing.append({
                "keyword": kw,
                "query": _translate_keyword(kw),
                "reason": result["reason"],
            })
    return missing


def fill_footage_for_script(
    script_id: str,
    config: dict,
    *,
    provider: str | None = None,
    limit: int | None = None,
) -> dict:
    """补充外部素材，返回结果摘要。"""
    result: dict = {
        "script_id": script_id,
        "missing_keywords": [],
        "downloaded": [],
        "skipped_cached": [],
        "failed": [],
    }

    missing = extract_missing_keywords(script_id, config)
    result["missing_keywords"] = missing

    if not missing:
        return result

    stock = config.get("stock", {})
    footage_dir = Path(config.get("paths", {}).get("footage_dir", "assets/footage"))
    cache_dir = footage_dir / ".cache"
    cache_hours = stock.get("cache_hours", 24)

    # 确定 provider 列表
    if provider:
        providers = [provider]
    else:
        providers = stock.get("provider_order", ["pexels", "pixabay"])

    # per-keyword 下载限制
    per_kw = limit if limit is not None else stock.get("max_downloads_per_keyword", 2)

    sources = _load_sources(footage_dir)

    for mk in missing:
        kw = mk["keyword"]
        query = mk["query"]
        downloaded_for_kw = 0

        for prov in providers:
            if downloaded_for_kw >= per_kw:
                break

            # 检查 API key
            env_key_name = config.get("api", {}).get(f"{prov}_api_key_env", f"{prov.upper()}_API_KEY")
            if not _get_api_key(env_key_name):
                result["failed"].append({
                    "keyword": kw, "provider": prov,
                    "error": f"{env_key_name} 环境变量未设置",
                })
                continue

            # 查缓存
            cache_file = _cache_path(cache_dir, prov, config, query)
            cached = _load_cache(cache_file, cache_hours)
            if cached is not None:
                for item in cached:
                    if downloaded_for_kw >= per_kw:
                        break
                    dedupe_key = f"{item['provider']}:{item['source_id']}"
                    if _is_duplicate(sources, dedupe_key):
                        result["skipped_cached"].append({"keyword": kw, "provider": prov})
                        continue
                    ext_dir = footage_dir / "external" / _slugify(query)
                    path = _download_video(item, ext_dir, config)
                    if path:
                        result["downloaded"].append({
                            "keyword": kw, "provider": prov,
                            "path": path, "source_url": item.get("source_url", ""),
                        })
                        sources.append({
                            "path": path, "provider": item["provider"],
                            "source_id": item["source_id"], "query": query,
                            "source_url": item.get("source_url", ""),
                            "author": item.get("author", ""),
                            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "dedupe_key": dedupe_key,
                        })
                        _save_sources(footage_dir, sources)
                        downloaded_for_kw += 1
                    else:
                        result["failed"].append({
                            "keyword": kw, "provider": prov, "error": "下载失败",
                        })
                continue

            # 搜索 API
            try:
                if prov == "pexels":
                    items = _search_pexels(query, config)
                elif prov == "pixabay":
                    items = _search_pixabay(query, config)
                else:
                    continue
            except Exception as e:
                result["failed"].append({
                    "keyword": kw, "provider": prov, "error": str(e),
                })
                continue

            if not items:
                result["failed"].append({
                    "keyword": kw, "provider": prov, "error": "API 无结果",
                })
                continue

            # 保存缓存
            _save_cache(cache_file, items)

            # 下载
            for item in items:
                if downloaded_for_kw >= per_kw:
                    break
                dedupe_key = f"{item['provider']}:{item['source_id']}"
                if _is_duplicate(sources, dedupe_key):
                    result["skipped_cached"].append({"keyword": kw, "provider": prov})
                    continue
                ext_dir = footage_dir / "external" / _slugify(query)
                path = _download_video(item, ext_dir, config)
                if path:
                    result["downloaded"].append({
                        "keyword": kw, "provider": prov,
                        "path": path, "source_url": item.get("source_url", ""),
                    })
                    sources.append({
                        "path": path, "provider": item["provider"],
                        "source_id": item["source_id"], "query": query,
                        "source_url": item.get("source_url", ""),
                        "author": item.get("author", ""),
                        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "dedupe_key": dedupe_key,
                    })
                    _save_sources(footage_dir, sources)
                    downloaded_for_kw += 1
                else:
                    result["failed"].append({
                        "keyword": kw, "provider": prov, "error": "下载失败",
                    })

    return result


# ── 健康检查 ────────────────────────────────────────────────────────

def health_check_external(footage_dir: Path) -> dict:
    """检查 external/ 素材健康状态。
    返回 {total_sources, valid, missing_files, zero_byte, orphan_files, issues}
    """
    sources = _load_sources(footage_dir)
    ext_dir = footage_dir / "external"
    issues: list[dict] = []

    # sources.json 中记录的文件
    source_paths: set[str] = set()
    valid = 0
    missing_files = 0
    zero_byte = 0

    for s in sources:
        p = s.get("path", "")
        if not p:
            continue
        source_paths.add(p)
        fp = Path(p)
        if not fp.exists():
            missing_files += 1
            issues.append({"type": "missing_file", "path": p, "source": s})
        elif fp.stat().st_size == 0:
            zero_byte += 1
            issues.append({"type": "zero_byte", "path": p})
        else:
            valid += 1

    # orphan: external/ 目录下有文件但 sources.json 无记录
    orphan_files = 0
    if ext_dir.exists():
        for f in ext_dir.rglob("*"):
            if f.suffix.lower() in {".mp4", ".mov", ".webm", ".avi"}:
                resolved = str(f.resolve())
                found = False
                for sp in source_paths:
                    if Path(sp).resolve() == Path(resolved):
                        found = True
                        break
                if not found:
                    orphan_files += 1
                    issues.append({"type": "orphan_file", "path": str(f)})

    return {
        "total_sources": len(sources),
        "valid": valid,
        "missing_files": missing_files,
        "zero_byte": zero_byte,
        "orphan_files": orphan_files,
        "issues": issues,
    }

"""BGM 智能匹配：catalog 加载 + 脚本情绪推断 + BGM 选择。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_MOOD_KEYWORDS: dict[str, list[str]] = {
    "tech": [
        "AI", "模型", "芯片", "算力", "训练", "推理", "算法", "数据",
        "机器人", "自动化", "大模型", "GPT", "LLM", "深度学习",
    ],
    "tense": [
        "争议", "危机", "暴跌", "冲突", "泄露", "裁员", "封杀",
        "监管", "罚款", "诉讼", "安全漏洞",
    ],
    "warm": [
        "故事", "人物", "创业", "团队", "创始人", "经历", "成长",
        "梦想", "坚持", "感动",
    ],
    "upbeat": [
        "快讯", "发布", "突破", "首发", "上线", "融资", "上市",
        "合作", "开源", "免费",
    ],
}

_MOOD_DEFAULT_ENERGY: dict[str, str] = {
    "tech": "medium",
    "tense": "medium",
    "warm": "low",
    "upbeat": "high",
}

_ENERGY_ORDER = {"low": 0, "medium": 1, "high": 2}

# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def load_bgm_catalog(config: dict) -> list[dict]:
    """加载 bgm_catalog.json，返回原始 enabled 条目（不检查文件存在）。

    - 仅读取真实 bgm_catalog.json，不 fallback 到 example
    - catalog 不存在或为空时返回空列表
    - JSON 解析失败时返回空列表 + warning
    - 文件存在性由 select_bgm() 负责检查
    """
    bgm_dir = _resolve_bgm_dir(config)
    catalog_path = bgm_dir / "bgm_catalog.json"
    if not catalog_path.exists():
        return []
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("BGM catalog 解析失败 (%s): %s", catalog_path, exc)
        return []
    if not isinstance(data, list):
        logger.warning("BGM catalog 格式错误（应为数组）: %s", catalog_path)
        return []
    return [entry for entry in data if isinstance(entry, dict) and entry.get("enabled", True)]


def infer_bgm_profile(script_id: str, config: dict) -> dict:
    """从 manifest.json 的 title + visual_keywords 推断 mood/energy。

    推断逻辑：
    1. 合并 title + 所有 visual_keywords 为文本
    2. 对每个 mood 计算关键词命中数
    3. 取命中最多的 mood（平局时优先 tech）
    4. energy 从 mood 默认映射
    5. 无命中时默认 tech/medium
    6. manifest.json 不存在时返回默认 tech/medium

    返回 {"mood": str, "energy": str, "matched_keywords": list[str]}
    """
    from pipeline import validate_script_id  # noqa: circular import guard

    script_id = validate_script_id(script_id)
    manifest_path = _PROJECT_ROOT / "cheat" / "scripts" / script_id / "manifest.json"

    title = ""
    keywords: list[str] = []
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            title = manifest.get("title", "")
            keywords = manifest.get("visual_keywords", [])
        except (json.JSONDecodeError, OSError):
            pass

    text = title + " " + " ".join(keywords)
    return _match_mood(text)


def select_bgm(script_id: str, config: dict) -> tuple[Path | None, dict]:
    """选择 BGM。返回 (路径, 匹配信息)。

    匹配链：
    1. 加载 catalog（仅 bgm_catalog.json），过滤 enabled=true 且文件存在
    2. 推断脚本 mood/energy
    3. 优先：mood 精确匹配（同 mood 中选 energy 最接近的）
    4. 次选：energy 匹配
    5. 兜底：catalog 第一首可用
    6. catalog 为空/不存在 → 返回 (None, info) 并提示参考 example

    路径处理：
    - 相对路径 → 按 bgm_dir 拼接绝对路径
    - 绝对路径 → 直接使用

    reason 枚举值：
    - "catalog_missing": bgm_catalog.json 不存在
    - "no_available_files": catalog 存在但无可用文件
    - "mood_match": mood 精确匹配
    - "energy_match": energy 匹配（mood 未命中时）
    - "first_available": 兜底，使用 catalog 第一首可用文件
    """
    bgm_dir = _resolve_bgm_dir(config)
    profile = infer_bgm_profile(script_id, config)
    mood = profile["mood"]
    energy = profile["energy"]

    catalog = load_bgm_catalog(config)
    if not catalog:
        return None, {**profile, "bgm_id": None, "reason": "catalog_missing"}

    # 过滤文件存在的条目
    available = []
    for entry in catalog:
        path = _resolve_entry_path(entry, bgm_dir)
        if path.exists():
            available.append((entry, path))
    if not available:
        return None, {**profile, "bgm_id": None, "reason": "no_available_files"}

    # 1. mood 精确匹配（同 mood 中选 energy 最接近的）
    mood_matches = [(e, p) for e, p in available if e.get("mood") == mood]
    if mood_matches:
        best = _closest_energy(mood_matches, energy)
        return best[1], {**profile, "bgm_id": best[0]["id"], "reason": "mood_match"}

    # 2. energy 匹配
    energy_matches = [(e, p) for e, p in available if e.get("energy") == energy]
    if energy_matches:
        best = energy_matches[0]
        return best[1], {**profile, "bgm_id": best[0]["id"], "reason": "energy_match"}

    # 3. 兜底：第一首可用
    best = available[0]
    return best[1], {**profile, "bgm_id": best[0]["id"], "reason": "first_available"}


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _resolve_bgm_dir(config: dict) -> Path:
    bgm_dir = Path(config.get("paths", {}).get("bgm_dir", "assets/bgm"))
    if not bgm_dir.is_absolute():
        bgm_dir = _PROJECT_ROOT / bgm_dir
    return bgm_dir


def _resolve_entry_path(entry: dict, bgm_dir: Path) -> Path:
    raw = entry.get("path", "")
    p = Path(raw)
    if p.is_absolute():
        return p
    return bgm_dir / raw


def _match_mood(text: str) -> dict:
    """对文本计算 mood 关键词命中，返回最佳匹配。"""
    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}
    for mood, keywords in _MOOD_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in text]
        scores[mood] = len(hits)
        matched[mood] = hits

    best_mood = max(scores, key=lambda m: (scores[m], m == "tech"))
    if scores[best_mood] == 0:
        return {"mood": "tech", "energy": "medium", "matched_keywords": []}
    return {
        "mood": best_mood,
        "energy": _MOOD_DEFAULT_ENERGY[best_mood],
        "matched_keywords": matched[best_mood],
    }


def _closest_energy(pairs: list[tuple[dict, Path]], target: str) -> tuple[dict, Path]:
    """在 (entry, path) 列表中选 energy 最接近 target 的。"""
    target_val = _ENERGY_ORDER.get(target, 1)
    return min(pairs, key=lambda ep: abs(_ENERGY_ORDER.get(ep[0].get("energy", "medium"), 1) - target_val))

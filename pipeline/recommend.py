"""每日选题推荐：拉取候选 → LLM 打分 → 排序 → 缓存"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pipeline.topic import fetch_topics
from pipeline.llm_utils import call_llm, parse_llm_json

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))

_SCORE_PROMPT = """你是一位短视频选题编辑。请对以下 {n} 条候选话题进行评分。

评分维度（1-10 分）：
- virality: 传播潜力——标题是否吸引点击，是否容易引发讨论/转发
- freshness: 时效性——是否是近期热点，是否还有讨论窗口
- audience_fit: 受众匹配——是否适合 AI/科技类短视频博主的受众

候选列表：
{candidates_json}

请输出严格 JSON（只需 candidate_id + 分数 + reason，不要其他字段）：
{{
  "scores": [
    {{
      "candidate_id": "候选ID",
      "virality": 8,
      "freshness": 7,
      "audience_fit": 9,
      "reason": "一句话理由"
    }}
  ]
}}"""


def _today_local() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d")


def _daily_dir(cheat_root: Path, date_str: str | None = None) -> Path:
    """返回 cheat/daily/YYYY-MM-DD/ 目录路径，自动创建。"""
    if date_str is None:
        date_str = _today_local()
    d = cheat_root / "daily" / date_str
    d.mkdir(parents=True, exist_ok=True)
    return d


def _recommendations_path(cheat_root: Path, date_str: str | None = None) -> Path:
    return _daily_dir(cheat_root, date_str) / "recommendations.json"


def load_recommendations(config: dict, date_str: str | None = None) -> dict | None:
    """读缓存 recommendations.json，不存在返回 None。"""
    cheat_root = Path(config["paths"]["cheat_root"])
    path = _recommendations_path(cheat_root, date_str)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"读取推荐缓存失败: {e}")
        return None


def score_candidates(candidates: list[dict], config: dict) -> list[dict]:
    """单次 LLM 调用批量打分。

    返回 [{candidate_id, virality, freshness, audience_fit, composite, reason}]，按 composite 降序。
    只保留有打分的候选。composite 本地计算，不信任 LLM 返回值。
    """
    if not candidates:
        return []

    compact = [
        {"candidate_id": c["candidate_id"], "title": c["title"],
         "snapshot_text": c.get("snapshot_text", "")[:200]}
        for c in candidates
    ]

    prompt = _SCORE_PROMPT.format(
        n=len(compact),
        candidates_json=json.dumps(compact, ensure_ascii=False, indent=2),
    )

    response = call_llm(
        messages=[{"role": "user", "content": prompt}],
        model=config["models"]["score_model"],
        api_key=config["api"]["openai_api_key"],
        base_url=config["api"]["openai_base_url"],
        max_retries=config["llm"]["max_retries"],
        retry_delay_seconds=config["llm"]["retry_delay_seconds"],
        timeout_seconds=config["llm"]["timeout_seconds"],
        response_format={"type": "json_object"},
    )

    result = parse_llm_json(response, ["scores"])
    scores = result["scores"]

    # composite 本地计算
    for s in scores:
        v = s.get("virality", 0)
        f = s.get("freshness", 0)
        a = s.get("audience_fit", 0)
        s["composite"] = round((v + f + a) / 3, 2)

    scores.sort(key=lambda x: x["composite"], reverse=True)
    return scores


def _save_recommendations(data: dict, cheat_root: Path, date_str: str | None) -> Path:
    """原子写入 recommendations.json。"""
    path = _recommendations_path(cheat_root, date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"推荐已保存: {path}")
    return path


def recommend_topics(
    config: dict,
    *,
    category: str = "ai-models",
    take: int = 15,
    top_n: int = 3,
    force: bool = False,
    date_str: str | None = None,
) -> dict:
    """完整推荐流水线：fetch → score → rank → cache。

    date_str 默认为今天（本地时区）。有缓存且 force=False 时返回缓存。
    take <= 0 时自动修正为 15。
    """
    if take <= 0:
        take = 15

    cheat_root = Path(config["paths"]["cheat_root"])

    # 检查缓存
    if not force:
        cached = load_recommendations(config, date_str)
        if cached:
            logger.info(f"使用缓存 ({len(cached.get('recommendations', []))} 条)")
            return cached

    # 拉取候选
    candidates = fetch_topics(category=category, take=take, cheat_root=str(cheat_root))

    if not candidates:
        empty = {
            "generated_at": datetime.now(_CST).isoformat(timespec="seconds"),
            "category": category,
            "take": take,
            "top_n": top_n,
            "model": config["models"]["score_model"],
            "candidates_fetched": 0,
            "error": "API 返回空列表或调用失败",
            "recommendations": [],
        }
        path = _save_recommendations(empty, cheat_root, date_str)
        logger.warning(f"候选为空，已写空缓存: {path}")
        return empty

    # 打分
    scores = score_candidates(candidates, config)

    if not scores:
        empty = {
            "generated_at": datetime.now(_CST).isoformat(timespec="seconds"),
            "category": category,
            "take": take,
            "top_n": top_n,
            "model": config["models"]["score_model"],
            "candidates_fetched": len(candidates),
            "error": "LLM 未返回有效评分",
            "recommendations": [],
        }
        path = _save_recommendations(empty, cheat_root, date_str)
        logger.warning(f"打分为空，已写空缓存: {path}")
        return empty

    # 本地 join：用 candidate_id 关联回原始数据
    cand_map = {c["candidate_id"]: c for c in candidates}

    recommendations = []
    for s in scores[:top_n]:
        cid = s["candidate_id"]
        cand = cand_map.get(cid, {})
        recommendations.append({
            "candidate_id": cid,
            "title": cand.get("title", ""),
            "source": cand.get("source", ""),
            "snapshot_text": cand.get("snapshot_text", ""),
            "snapshot_at": cand.get("snapshot_at", ""),
            "url": cand.get("url", ""),
            "score": {
                "virality": s.get("virality", 0),
                "freshness": s.get("freshness", 0),
                "audience_fit": s.get("audience_fit", 0),
                "composite": s.get("composite", 0),
            },
            "reason": s.get("reason", ""),
        })

    output = {
        "generated_at": datetime.now(_CST).isoformat(timespec="seconds"),
        "category": category,
        "take": take,
        "top_n": top_n,
        "model": config["models"]["score_model"],
        "candidates_fetched": len(candidates),
        "error": None,
        "recommendations": recommendations,
    }

    path = _save_recommendations(output, cheat_root, date_str)
    logger.info(f"推荐完成: {len(recommendations)}/{len(scores)} 条，已写入 {path}")
    return output

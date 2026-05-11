"""质控：打分 + 重写循环 + 盲预测"""

import json
from pathlib import Path

from pipeline import validate_script_id
from pipeline.llm_utils import call_llm, parse_llm_json


def quality_check(script_id: str, config: dict) -> dict:
    """质控主流程。

    流程:
      1. 读取 cheat/scripts/<script_id>/draft.md
      2. adapter.score_script() 打分 → 写 score.json
      3. composite < threshold → 重写（最多 max_rewrites 轮）
      4. 通过后 adapter.write_prediction() 写 predictions/<id>.json + .md
      5. 最终版写 final.md + 更新 manifest.json
    返回: {passed, score, rewrites}
    """
    from adapter.cheat import load_rubric, score_script, write_prediction

    validate_script_id(script_id)
    cheat_root = Path(config["paths"]["cheat_root"])
    script_dir = cheat_root / "scripts" / script_id
    draft_path = script_dir / "draft.md"

    if not draft_path.exists():
        raise FileNotFoundError(f"脚本不存在: {draft_path}")

    script_text = draft_path.read_text(encoding="utf-8")
    rubric = load_rubric(config)
    threshold = config["quality"]["score_threshold"]
    max_rewrites = config["quality"]["max_rewrites"]

    best_score = None
    best_text = script_text
    rewrites = 0

    for attempt in range(max_rewrites + 1):
        score_result = score_script(script_text, rubric, config)

        if best_score is None or score_result["composite"] > best_score["composite"]:
            best_score = score_result
            best_text = script_text

        if score_result["composite"] >= threshold:
            # 先生成预测（可能失败），成功后再写文件
            prediction = _generate_prediction(script_text, score_result, config)

            # 预测成功 → 写 score.json + final.md（确保全部成功后才持久化）
            (script_dir / "score.json").write_text(
                json.dumps(score_result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (script_dir / "final.md").write_text(script_text, encoding="utf-8")

            # 写盲预测
            write_prediction(script_id, score_result, prediction, config)

            # 更新 manifest（最后更新，确保前面都成功）
            manifest_path = script_dir / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if "score" not in manifest["completed_steps"]:
                    manifest["completed_steps"].append("score")
                if "predict" not in manifest["completed_steps"]:
                    manifest["completed_steps"].append("predict")
                manifest["current_step"] = "tts"
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            return {"passed": True, "score": score_result, "rewrites": rewrites}

        # 未通过 → 重写
        if attempt < max_rewrites:
            script_text = _rewrite_script(script_text, score_result, config)
            rewrites += 1

    # 重写用尽，保存 best.md
    (script_dir / "best.md").write_text(best_text, encoding="utf-8")
    return {"passed": False, "score": best_score, "rewrites": rewrites}


def _rewrite_script(script_text: str, score_result: dict, config: dict) -> str:
    """根据打分反馈重写脚本。"""
    feedback = "\n".join(
        f"- {d['name']}: {d['score']}/10 — {d['reason']}"
        for d in score_result["dimensions"]
    )

    prompt = f"""请根据以下反馈重写短视频口播脚本，保持口语化风格：

当前评分（{score_result['composite']}/10）：
{feedback}

原始脚本：
{script_text}

要求：保持总时长 60-90 秒，分 4-6 段。只输出脚本文本，不要 JSON。"""

    return call_llm(
        messages=[{"role": "user", "content": prompt}],
        model=config["models"]["script_model"],
        api_key=config["api"]["openai_api_key"],
        base_url=config["api"]["openai_base_url"],
        max_retries=config["llm"]["max_retries"],
        retry_delay_seconds=config["llm"]["retry_delay_seconds"],
        timeout_seconds=config["llm"]["timeout_seconds"],
    )


def _generate_prediction(script_text: str, score_result: dict, config: dict) -> dict:
    """生成盲预测。"""
    from datetime import datetime, timezone

    prompt = f"""你是一位短视频数据分析师。请对以下口播脚本进行数据预测。

脚本内容：
{script_text}

评分：{score_result['composite']}/10

请输出 JSON（严格格式）：
{{
  "views": {{"p50": 整数, "p80": 整数}},
  "likes": {{"p50": 整数, "p80": 整数}},
  "comments": {{"p50": 整数, "p80": 整数}},
  "bucket": "S/A/B/C",
  "probability_distribution": {{"viral": 0.05, "high": 0.2, "medium": 0.5, "low": 0.25}},
  "drivers": ["看好因素1", "看好因素2"],
  "risks": ["风险因素1"],
  "platform": "douyin",
  "horizon_days": 3,
  "confidence": 0.7,
  "rationale": "预测理由"
}}"""

    response = call_llm(
        messages=[{"role": "user", "content": prompt}],
        model=config["models"]["predict_model"],
        api_key=config["api"]["openai_api_key"],
        base_url=config["api"]["openai_base_url"],
        max_retries=config["llm"]["max_retries"],
        retry_delay_seconds=config["llm"]["retry_delay_seconds"],
        timeout_seconds=config["llm"]["timeout_seconds"],
        response_format={"type": "json_object"},
    )

    prediction = parse_llm_json(response, ["views", "likes", "comments", "bucket"])
    prediction["rubric_version"] = "v0"
    prediction["created_at"] = datetime.now(timezone.utc).isoformat()
    return prediction

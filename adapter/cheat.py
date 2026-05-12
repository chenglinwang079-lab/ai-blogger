"""cheat-on-content 本地 adapter"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pipeline import validate_script_id
from pipeline.llm_utils import call_llm, parse_llm_json


def load_rubric(config: dict) -> dict:
    """读取 cheat/rubric_notes.md → 结构化 rubric。"""
    cheat_root = Path(config["paths"]["cheat_root"])
    rubric_path = cheat_root / "rubric_notes.md"

    if not rubric_path.exists():
        raise FileNotFoundError(f"评分标准不存在: {rubric_path}")

    text = rubric_path.read_text(encoding="utf-8")

    # 解析维度
    dimensions = []
    for m in re.finditer(r'\d+\.\s*\*\*(.+?)\*\*（(\d+)-(\d+)）：(.+)', text):
        name, low, high, desc = m.groups()
        dimensions.append({
            "name": name,
            "min": int(low),
            "max": int(high),
            "description": desc,
        })

    return {"dimensions": dimensions, "version": "v0"}


def score_script(script_text: str, rubric: dict, config: dict) -> dict:
    """LLM 按 rubric 打分（10 分制）。

    → {dimensions: [{name, score, reason}], composite}
    composite = 各维度均值（v0 等权）
    """
    dim_desc = "\n".join(
        f"- {d['name']}（{d['min']}-{d['max']}）：{d['description']}"
        for d in rubric["dimensions"]
    )

    prompt = f"""请对以下短视频口播脚本进行评分。

评分维度（10 分制）：
{dim_desc}

脚本内容：
{script_text}

请输出 JSON（严格格式）：
{{
  "dimensions": [
    {{"name": "维度名", "score": 8.0, "reason": "评分理由"}}
  ]
}}"""

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

    result = parse_llm_json(response, ["dimensions"])
    if not result["dimensions"]:
        raise ValueError("LLM 返回空评分维度列表")
    scores = [d["score"] for d in result["dimensions"]]
    result["composite"] = round(sum(scores) / len(scores), 2)

    return result


def write_prediction(script_id: str, score: dict, prediction: dict, config: dict, *, force: bool = False) -> None:
    """写盲预测（immutable）。

    已存在 predictions/<script_id>.json 时拒绝覆盖，除非 force=True。
    同时写 cheat/predictions/<script_id>.json（机器源）和 .md（人工审阅）。
    """
    validate_script_id(script_id)
    cheat_root = Path(config["paths"]["cheat_root"])
    pred_dir = cheat_root / "predictions"
    pred_dir.mkdir(parents=True, exist_ok=True)

    json_path = pred_dir / f"{script_id}.json"
    md_path = pred_dir / f"{script_id}.md"

    if json_path.exists() and not force:
        raise FileExistsError(f"预测已存在: {json_path}（immutable，不可覆盖；force=True 可覆盖）")

    # JSON（机器源）
    pred_with_score = {**prediction, "score": score}
    json_path.write_text(
        json.dumps(pred_with_score, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # MD（人工审阅）
    md_lines = [
        f"# 盲预测 — {script_id}\n",
        f"**评分**: {score['composite']}/10\n",
        f"**流量档位**: {prediction.get('bucket', '?')}",
        f"**置信度**: {prediction.get('confidence', '?')}\n",
        "## 数据预测\n",
        f"| 指标 | P50 | P80 |",
        f"|------|-----|-----|",
    ]
    for metric in ["views", "likes", "comments"]:
        m = prediction.get(metric, {})
        md_lines.append(f"| {metric} | {m.get('p50', '?')} | {m.get('p80', '?')} |")

    md_lines.extend([
        "\n## 看好因素\n",
        *[f"- {d}" for d in prediction.get("drivers", [])],
        "\n## 风险因素\n",
        *[f"- {r}" for r in prediction.get("risks", [])],
        f"\n## 预测理由\n\n{prediction.get('rationale', '')}",
        f"\n---\nrubric_version: {prediction.get('rubric_version', '?')} | created_at: {prediction.get('created_at', '?')}",
    ])
    md_path.write_text("\n".join(md_lines), encoding="utf-8")


def retro(script_id: str, actual: dict, config: dict) -> dict:
    """输入真实数据 → 对比预测 → 写 cheat/videos/<script_id>/report.md。

    更新 .cheat-state.json（calibration_samples, last_retro_at）。
    """
    validate_script_id(script_id)
    cheat_root = Path(config["paths"]["cheat_root"])

    # 读取预测
    pred_path = cheat_root / "predictions" / f"{script_id}.json"
    if not pred_path.exists():
        raise FileNotFoundError(f"预测不存在: {pred_path}")

    prediction = json.loads(pred_path.read_text(encoding="utf-8"))

    # 对比
    report_lines = [f"# 复盘报告 — {script_id}\n"]
    # 标题从 manifest.json 读取（prediction 中无 title 字段）
    manifest_path = cheat_root / "scripts" / script_id / "manifest.json"
    title = "?"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        title = manifest.get("title", "?")
    report_lines.append(f"**标题**: {title}\n")
    report_lines.append("## 预测 vs 实际\n")
    report_lines.append("| 指标 | P50 | P80 | 实际 | 偏差 |")
    report_lines.append("|------|-----|-----|------|------|")

    deviations = {}
    for metric in ["views", "likes", "comments", "shares"]:
        pred_metric = prediction.get(metric)
        if pred_metric:
            p50 = pred_metric.get("p50", 0)
            p80 = pred_metric.get("p80", 0)
        else:
            p50, p80 = 0, 0
        act = actual.get(metric, 0)
        dev_pct = ((act - p50) / p50 * 100) if p50 else None
        deviation = f"{dev_pct:.0f}%" if dev_pct is not None else ("-" if not pred_metric else "N/A")
        deviations[metric] = round(dev_pct, 1) if dev_pct is not None else None
        p50_str = str(p50) if pred_metric else "-"
        p80_str = str(p80) if pred_metric else "-"
        report_lines.append(f"| {metric} | {p50_str} | {p80_str} | {act} | {deviation} |")

    report_lines.extend([
        f"\n**预测档位**: {prediction.get('bucket', '?')}",
        f"**实际表现**: {'待评估' if actual.get('views', 0) > 0 else '无数据'}",
    ])

    # 写报告
    report_dir = cheat_root / "videos" / script_id
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.md").write_text("\n".join(report_lines), encoding="utf-8")

    # 更新 .cheat-state.json
    state_path = cheat_root / ".cheat-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"calibration_samples": 0, "last_retro_at": None, "rubric_version": "v0"}

    state["calibration_samples"] = state.get("calibration_samples", 0) + 1
    state["last_retro_at"] = datetime.now(timezone.utc).isoformat()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"report_path": str(report_dir / "report.md"), "deviations": deviations}

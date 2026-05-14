"""素材命中率统计与未命中关键词分析。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


def load_all_reports(dist_dir: Path) -> list[dict]:
    """扫描 dist/*/render_report.json，返回 [{script_id, report}...]。"""
    reports = []
    if not dist_dir.exists():
        return reports
    for d in sorted(dist_dir.iterdir()):
        if not d.is_dir():
            continue
        report_file = d / "render_report.json"
        if report_file.exists():
            try:
                report = json.loads(report_file.read_text(encoding="utf-8"))
                reports.append({"script_id": d.name, "report": report})
            except (json.JSONDecodeError, OSError):
                continue
    return reports


def aggregate_stats(reports: list[dict]) -> dict:
    """全局聚合统计。"""
    scripts_total = len(reports)
    segments_total = 0
    matched = 0
    abstract_fallback = 0
    gradient_fallback = 0
    quality_scores: list[float] = []
    missed_counter: Counter[str] = Counter()

    for item in reports:
        r = item["report"]
        segments_total += r.get("segments_total", 0)
        matched += r.get("matched", 0)
        abstract_fallback += r.get("abstract_fallback", 0)
        gradient_fallback += r.get("gradient_fallback", 0)
        # quality score（兼容旧 report 无 quality 字段）
        q = r.get("quality")
        if q and "score" in q:
            quality_scores.append(q["score"])
        # 从 segments 统计 gradient_fallback 关键词频次
        for seg in r.get("segments", []):
            if seg.get("source") in ("gradient_fallback", "gradient"):
                kw = (seg.get("keyword") or seg.get("visual_keyword") or "").strip()
                if kw:
                    missed_counter[kw] += 1

    match_rate = matched / segments_total if segments_total > 0 else 0.0
    avg_score = round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else None
    top_missed = [{"keyword": kw, "count": cnt} for kw, cnt in missed_counter.most_common(10)]
    return {
        "scripts_total": scripts_total,
        "segments_total": segments_total,
        "matched": matched,
        "abstract_fallback": abstract_fallback,
        "gradient_fallback": gradient_fallback,
        "match_rate": match_rate,
        "quality_score_avg": avg_score,
        "top_missed_keywords": top_missed,
    }


def per_script_stats(reports: list[dict]) -> list[dict]:
    """每脚本明细。"""
    rows = []
    for item in reports:
        r = item["report"]
        sid = item["script_id"]
        total = r.get("segments_total", 0)
        m = r.get("matched", 0)
        rate = m / total if total > 0 else 0.0
        rows.append({
            "script_id": sid,
            "segments_total": total,
            "matched": m,
            "abstract_fallback": r.get("abstract_fallback", 0),
            "gradient_fallback": r.get("gradient_fallback", 0),
            "match_rate": rate,
        })
    return rows


def format_stats_markdown(agg: dict, per_script: list[dict]) -> str:
    """Markdown 格式统计面板。"""
    lines = [
        "## 素材命中率统计",
        "",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 脚本总数 | {agg['scripts_total']} |",
        f"| 总段数 | {agg['segments_total']} |",
        f"| 直接命中 | {agg['matched']} |",
        f"| abstract 降级 | {agg['abstract_fallback']} |",
        f"| 渐变兜底 | {agg['gradient_fallback']} |",
        f"| **命中率** | **{agg['match_rate']:.1%}** |",
    ]
    if agg.get("quality_score_avg") is not None:
        lines.append(f"| **平均质量分** | **{agg['quality_score_avg']}** |")

    if per_script:
        lines += [
            "",
            "### 每脚本明细",
            "",
            "| script_id | 段数 | 命中 | abstract | 渐变 | 命中率 |",
            "|-----------|------|------|----------|------|--------|",
        ]
        for row in per_script:
            lines.append(
                f"| {row['script_id']} | {row['segments_total']} | {row['matched']} "
                f"| {row['abstract_fallback']} | {row['gradient_fallback']} "
                f"| {row['match_rate']:.1%} |"
            )

    return "\n".join(lines)


def collect_missed_keywords(reports: list[dict]) -> list[dict]:
    """source != 'matched' 的 keyword 按频次降序。
    返回 [{"keyword", "count", "reasons": {"abstract_fallback": n, "gradient_fallback": n}}]
    """
    counter: Counter[str] = Counter()
    reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for item in reports:
        for seg in item["report"].get("segments", []):
            if seg.get("source") != "matched":
                kw = seg.get("keyword", "")
                if kw:
                    counter[kw] += 1
                    reasons[kw][seg["source"]] += 1

    result = []
    for kw, count in counter.most_common():
        result.append({
            "keyword": kw,
            "count": count,
            "reasons": dict(reasons[kw]),
        })
    return result


def format_missed_keywords_markdown(missed: list[dict], top_n: int = 20) -> str:
    """Markdown 格式未命中关键词。"""
    if not missed:
        return "所有关键词均已命中，无未命中项。"

    lines = [
        "## 未命中关键词 Top N",
        "",
        "| 关键词 | 次数 | abstract | 渐变 |",
        "|--------|------|----------|------|",
    ]
    for item in missed[:top_n]:
        ab = item["reasons"].get("abstract_fallback", 0)
        gf = item["reasons"].get("gradient_fallback", 0)
        lines.append(f"| {item['keyword']} | {item['count']} | {ab} | {gf} |")

    return "\n".join(lines)

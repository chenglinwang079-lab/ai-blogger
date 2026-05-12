"""脚本：LLM 生成分段脚本"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from pipeline.llm_utils import call_llm, parse_llm_json


def make_script_id(title: str, script_text: str) -> str:
    return hashlib.sha256(f"{title}{script_text}".encode()).hexdigest()[:12]


SCRIPT_SYSTEM_PROMPT = """你是一位 AI 领域的短视频博主，风格口语化、有节奏感、信息密度高。

要求：
- 总时长 60-90 秒
- 分 4-6 段，每段 10-15 秒
- 开头 3 秒必须有钩子（反问/悬念/冲击性事实）
- 用词口语化，避免书面语
- 每段标注 visual_keyword（画面关键词）和 duration_est（预估秒数）

输出格式（严格 JSON）：
{
  "title": "视频标题",
  "segments": [
    {"text": "口播内容", "visual_keyword": "关键词", "duration_est": 12}
  ]
}"""

_SCRIPT_STYLES = [
    "强钩子观点型：开头直接抛出反直觉观点，用情绪化语言抓注意力",
    "信息密度解释型：密集信息量，用数据和案例支撑，节奏紧凑",
    "争议反转型：先抛主流观点再反驳，制造认知冲突",
]


def generate_script(topic: dict, config: dict) -> dict:
    """LLM 生成分段脚本。

    输入: topic dict（至少含 title, snapshot_text）
    输出: {script_id, title, segments, script_text, script_dir}
    创建 cheat/scripts/<script_id>/manifest.json + draft.md
    """
    user_prompt = f"""请根据以下选题生成短视频口播脚本：

标题：{topic['title']}
背景信息：{topic.get('snapshot_text', '')}
来源：{topic.get('url', '')}"""

    style = topic.get("style_hint", "")
    if style:
        user_prompt += f"\n风格要求：{style}"

    api_cfg = config["api"]
    model_cfg = config["models"]

    response = call_llm(
        messages=[
            {"role": "system", "content": SCRIPT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        model=model_cfg["script_model"],
        api_key=api_cfg["openai_api_key"],
        base_url=api_cfg["openai_base_url"],
        max_retries=config["llm"]["max_retries"],
        retry_delay_seconds=config["llm"]["retry_delay_seconds"],
        timeout_seconds=config["llm"]["timeout_seconds"],
        response_format={"type": "json_object"},
    )

    result = parse_llm_json(response, ["title", "segments"])
    title = result["title"]
    segments = result["segments"]

    # 生成 script_text（纯文本版，供打分用）
    script_text = "\n\n".join(seg["text"] for seg in segments)
    script_id = make_script_id(title, script_text)

    # 写入文件
    cheat_root = Path(config["paths"]["cheat_root"])
    script_dir = cheat_root / "scripts" / script_id
    script_dir.mkdir(parents=True, exist_ok=True)

    # draft.md
    draft_lines = [f"# {title}\n"]
    for i, seg in enumerate(segments, 1):
        draft_lines.append(f"## 段 {i} ({seg['duration_est']}s)")
        draft_lines.append(f"**画面**: {seg['visual_keyword']}")
        draft_lines.append(f"\n{seg['text']}\n")
    (script_dir / "draft.md").write_text("\n".join(draft_lines), encoding="utf-8")

    # manifest.json
    manifest = {
        "script_id": script_id,
        "candidate_id": topic.get("candidate_id"),
        "title": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_steps": ["topic", "script"],
        "current_step": "score",
    }
    (script_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "script_id": script_id,
        "title": title,
        "segments": segments,
        "script_text": script_text,
        "script_dir": str(script_dir),
    }


def generate_scripts(topic: dict, config: dict, count: int = 3) -> list[dict]:
    """为同一话题生成 count 个不同风格的候选脚本（批量工具函数）。"""
    results = []
    for i in range(count):
        style = _SCRIPT_STYLES[i % len(_SCRIPT_STYLES)]
        styled_topic = dict(topic)
        styled_topic["style_hint"] = style
        result = generate_script(styled_topic, config)
        results.append(result)
    return results

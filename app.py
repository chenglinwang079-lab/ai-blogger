"""AI Blogger 工作台 — Gradio WebUI"""

import json
import os
import re
import sys
import threading
import traceback
from pathlib import Path

import gradio as gr

PROJECT_ROOT = Path(__file__).parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from main import load_config, validate_config, ensure_directories
from pipeline import STEP_ORDER, validate_script_id

from pipeline.tts import generate_audio
from pipeline.video import render_video
from pipeline.script import generate_script
from pipeline.quality import quality_check
from pipeline.topic import fetch_topics
from pipeline.export_pkg import export_package
from adapter.cheat import retro

# ── Config ──────────────────────────────────────────────────────────────
config = load_config(PROJECT_ROOT / "config.toml")
# 不用 require_api=True 阻塞启动，UI 内显示警告
validate_config(config, require_api=False)
ensure_directories(config)

CHEAT_ROOT = PROJECT_ROOT / config["paths"]["cheat_root"]
OUTPUT_DIR = PROJECT_ROOT / config["paths"]["output_dir"]

tts_lock = threading.Lock()

# ── 辅助函数 ─────────────────────────────────────────────────────────────


def safe_call(fn, *args, **kwargs):
    """调用 pipeline 函数，返回 (result, error_traceback)。UI 显示 + 终端 print。"""
    try:
        return fn(*args, **kwargs), None
    except Exception:
        err = traceback.format_exc()
        print(err)
        return None, err


def ensure_venv_warning() -> str:
    """检查 Python 环境，用 Path.resolve() 避免大小写/斜杠差异。"""
    if Path(sys.executable).resolve() != Path(r"D:\voxcpm\venv\Scripts\python.exe").resolve():
        return f"⚠️ 当前 Python: `{sys.executable}`\n\n应使用: `D:\\voxcpm\\venv\\Scripts\\python.exe`"
    return f"✅ Python: `{sys.executable}`"


def parse_script_id(choice: str) -> str | None:
    """从 Dropdown 选项中提取 script_id（12 位 hex）。格式：'标题 | abc123def456 | export ✅'"""
    if not choice:
        return None
    parts = [p.strip() for p in choice.split("|")]
    for part in parts:
        if re.fullmatch(r"[a-f0-9]{12}", part):
            return part
    return None


def existing_path(path) -> str | None:
    """文件存在返回路径字符串，不存在返回 None。"""
    return str(path) if Path(path).exists() else None


def get_script_choices() -> list[str]:
    """返回 ['标题 | script_id | export ✅', ...] 格式。无脚本时返回空列表。"""
    scripts_dir = CHEAT_ROOT / "scripts"
    if not scripts_dir.exists():
        return []
    choices = []
    for manifest_path in sorted(scripts_dir.glob("*/manifest.json")):
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sid = m.get("script_id", "?")
        title = m.get("title", "?")
        completed = set(m.get("completed_steps", []))
        # 显示最高完成步骤
        status = "new"
        for step in reversed(STEP_ORDER):
            if step in completed:
                status = step
                break
        icon = "✅" if status == "export" or m.get("current_step") == "done" else "⏳"
        choices.append(f"{title} | {sid} | {status} {icon}")
    return choices


def refresh_dropdown():
    """刷新所有脚本 Dropdown 的选项。"""
    choices = get_script_choices()
    return gr.update(choices=choices, value=None)


def render_step_pipeline(manifest: dict) -> str:
    """彩色 HTML 步骤条（绿=完成, 蓝=当前, 灰=待做）。"""
    completed = set(manifest.get("completed_steps", []))
    current = manifest.get("current_step", "")
    parts = []
    for step in STEP_ORDER:
        if step in completed:
            bg, fg, icon = "#4caf50", "#fff", "✓"
        elif step == current:
            bg, fg, icon = "#2196f3", "#fff", "▶"
        else:
            bg, fg, icon = "#e0e0e0", "#999", "·"
        parts.append(
            f'<span style="background:{bg};color:{fg};padding:4px 12px;'
            f'border-radius:12px;font-size:13px">{icon} {step}</span>'
        )
    return " → ".join(parts)


def render_score_table(score_data: dict) -> str:
    """动态渲染 N 维度分数表格 + 条形图。"""
    dims = score_data.get("dimensions", [])
    if not dims:
        return "无评分数据"
    rows = []
    for dim in dims:
        score = dim.get("score", 0)
        bar_w = int(score * 20)
        color = "#4caf50" if score >= 7.5 else "#ff9800" if score >= 6 else "#f44336"
        rows.append(
            f"| {dim['name']} | {score} "
            f"| <div style='background:{color};width:{bar_w}px;height:16px;border-radius:3px'></div> "
            f"| {dim.get('reason', '')} |"
        )
    header = "| 维度 | 分数 | 条形 | 评语 |\n|------|------|------|------|"
    composite = score_data.get("composite", 0)
    return header + "\n" + "\n".join(rows) + f"\n\n**综合分: {composite}**"


def render_prediction_table(pred: dict) -> str:
    """渲染盲预测表格。"""
    lines = ["| 指标 | P50 | P80 |", "|------|-----|-----|"]
    for metric in ["views", "likes", "comments"]:
        m = pred.get(metric, {})
        lines.append(f"| {metric} | {m.get('p50', '-'):,} | {m.get('p80', '-'):,} |")
    bucket = pred.get("bucket", "?")
    conf = pred.get("confidence", "?")
    lines.append(f"\n**Bucket**: {bucket} | **Confidence**: {conf}")
    drivers = pred.get("drivers", [])
    risks = pred.get("risks", [])
    if drivers:
        lines.append("\n**驱动因素**: " + "、".join(drivers))
    if risks:
        lines.append("**风险因素**: " + "、".join(risks))
    return "\n".join(lines)


def load_file_text(path: Path, max_chars: int = 5000) -> str:
    """读取文本文件，超长截断。"""
    if not path.exists():
        return f"文件不存在: {path}"
    text = path.read_text(encoding="utf-8")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n... (截断，共 {len(text)} 字符)"
    return text


def env_info_md() -> str:
    """Tab 7 环境信息 Markdown。"""
    api_key = config.get("api", {}).get("openai_api_key", "")
    mimo_key = os.environ.get("MIMO_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    voxcpm_path = Path(config["paths"].get("voxcpm_model", ""))

    lines = [
        "## 环境信息",
        f"- **Python**: `{sys.executable}`",
        f"- **Gradio**: {gr.__version__}",
        f"- **Working dir**: `{PROJECT_ROOT}`",
        f"- **MIMO_API_KEY**: {'✅ set' if mimo_key else '❌ missing'}",
        f"- **OPENAI_API_KEY**: {'✅ set' if openai_key else '❌ missing'}",
        f"- **config api_key**: {'✅ set' if api_key else '❌ missing (fallback to env)'}",
        f"- **VoxCPM2 model**: {'✅ exists' if voxcpm_path.exists() else '❌ missing'} — `{voxcpm_path}`",
    ]
    venv_warn = ensure_venv_warning()
    if venv_warn.startswith("⚠️"):
        lines.append(f"\n> {venv_warn}")
    return "\n".join(lines)


def scripts_overview_md() -> str:
    """Tab 7 脚本总览表格。"""
    scripts_dir = CHEAT_ROOT / "scripts"
    if not scripts_dir.exists():
        return "暂无脚本"
    manifests = sorted(scripts_dir.glob("*/manifest.json"))
    if not manifests:
        return "暂无脚本"
    rows = ["| script_id | 标题 | 创建时间 | 当前步骤 | 已完成 |",
            "|-----------|------|----------|----------|--------|"]
    for mp in manifests:
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sid = m.get("script_id", "?")
        title = m.get("title", "?")[:30]
        created = m.get("created_at", "?")[:19]
        current = m.get("current_step", "?")
        completed = len(m.get("completed_steps", []))
        rows.append(f"| {sid} | {title} | {created} | {current} | {completed}/7 |")
    return "\n".join(rows)


def script_detail_md(choice: str) -> str:
    """Tab 7 选中脚本详情。"""
    sid = parse_script_id(choice)
    if not sid:
        return "点击上方表格行查看详情"
    manifest_path = CHEAT_ROOT / "scripts" / sid / "manifest.json"
    if not manifest_path.exists():
        return f"manifest.json 不存在: {sid}"
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    pipeline_html = render_step_pipeline(m)

    # 文件存在检查
    dist_dir = OUTPUT_DIR / sid
    files_check = []
    for name in ["audio.wav", "final.mp4", "subtitle.srt", "timestamps.json", "thumbnail.png"]:
        p = dist_dir / name
        files_check.append(f"- {'✅' if p.exists() else '❌'} `{name}`")

    return (
        f"### {m.get('title', '?')}\n\n"
        f"**script_id**: `{sid}` | **created**: {m.get('created_at', '?')[:19]}\n\n"
        f"**Pipeline**: {pipeline_html}\n\n"
        f"### 产物文件\n" + "\n".join(files_check)
    )


# ── 回调：Tab 1 选题 ─────────────────────────────────────────────────────


def fetch_topics_cb(category: str, take: int):
    """拉取候选选题。"""
    result, err = safe_call(fetch_topics, category=category, take=int(take), cheat_root=str(CHEAT_ROOT))
    if err:
        return [], f"```\n{err}\n```", "[]"
    topics = result
    if not topics:
        return [], "⚠️ API 返回空列表或调用失败", "[]"
    rows = [
        [t.get("candidate_id", ""), t.get("title", ""), t.get("source", ""),
         t.get("snapshot_at", "")[:19], t.get("url", "")]
        for t in topics
    ]
    return rows, f"✅ 拉取到 {len(topics)} 条候选选题", json.dumps(topics, ensure_ascii=False)


def on_topic_select(evt: gr.SelectData, topics_json: str):
    """点击候选表行 → 存入 State。"""
    try:
        topics = json.loads(topics_json)
        if evt.index[0] < len(topics):
            return json.dumps(topics[evt.index[0]], ensure_ascii=False)
    except (json.JSONDecodeError, IndexError):
        pass
    return ""


# ── 回调：Tab 2 脚本 ─────────────────────────────────────────────────────


def generate_script_cb(title: str, summary: str, url: str, selected_topic_json: str):
    """生成脚本。"""
    if not title and selected_topic_json:
        try:
            topic = json.loads(selected_topic_json)
        except json.JSONDecodeError:
            yield "❌ 选中 topic 数据损坏", "", "", ""
            return
    elif title:
        topic = {"title": title, "snapshot_text": summary or "", "url": url or "", "source": "manual"}
    else:
        yield "❌ 请输入选题标题或从选题页选择", "", "", ""
        return

    yield "⏳ 正在生成脚本...", "", "", ""
    result, err = safe_call(generate_script, topic, config)
    if err:
        yield f"```\n{err}\n```", "", "", ""
        return

    sid = result["script_id"]
    script_dir = Path(result["script_dir"])
    draft_md = load_file_text(script_dir / "draft.md")
    manifest = json.loads((script_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest_html = render_step_pipeline(manifest)
    script_json = json.dumps(result, ensure_ascii=False, indent=2)

    yield f"✅ 脚本生成完成: `{sid}`\n\n{manifest_html}", draft_md, script_json, sid


# ── 回调：Tab 3 质控 ─────────────────────────────────────────────────────


def quality_check_cb(script_id_choice: str):
    """执行质控。"""
    sid = parse_script_id(script_id_choice)
    if not sid:
        yield "❌ 请选择脚本", "", ""
        return

    yield f"⏳ 正在质控 `{sid}`...", "", ""
    result, err = safe_call(quality_check, sid, config)
    if err:
        yield f"```\n{err}\n```", "", ""
        return

    score_html = render_score_table(result["score"])
    passed = "✅ 通过" if result["passed"] else "❌ 未通过"
    rewrites = result["rewrites"]

    # 读取预测
    pred_path = CHEAT_ROOT / "predictions" / f"{sid}.json"
    pred_html = ""
    if pred_path.exists():
        pred = json.loads(pred_path.read_text(encoding="utf-8"))
        pred_html = render_prediction_table(pred)

    status = f"**{passed}** | 重写次数: {rewrites} | 综合分: {result['score']['composite']}"
    yield status, score_html, pred_html


# ── 回调：Tab 4 生成 ─────────────────────────────────────────────────────


def tts_gen_cb(script_id_choice: str):
    """TTS 生成（generator yield 进度）。"""
    sid = parse_script_id(script_id_choice)
    if not sid:
        yield "❌ 请选择脚本", None, "", ""
        return

    yield "⏳ 正在生成音频...", None, "", ""

    with tts_lock:
        result, err = safe_call(generate_audio, sid, config)

    if err:
        yield f"```\n{err}\n```", None, "", ""
        return

    segments_total = result.get("segments_total", "?")
    segments_voxcpm2 = result.get("segments_voxcpm2", 0)
    segments_edge_tts = result.get("segments_edge_tts", 0)
    fallback_reason = result.get("fallback_reason") or "none"

    yield (
        f"✅ 音频完成\n"
        f"backend: {result['backend']}\n"
        f"VoxCPM2: {segments_voxcpm2}/{segments_total} 段\n"
        f"edge-tts: {segments_edge_tts}/{segments_total} 段\n"
        f"fallback_reason: {fallback_reason}\n"
        f"duration: {result['duration']}s\n"
        f"path: {result['audio_path']}",
        existing_path(result["audio_path"]),
        result["backend"],
        f"{result['duration']}s",
    )


def _read_render_report(sid: str) -> str:
    """读取 render_report.json，返回命中率摘要。"""
    report_path = OUTPUT_DIR / sid / "render_report.json"
    if not report_path.exists():
        return ""
    try:
        r = json.loads(report_path.read_text("utf-8"))
        if r.get("render_mode") != "footage":
            return ""
        return (
            f"\n\n素材命中: {r['footage_hits']}/{r['segments_total']}\n"
            f"关键词命中: {r['matched']}  |  abstract fallback: {r['abstract_fallback']}  |  渐变 fallback: {r['gradient_fallback']}"
        )
    except Exception:
        return ""


def render_gen_cb(script_id_choice: str, render_mode: str = "gradient"):
    """视频渲染（generator yield 进度）。"""
    sid = parse_script_id(script_id_choice)
    if not sid:
        yield "❌ 请选择脚本", None, ""
        return

    yield "⏳ 正在渲染视频...", None, ""

    # 临时覆盖 render_mode
    cfg = dict(config)
    cfg["video"] = dict(config["video"])
    cfg["video"]["render_mode"] = render_mode
    cfg["paths"] = dict(config["paths"])

    result, err = safe_call(render_video, sid, cfg)
    if err:
        yield f"```\n{err}\n```", None, ""
        return

    report_info = _read_render_report(sid)
    yield f"✅ 视频完成\npath: {result}{report_info}", existing_path(result), report_info


def stock_fill_cb(script_id_choice: str):
    """补充外部素材（generator yield 进度）。"""
    sid = parse_script_id(script_id_choice)
    if not sid:
        yield "❌ 请选择脚本"
        return

    from pipeline.stock import fill_footage_for_script
    yield "⏳ 正在补充外部素材..."
    result, err = safe_call(fill_footage_for_script, sid, config)
    if err:
        yield f"❌ 失败:\n```\n{err}\n```"
        return

    mk = result["missing_keywords"]
    dl = result["downloaded"]
    sc = result["skipped_cached"]
    fl = result["failed"]
    lines = [f"**缺失关键词**: {len(mk)}"]
    if not mk:
        lines.append("无需补充素材")
    else:
        lines.append(f"**下载成功**: {len(dl)}")
        if sc:
            lines.append(f"**跳过缓存**: {len(sc)}")
        if fl:
            lines.append(f"**失败**: {len(fl)}")
            for f in fl:
                lines.append(f"  - {f['keyword']} ({f['provider']}): {f['error']}")
    yield "\n".join(lines)


def pipeline_gen_cb(script_id_choice: str, render_mode: str = "gradient"):
    """TTS + 渲染一键执行（generator 顺序 yield）。"""
    sid = parse_script_id(script_id_choice)
    if not sid:
        yield "❌ 请选择脚本", None, None, ""
        return

    # TTS
    yield "⏳ [1/2] 正在生成音频...", None, None, ""
    with tts_lock:
        tts_result, tts_err = safe_call(generate_audio, sid, config)
    if tts_err:
        yield f"```\n{tts_err}\n```", None, None, ""
        return

    seg_total = tts_result.get("segments_total", "?")
    seg_vox = tts_result.get("segments_voxcpm2", 0)
    seg_edge = tts_result.get("segments_edge_tts", 0)
    fb_reason = tts_result.get("fallback_reason") or "none"
    audio_info = (
        f"audio: {tts_result['audio_path']}\n"
        f"backend: {tts_result['backend']} "
        f"(VoxCPM2: {seg_vox}/{seg_total}, edge-tts: {seg_edge}/{seg_total}, "
        f"fallback: {fb_reason})\n"
        f"duration: {tts_result['duration']}s"
    )

    # 临时覆盖 render_mode
    cfg = dict(config)
    cfg["video"] = dict(config["video"])
    cfg["video"]["render_mode"] = render_mode
    cfg["paths"] = dict(config["paths"])

    # Render
    yield "⏳ [2/2] 正在渲染视频...", existing_path(tts_result["audio_path"]), None, audio_info
    render_result, render_err = safe_call(render_video, sid, cfg)
    if render_err:
        yield f"```\n{render_err}\n```", existing_path(tts_result["audio_path"]), None, audio_info
        return

    report_info = _read_render_report(sid)
    full_info = audio_info + report_info
    yield (
        f"✅ 全部完成\n{audio_info}\nvideo: {render_result}{report_info}",
        existing_path(tts_result["audio_path"]),
        existing_path(render_result),
        full_info,
    )


# ── 回调：Tab 5 发布包 ───────────────────────────────────────────────────


def export_cb(script_id_choice: str):
    """导出发布包。"""
    sid = parse_script_id(script_id_choice)
    if not sid:
        yield "❌ 请选择脚本", None, "", "", "", ""
        return

    yield "⏳ 正在导出...", None, "", "", "", ""
    result, err = safe_call(export_package, sid, config)
    if err:
        yield f"```\n{err}\n```", None, "", "", "", ""
        return

    out_dir = Path(result)
    thumb = existing_path(out_dir / "thumbnail.png")
    title = load_file_text(out_dir / "title.txt", 500).strip()
    desc = load_file_text(out_dir / "description.txt", 1000).strip()
    tags = load_file_text(out_dir / "tags.txt", 200).strip()

    # 文件列表
    files = []
    for f in sorted(out_dir.iterdir()):
        size = f.stat().st_size
        files.append(f"| {f.name} | {size:,} bytes |")
    file_list = "| 文件 | 大小 |\n|------|------|\n" + "\n".join(files) if files else "无文件"

    yield f"✅ 已导出到: {result}", thumb, title, desc, tags, file_list


# ── 回调：Tab 8 工作流 ─────────────────────────────────────────────────────


def wf_fetch_cb(category, count):
    """Step 1: 拉取选题。"""
    from pipeline.topic import fetch_topics
    topics = fetch_topics(category=category, take=int(count), cheat_root=str(CHEAT_ROOT))
    table = [[t["title"], t.get("source", ""), t["candidate_id"][:8]] for t in topics]
    state = {"topics": topics, "selected_topic": None, "scripts": [], "selected_script_id": None, "quality_passed": False}
    return table, state, "已拉取，请在表格中选择一行"


def wf_select_topic(evt: gr.SelectData, state):
    """Step 1: 选中话题。"""
    row = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    topics = state.get("topics", [])
    if row >= len(topics):
        return state, "选择无效", gr.update()
    topic = topics[row]
    state["selected_topic"] = topic
    info = f"**已选**: {topic['title']}\n\n{topic.get('snapshot_text', '')[:300]}..."
    return state, info, gr.update(interactive=True)


def wf_gen_cb(state):
    """Step 2: 逐个生成候选脚本，yield 进度。"""
    from pipeline.script import generate_script, _SCRIPT_STYLES
    topic = state.get("selected_topic")
    if not topic:
        yield state, "请先选择话题", gr.update(), ""
        return

    scripts = []
    for i in range(3):
        yield state, f"⏳ 正在生成候选 {i+1}/3...", gr.update(), ""
        style = _SCRIPT_STYLES[i % len(_SCRIPT_STYLES)]
        styled_topic = dict(topic)
        styled_topic["style_hint"] = style
        try:
            result = generate_script(styled_topic, config)
            scripts.append(result)
        except Exception:
            err = traceback.format_exc()
            yield state, f"⚠️ 候选 {i+1} 失败:\n```\n{err}\n```", gr.update(), ""
            continue

    if not scripts:
        yield state, "❌ 全部候选生成失败", gr.update(), ""
        return

    state["scripts"] = scripts
    state["selected_script_id"] = None
    state["quality_passed"] = False

    from pipeline.script import _SCRIPT_STYLES as styles
    choices = []
    for j, s in enumerate(scripts):
        style_name = styles[j].split("：")[0] if j < len(styles) else f"候选 {j+1}"
        choices.append(f"候选 {j+1} | {s['script_id']} | {style_name} | {s['title']}")

    status = f"✅ 生成 {len(scripts)}/3 个候选，请选择"
    yield state, status, gr.update(choices=choices, interactive=True, value=None), ""


def wf_select_script(value, state):
    """Step 2: 选中候选脚本。"""
    if not value:
        return state, "", gr.update()
    sid = parse_script_id(value)
    if not sid:
        return state, "选择无效", gr.update()
    state["selected_script_id"] = sid
    state["quality_passed"] = False
    # 读 draft.md 预览
    draft_path = CHEAT_ROOT / "scripts" / sid / "draft.md"
    preview = ""
    if draft_path.exists():
        preview = load_file_text(draft_path, 2000)
    return state, preview, gr.update(interactive=True)


def wf_quality_cb(state):
    """Step 3: 质控打分。"""
    from pipeline.quality import quality_check
    sid = state.get("selected_script_id")
    if not sid:
        yield state, "请先选择候选脚本", gr.update(), gr.update(), gr.update()
        return

    # 禁用质控按钮防重复点击
    yield state, "⏳ 正在质控...", gr.update(), gr.update(), gr.update(interactive=False)
    try:
        result = quality_check(sid, config)
    except Exception:
        err = traceback.format_exc()
        yield state, f"❌ 质控失败:\n```\n{err}\n```", gr.update(), gr.update(), gr.update(interactive=True)
        return

    score = result.get("score", {})
    passed = result.get("passed", False)
    composite = score.get("composite", "?")

    # 加载详细数据用于渲染
    score_path = CHEAT_ROOT / "scripts" / sid / "score.json"
    score_data = json.loads(score_path.read_text("utf-8")) if score_path.exists() else {}
    pred_path = CHEAT_ROOT / "predictions" / f"{sid}.json"
    pred = json.loads(pred_path.read_text("utf-8")) if pred_path.exists() else {}

    md = f"**综合分: {composite}** | {'✅ 通过' if passed else '❌ 未通过'}\n\n"
    if score_data:
        md += render_score_table(score_data) + "\n\n"
    if pred:
        md += render_prediction_table(pred)

    state["quality_passed"] = passed
    if passed:
        yield state, md, gr.update(visible=True), gr.update(), gr.update(interactive=False)
    else:
        md += "\n\n⚠️ 未达到阈值，请重新生成候选"
        yield state, md, gr.update(visible=False), gr.update(), gr.update(interactive=True)


def wf_go_cb(state):
    """Step 3: 确认继续，解锁 Step 4。"""
    return gr.update(interactive=True)


def wf_pipeline_cb(state, render_mode):
    """Step 4: TTS → 渲染 → 导出。"""
    sid = state.get("selected_script_id")
    if not sid:
        yield "请先完成质控", None, "", gr.update()
        return

    cfg = dict(config)
    cfg["video"] = dict(config["video"])
    cfg["video"]["render_mode"] = render_mode
    cfg["paths"] = dict(config["paths"])

    # 禁用按钮防重复点击
    pipe_disabled = gr.update(interactive=False)

    # TTS
    yield "⏳ [1/3] TTS...", None, "", pipe_disabled
    with tts_lock:
        tts_result, tts_err = safe_call(generate_audio, sid, cfg)
    if tts_err:
        yield f"❌ TTS 失败:\n```\n{tts_err}\n```", None, "", gr.update(interactive=True)
        return

    # Render
    yield "⏳ [2/3] 渲染...", None, "", pipe_disabled
    render_result, render_err = safe_call(render_video, sid, cfg)
    if render_err:
        yield f"❌ 渲染失败:\n```\n{render_err}\n```", None, "", gr.update(interactive=True)
        return

    # Export
    yield "⏳ [3/3] 导出...", None, "", pipe_disabled
    export_result, export_err = safe_call(export_package, sid, cfg)
    if export_err:
        yield f"❌ 导出失败:\n```\n{export_err}\n```", None, "", gr.update(interactive=True)
        return

    # 结果摘要
    out_dir = Path(export_result)
    title = load_file_text(out_dir / "title.txt", 200).strip()
    tags = load_file_text(out_dir / "tags.txt", 200).strip()
    report = _read_render_report(sid)

    summary = (
        f"✅ 全部完成\n\n"
        f"**发布包**: `{export_result}`\n"
        f"**视频**: `{render_result}`\n"
        f"**标题**: {title}\n"
        f"**标签**: {tags}{report}"
    )
    yield summary, existing_path(render_result), export_result, pipe_disabled


# ── 回调：Tab 6 复盘 ─────────────────────────────────────────────────────


def load_prediction_cb(script_id_choice: str):
    """加载已有预测数据。"""
    sid = parse_script_id(script_id_choice)
    if not sid:
        return "请选择脚本"
    pred_path = CHEAT_ROOT / "predictions" / f"{sid}.json"
    if not pred_path.exists():
        return f"预测不存在: {sid}"
    pred = json.loads(pred_path.read_text(encoding="utf-8"))
    return render_prediction_table(pred)


def retro_cb(script_id_choice: str, views: int, likes: int, comments: int):
    """生成复盘报告。"""
    sid = parse_script_id(script_id_choice)
    if not sid:
        yield "❌ 请选择脚本", ""
        return

    actual = {"views": int(views), "likes": int(likes), "comments": int(comments)}
    yield f"⏳ 正在生成复盘报告...", ""
    result, err = safe_call(retro, sid, actual, config)
    if err:
        yield f"```\n{err}\n```", ""
        return

    report = load_file_text(Path(result["report_path"]))
    yield f"✅ 复盘报告已生成: {result['report_path']}", report


# ── 回调：Tab 7 状态 ─────────────────────────────────────────────────────


def refresh_status():
    """刷新状态页。"""
    return env_info_md(), scripts_overview_md()


def show_detail(choice: str):
    """显示脚本详情。"""
    return script_detail_md(choice)




# ── UI 构建 ──────────────────────────────────────────────────────────────

CSS = """
.step-done { background: #4caf50; color: white; }
.step-current { background: #2196f3; color: white; }
.step-pending { background: #e0e0e0; color: #999; }
"""

with gr.Blocks(title="AI Blogger 工作台") as app:

    # ── Header ──
    gr.Markdown("# 🎬 AI Blogger 工作台")

    api_key = config.get("api", {}).get("openai_api_key", "")
    if not api_key:
        gr.Markdown(
            "⚠️ **API Key 未配置**。请设置环境变量 `MIMO_API_KEY` 或在 `config.toml` 中填写。"
            "LLM 相关功能（脚本、质控）将不可用。"
        )

    # 共享 State
    selected_topic = gr.State(value="")
    all_topics = gr.State(value="[]")

    with gr.Tabs() as tabs:

        # ════════════════════════════════════════════════════════════════
        # Tab 7: 状态
        # ════════════════════════════════════════════════════════════════
        with gr.Tab("7️⃣ 状态"):
            with gr.Row():
                btn_refresh_status = gr.Button("刷新")

            env_md = gr.Markdown(value=env_info_md(), label="环境信息")
            scripts_md = gr.Markdown(value=scripts_overview_md(), label="脚本总览")

            # 用 Dataframe 做脚本选择（点击行可查看详情）
            with gr.Row():
                status_dropdown = gr.Dropdown(
                    label="选择脚本查看详情",
                    choices=get_script_choices(),
                    interactive=True,
                )
                btn_detail = gr.Button("查看详情")
            detail_md = gr.Markdown(value="选择脚本查看详情", label="脚本详情")

            btn_refresh_status.click(
                fn=refresh_status,
                outputs=[env_md, scripts_md],
            )
            btn_detail.click(
                fn=show_detail,
                inputs=[status_dropdown],
                outputs=[detail_md],
            )

        # ════════════════════════════════════════════════════════════════
        # Tab 1: 选题
        # ════════════════════════════════════════════════════════════════
        with gr.Tab("1️⃣ 选题"):
            with gr.Row():
                category_dd = gr.Dropdown(
                    label="分类",
                    choices=["ai-models", "ai-products", "ai-industry", "ai-tips"],
                    value="ai-models",
                )
                take_slider = gr.Slider(label="数量", minimum=3, maximum=30, value=10, step=1)
                btn_fetch = gr.Button("拉取选题", variant="primary")

            topic_status = gr.Markdown()
            topic_table = gr.Dataframe(
                headers=["candidate_id", "title", "source", "时间", "url"],
                interactive=False,
                label="候选选题",
            )
            topic_preview = gr.Markdown(label="选中选题详情")

            btn_fetch.click(
                fn=fetch_topics_cb,
                inputs=[category_dd, take_slider],
                outputs=[topic_table, topic_status, all_topics],
            )
            topic_table.select(
                fn=on_topic_select,
                inputs=[all_topics],
                outputs=[selected_topic],
            )
            # 选中后预览
            selected_topic.change(
                fn=lambda s: f"```\n{s}\n```" if s else "点击表格行选择选题",
                inputs=[selected_topic],
                outputs=[topic_preview],
            )

        # ════════════════════════════════════════════════════════════════
        # Tab 4: 生成
        # ════════════════════════════════════════════════════════════════
        with gr.Tab("4️⃣ 生成"):
            with gr.Row():
                gen_dropdown = gr.Dropdown(
                    label="选择脚本",
                    choices=get_script_choices(),
                    interactive=True,
                )
                btn_gen_refresh = gr.Button("刷新列表")

            gr.Markdown("### TTS 音频生成")
            btn_tts = gr.Button("生成音频", variant="primary")
            tts_status = gr.Markdown()
            with gr.Row():
                tts_audio = gr.Audio(label="音频预览", type="filepath")
                tts_backend = gr.Textbox(label="Backend", interactive=False)
                tts_duration = gr.Textbox(label="Duration", interactive=False)

            gr.Markdown("### 补充外部素材")
            btn_stock = gr.Button("补充外部素材", variant="secondary")
            stock_status = gr.Markdown()

            gr.Markdown("### 视频渲染")
            render_mode = gr.Radio(
                choices=["gradient", "footage"],
                value="gradient",
                label="渲染模式",
                info="渐变背景 / 本地素材混剪",
            )
            btn_render = gr.Button("渲染视频", variant="primary")
            render_status = gr.Markdown()
            with gr.Row():
                render_video_out = gr.Video(label="视频预览")
                render_report = gr.Textbox(label="素材命中", interactive=False, lines=3)

            gr.Markdown("### 一键执行")
            btn_pipeline = gr.Button("TTS + 渲染一键执行", variant="stop")
            pipeline_status = gr.Markdown()
            with gr.Row():
                pipeline_audio = gr.Audio(label="音频", type="filepath")
                pipeline_video = gr.Video(label="视频")
            pipeline_info = gr.Textbox(label="文件路径", interactive=False)

            btn_gen_refresh.click(fn=refresh_dropdown, outputs=[gen_dropdown])

            btn_tts.click(
                fn=tts_gen_cb,
                inputs=[gen_dropdown],
                outputs=[tts_status, tts_audio, tts_backend, tts_duration],
                concurrency_limit=1,
            )
            btn_stock.click(
                fn=stock_fill_cb,
                inputs=[gen_dropdown],
                outputs=[stock_status],
                concurrency_limit=1,
            )
            btn_render.click(
                fn=render_gen_cb,
                inputs=[gen_dropdown, render_mode],
                outputs=[render_status, render_video_out, render_report],
                concurrency_limit=1,
            )
            btn_pipeline.click(
                fn=pipeline_gen_cb,
                inputs=[gen_dropdown, render_mode],
                outputs=[pipeline_status, pipeline_audio, pipeline_video, pipeline_info],
                concurrency_limit=1,
            )

        # ════════════════════════════════════════════════════════════════
        # Tab 5: 发布包
        # ════════════════════════════════════════════════════════════════
        with gr.Tab("5️⃣ 发布包"):
            with gr.Row():
                exp_dropdown = gr.Dropdown(
                    label="选择脚本",
                    choices=get_script_choices(),
                    interactive=True,
                )
                btn_exp_refresh = gr.Button("刷新列表")
                btn_export = gr.Button("导出发布包", variant="primary")

            exp_status = gr.Markdown()
            with gr.Row():
                exp_thumb = gr.Image(label="缩略图")
                with gr.Column():
                    exp_title = gr.Textbox(label="标题", interactive=False)
                    exp_tags = gr.Textbox(label="标签", interactive=False)
            exp_desc = gr.Textbox(label="描述", interactive=False, lines=5)
            exp_files = gr.Markdown(label="文件列表")

            btn_exp_refresh.click(fn=refresh_dropdown, outputs=[exp_dropdown])
            btn_export.click(
                fn=export_cb,
                inputs=[exp_dropdown],
                outputs=[exp_status, exp_thumb, exp_title, exp_desc, exp_tags, exp_files],
            )

        # ════════════════════════════════════════════════════════════════
        # Tab 2: 脚本
        # ════════════════════════════════════════════════════════════════
        with gr.Tab("2️⃣ 脚本"):
            gr.Markdown("输入选题标题，或从选题页选择后自动填充")
            with gr.Row():
                topic_title = gr.Textbox(label="选题标题", lines=1)
                topic_summary = gr.Textbox(label="背景信息（可选）", lines=2)
                topic_url = gr.Textbox(label="来源链接（可选）", lines=1)
            btn_gen_script = gr.Button("生成脚本", variant="primary")
            script_status = gr.Markdown()
            script_preview = gr.Markdown(label="脚本预览")
            with gr.Accordion("原始 JSON", open=False):
                script_json = gr.Code(language="json", label="JSON")
            script_id_out = gr.Textbox(label="script_id", interactive=False)

            # 从 selected_topic 自动填充标题
            selected_topic.change(
                fn=lambda s: json.loads(s).get("title", "") if s else "",
                inputs=[selected_topic],
                outputs=[topic_title],
            )
            btn_gen_script.click(
                fn=generate_script_cb,
                inputs=[topic_title, topic_summary, topic_url, selected_topic],
                outputs=[script_status, script_preview, script_json, script_id_out],
            )

        # ════════════════════════════════════════════════════════════════
        # Tab 3: 质控
        # ════════════════════════════════════════════════════════════════
        with gr.Tab("3️⃣ 质控"):
            with gr.Row():
                qc_dropdown = gr.Dropdown(
                    label="选择脚本",
                    choices=get_script_choices(),
                    interactive=True,
                )
                btn_qc_refresh = gr.Button("刷新列表")
                btn_qc = gr.Button("执行质控", variant="primary")

            qc_status = gr.Markdown()
            qc_score = gr.Markdown(label="评分详情")
            with gr.Accordion("盲预测", open=False):
                qc_pred = gr.Markdown()

            btn_qc_refresh.click(fn=refresh_dropdown, outputs=[qc_dropdown])
            btn_qc.click(
                fn=quality_check_cb,
                inputs=[qc_dropdown],
                outputs=[qc_status, qc_score, qc_pred],
            )

        # ════════════════════════════════════════════════════════════════
        # Tab 6: 复盘
        # ════════════════════════════════════════════════════════════════
        with gr.Tab("6️⃣ 复盘"):
            with gr.Row():
                retro_dropdown = gr.Dropdown(
                    label="选择脚本",
                    choices=get_script_choices(),
                    interactive=True,
                )
                btn_retro_refresh = gr.Button("刷新列表")
                btn_load_pred = gr.Button("加载预测")

            retro_pred_preview = gr.Markdown(label="预测回顾")

            with gr.Row():
                actual_views = gr.Number(label="实际播放量", value=0, minimum=0)
                actual_likes = gr.Number(label="实际点赞", value=0, minimum=0)
                actual_comments = gr.Number(label="实际评论", value=0, minimum=0)

            btn_retro = gr.Button("生成复盘报告", variant="primary")
            retro_status = gr.Markdown()
            retro_report = gr.Markdown(label="复盘报告")

            btn_retro_refresh.click(fn=refresh_dropdown, outputs=[retro_dropdown])
            btn_load_pred.click(
                fn=load_prediction_cb,
                inputs=[retro_dropdown],
                outputs=[retro_pred_preview],
            )
            btn_retro.click(
                fn=retro_cb,
                inputs=[retro_dropdown, actual_views, actual_likes, actual_comments],
                outputs=[retro_status, retro_report],
            )

        # ════════════════════════════════════════════════════════════════
        # Tab 8: 工作流
        # ════════════════════════════════════════════════════════════════
        with gr.Tab("🔄 工作流"):
            wf_state = gr.State({
                "topics": [], "selected_topic": None,
                "scripts": [], "selected_script_id": None, "quality_passed": False,
            })

            # Step 1: 拉取选题
            gr.Markdown("### ① 拉取选题")
            with gr.Row():
                wf_cat = gr.Dropdown(
                    choices=["ai-models", "ai-products", "ai-industry", "ai-tips"],
                    value="ai-models", label="分类",
                )
                wf_fetch_n = gr.Slider(5, 20, value=10, step=1, label="数量")
                btn_wf_fetch = gr.Button("拉取", variant="primary")
            wf_topics_df = gr.Dataframe(
                headers=["标题", "来源", "ID"], interactive=False, label="候选话题",
            )
            wf_topic_info = gr.Markdown("在表格中选择一行")

            # Step 2: 生成候选
            gr.Markdown("### ② 生成候选脚本")
            btn_wf_gen = gr.Button("生成 3 个候选", variant="primary", interactive=False)
            wf_gen_status = gr.Markdown("")
            wf_script_radio = gr.Radio(choices=[], label="选择候选", interactive=False)
            wf_script_preview = gr.Markdown("")

            # Step 3: 质控
            gr.Markdown("### ③ 质控打分")
            btn_wf_quality = gr.Button("运行质控", variant="primary", interactive=False)
            wf_quality_md = gr.Markdown("")
            btn_wf_go = gr.Button("确认，继续 ▶", variant="stop", visible=False)

            # Step 4: 一键生成
            gr.Markdown("### ④ 一键生成")
            wf_rm = gr.Radio(
                choices=["gradient", "footage"], value="gradient", label="渲染模式",
            )
            btn_wf_pipe = gr.Button("TTS → 渲染 → 导出", variant="stop", interactive=False)
            wf_pipe_status = gr.Markdown("")
            wf_pipe_video = gr.Video(label="视频")
            wf_export_md = gr.Markdown("")

            # ── 事件绑定 ──
            # Step 1
            btn_wf_fetch.click(
                fn=wf_fetch_cb,
                inputs=[wf_cat, wf_fetch_n],
                outputs=[wf_topics_df, wf_state, wf_topic_info],
            )
            wf_topics_df.select(
                fn=wf_select_topic,
                inputs=[wf_state],
                outputs=[wf_state, wf_topic_info, btn_wf_gen],
            )

            # Step 2
            btn_wf_gen.click(
                fn=wf_gen_cb,
                inputs=[wf_state],
                outputs=[wf_state, wf_gen_status, wf_script_radio, wf_script_preview],
            )
            wf_script_radio.change(
                fn=wf_select_script,
                inputs=[wf_script_radio, wf_state],
                outputs=[wf_state, wf_script_preview, btn_wf_quality],
            )

            # Step 3
            btn_wf_quality.click(
                fn=wf_quality_cb,
                inputs=[wf_state],
                outputs=[wf_state, wf_quality_md, btn_wf_go, btn_wf_pipe, btn_wf_quality],
            )
            btn_wf_go.click(
                fn=wf_go_cb,
                inputs=[wf_state],
                outputs=[btn_wf_pipe],
            )

            # Step 4
            btn_wf_pipe.click(
                fn=wf_pipeline_cb,
                inputs=[wf_state, wf_rm],
                outputs=[wf_pipe_status, wf_pipe_video, wf_export_md, btn_wf_pipe],
            )


# ── 启动 ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI Blogger 工作台")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--share", action="store_true")
    args = parser.parse_args()

    app.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        inbrowser=True,
        show_error=True,
        theme=gr.themes.Soft(),
        css=CSS,
    )

"""AI 博主半自动内容生产 + 质量校准系统 — CLI 入口"""

import argparse
import sys
import tomllib
import logging
from datetime import datetime
from pathlib import Path

# Windows 终端 UTF-8 输出
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

from pipeline import STEP_ORDER

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ai-blogger")

PROJECT_ROOT = Path(__file__).parent


def load_config(config_path: Path | None = None) -> dict:
    """加载 config.toml，返回配置字典。

    api.openai_api_key 为空时自动从环境变量 MIMO_API_KEY 读取。
    """
    import os
    path = config_path or (PROJECT_ROOT / "config.toml")
    if not path.exists():
        logger.error(f"配置文件不存在: {path}")
        sys.exit(1)
    with open(path, "rb") as f:
        config = tomllib.load(f)

    # API key fallback 到环境变量（优先 MIMO_API_KEY，其次 OPENAI_API_KEY）
    if not config.get("api", {}).get("openai_api_key"):
        env_key = os.environ.get("MIMO_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        if env_key:
            config.setdefault("api", {})["openai_api_key"] = env_key

    return config


def validate_config(config: dict, *, require_api: bool = False) -> None:
    """校验必填配置项，缺失则报错退出。

    require_api=True 时额外校验 API key 和模型配置（run/step/export/retro 需要）。
    """
    required_keys = [
        ("paths", "output_dir"),
        ("paths", "cheat_root"),
    ]
    if require_api:
        required_keys += [
            ("api", "openai_api_key"),
            ("api", "openai_base_url"),
            ("models", "script_model"),
            ("models", "score_model"),
            ("models", "predict_model"),
        ]
    errors = []
    for section, key in required_keys:
        if section not in config or key not in config[section]:
            errors.append(f"[{section}] {key}")
        elif not config[section][key]:
            errors.append(f"[{section}] {key} (值为空)")
    if errors:
        logger.error("以下配置项缺失或为空:\n  " + "\n  ".join(errors))
        sys.exit(1)


def ensure_directories(config: dict) -> None:
    """自动创建所有子目录。"""
    cheat_root = PROJECT_ROOT / config["paths"]["cheat_root"]
    output_dir = PROJECT_ROOT / config["paths"]["output_dir"]

    dirs = [
        cheat_root / "scripts",
        cheat_root / "predictions",
        cheat_root / "videos",
        output_dir,
        PROJECT_ROOT / "assets" / "fonts",
        PROJECT_ROOT / "assets" / "bgm",
        PROJECT_ROOT / "assets" / "footage" / "external",
        PROJECT_ROOT / "assets" / "footage" / ".cache",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def generate_run_id() -> str:
    """生成 run_id = YYYYMMDD_HHmmss"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_until_step(until: str | None) -> int:
    """将 --until 步骤名转为 STEP_ORDER 索引，返回截止索引（含）。"""
    if until is None:
        return len(STEP_ORDER) - 1  # 默认跑完 export
    if until not in STEP_ORDER:
        logger.error(f"未知步骤: {until}，可选: {', '.join(STEP_ORDER)}")
        sys.exit(1)
    return STEP_ORDER.index(until)


def _load_manifest(script_id: str, config: dict) -> dict | None:
    """加载 manifest.json，不存在返回 None。"""
    import json
    manifest_path = PROJECT_ROOT / config["paths"]["cheat_root"] / "scripts" / script_id / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return None


def _run_step(step: str, *, topic: dict | None = None, script_id: str | None = None, config: dict | None = None, force: bool = False) -> dict | None:
    """执行单步，返回结果 dict。"""
    from pipeline import topic as topic_mod
    from pipeline import script as script_mod
    from pipeline import quality as quality_mod
    from pipeline import tts as tts_mod
    from pipeline import video as video_mod
    from pipeline import export_pkg as export_mod

    if step == "topic":
        return {"topics": topic_mod.fetch_topics(cheat_root=config["paths"]["cheat_root"])}

    elif step == "script":
        if not topic:
            logger.error("script 步骤需要 --topic 或从 topic 步骤结果获取")
            return None
        return script_mod.generate_script(topic, config)

    elif step == "score":
        if not script_id:
            logger.error("score 步骤需要 --script-id")
            return None
        return quality_mod.quality_check(script_id, config)

    elif step == "predict":
        # predict 已在 quality_check 中自动完成
        logger.info("predict 已在 score 步骤中自动完成")
        return {"status": "already_done"}

    elif step == "tts":
        if not script_id:
            logger.error("tts 步骤需要 --script-id")
            return None
        return tts_mod.generate_audio(script_id, config)

    elif step == "render":
        if not script_id:
            logger.error("render 步骤需要 --script-id")
            return None
        return {"video_path": video_mod.render_video(script_id, config)}

    elif step == "export":
        if not script_id:
            logger.error("export 步骤需要 --script-id")
            return None
        return {"output_dir": export_mod.export_package(script_id, config)}

    return None


def cmd_run(args: argparse.Namespace, config: dict) -> None:
    """run 命令：完整流程或从断点恢复。"""
    run_id = generate_run_id()
    logger.info(f"Run {run_id} 启动")

    until_idx = resolve_until_step(args.until)
    steps_to_run = STEP_ORDER[: until_idx + 1]

    script_id = args.script_id
    topic = None

    if script_id:
        logger.info(f"从已有脚本恢复: {script_id}")
        manifest = _load_manifest(script_id, config)
        if manifest and not args.force:
            completed = set(manifest.get("completed_steps", []))
            steps_to_run = [s for s in steps_to_run if s not in completed]
            logger.info(f"跳过已完成: {', '.join(completed)}")
        elif args.force:
            # --force + --script-id: 跳过 topic 和 script，从 score 开始重跑
            steps_to_run = [s for s in steps_to_run if s not in {"topic", "script"}]
            logger.info("强制模式：从已有脚本重跑 score 及后续步骤")
    elif args.topic:
        logger.info(f"选题: {args.topic}")
        topic = {"title": args.topic, "snapshot_text": "", "source": "manual"}
        # 手动指定 topic 时跳过 topic 步骤（它只拉 API 候选）
        steps_to_run = [s for s in steps_to_run if s != "topic"]
    else:
        logger.error("run 命令需要 --topic 或 --script-id")
        sys.exit(1)

    for step in steps_to_run:
        logger.info(f"--- 步骤: {step} ---")
        result = _run_step(step, topic=topic, script_id=script_id, config=config, force=args.force)

        if result is None:
            logger.error(f"步骤 {step} 失败，终止")
            sys.exit(1)

        # 从 script 步骤结果获取 script_id
        if step == "script" and "script_id" in result:
            script_id = result["script_id"]
            topic = None  # 后续步骤不再需要 topic

        logger.info(f"步骤 {step} 完成")

    logger.info(f"Run {run_id} 完成")


def cmd_step(args: argparse.Namespace, config: dict) -> None:
    """step 命令：单步执行。"""
    step = args.step_name
    if step not in STEP_ORDER:
        logger.error(f"未知步骤: {step}，可选: {', '.join(STEP_ORDER)}")
        sys.exit(1)

    logger.info(f"执行单步: {step}")

    topic = None
    if args.topic:
        topic = {"title": args.topic, "snapshot_text": "", "source": "manual"}

    result = _run_step(
        step,
        topic=topic,
        script_id=args.script_id,
        config=config,
        force=getattr(args, "force", False),
    )

    if result is None:
        logger.error(f"步骤 {step} 失败")
        sys.exit(1)

    # 输出结果
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    logger.info(f"步骤 {step} 完成")


def cmd_export(args: argparse.Namespace, config: dict) -> None:
    """export 命令：导出发布包。"""
    if not args.script_id:
        logger.error("export 需要 --script-id")
        sys.exit(1)
    from pipeline import validate_script_id
    validate_script_id(args.script_id)
    logger.info(f"导出发布包: {args.script_id}")
    from pipeline.export_pkg import export_package
    result = export_package(args.script_id, config)
    logger.info(f"已导出到: {result}")


def cmd_retro(args: argparse.Namespace, config: dict) -> None:
    """retro 命令：复盘。"""
    if not args.script_id and not args.title:
        logger.error("retro 需要 --script-id 或 --title")
        sys.exit(1)
    if args.script_id:
        from pipeline import validate_script_id
        validate_script_id(args.script_id)
    actual = {
        "views": args.views,
        "likes": args.likes,
        "comments": args.comments,
    }
    logger.info(f"复盘: {args.script_id or args.title}")
    from adapter.cheat import retro
    # TODO: --title 模糊搜索支持
    result = retro(args.script_id, actual, config)
    logger.info(f"复盘报告: {result['report_path']}")


def cmd_status(args: argparse.Namespace, config: dict) -> None:
    """status 命令：显示进度。"""
    cheat_root = PROJECT_ROOT / config["paths"]["cheat_root"]
    scripts_dir = cheat_root / "scripts"

    if not scripts_dir.exists():
        logger.info("尚无任何脚本")
        return

    manifests = list(scripts_dir.glob("*/manifest.json"))
    if not manifests:
        logger.info("尚无任何脚本")
        return

    print(f"\n{'='*60}")
    print(f"脚本总数: {len(manifests)}")
    print(f"{'='*60}")

    for mp in sorted(manifests):
        import json
        with open(mp, "r", encoding="utf-8") as f:
            m = json.load(f)
        sid = m.get("script_id", "?")
        title = m.get("title", "?")
        completed = m.get("completed_steps", [])
        current = m.get("current_step", "?")
        print(f"\n  script_id: {sid}")
        print(f"  title: {title}")
        print(f"  completed: {' → '.join(completed) if completed else '(none)'}")
        print(f"  current: {current}")


def cmd_stock(args: argparse.Namespace, config: dict) -> None:
    """stock 命令：外部素材管理。"""
    if args.stock_action == "fill":
        from pipeline.stock import fill_footage_for_script
        result = fill_footage_for_script(
            args.script_id, config,
            provider=getattr(args, "provider", None),
            limit=getattr(args, "limit", None),
        )
        mk = result["missing_keywords"]
        dl = result["downloaded"]
        sc = result["skipped_cached"]
        fl = result["failed"]
        print(f"\n缺失关键词: {len(mk)}")
        if not mk:
            print("无需补充素材")
        else:
            print(f"下载成功: {len(dl)}")
            if sc:
                print(f"跳过缓存: {len(sc)}")
            if fl:
                print(f"失败: {len(fl)}")
                for f in fl:
                    print(f"  - {f['keyword']} ({f['provider']}): {f['error']}")
        print()


def cmd_footage(args: argparse.Namespace, config: dict) -> None:
    """footage 命令：素材池管理。"""
    from pipeline.footage import (
        load_blocklist, add_to_blocklist, remove_from_blocklist, list_blocklist,
    )
    footage_dir = PROJECT_ROOT / config["paths"].get("footage_dir", "assets/footage")

    if args.footage_action == "disable":
        path = args.path
        if not Path(path).exists():
            logger.error(f"文件不存在: {path}")
            sys.exit(1)
        add_to_blocklist(footage_dir, path, reason=args.reason or "")
        print(f"已禁用: {path}")

    elif args.footage_action == "enable":
        path = args.path
        removed = remove_from_blocklist(footage_dir, path)
        if removed:
            print(f"已启用: {path}")
        else:
            print(f"未在黑名单中找到: {path}")

    elif args.footage_action == "disabled":
        items = list_blocklist(footage_dir)
        if not items:
            print("黑名单为空")
        else:
            print(f"\n黑名单 ({len(items)} 项):")
            for item in items:
                reason = f" — {item['reason']}" if item.get("reason") else ""
                print(f"  {item['path']}{reason}")
            print()

    elif args.footage_action == "stats":
        from pipeline.footage_stats import load_all_reports, aggregate_stats, per_script_stats, format_stats_markdown
        dist_dir = PROJECT_ROOT / config["paths"].get("output_dir", "dist")
        reports = load_all_reports(dist_dir)
        if not reports:
            print("暂无 render_report.json 数据")
            return
        if args.script_id:
            reports = [r for r in reports if r["script_id"] == args.script_id]
            if not reports:
                print(f"未找到 script_id={args.script_id} 的渲染报告")
                return
        agg = aggregate_stats(reports)
        ps = per_script_stats(reports)
        print(format_stats_markdown(agg, ps))

    elif args.footage_action == "missed":
        from pipeline.footage_stats import load_all_reports, collect_missed_keywords, format_missed_keywords_markdown
        dist_dir = PROJECT_ROOT / config["paths"].get("output_dir", "dist")
        reports = load_all_reports(dist_dir)
        if not reports:
            print("暂无 render_report.json 数据")
            return
        missed = collect_missed_keywords(reports)
        print(format_missed_keywords_markdown(missed, top_n=args.top))


def main():
    parser = argparse.ArgumentParser(
        prog="ai-blogger",
        description="AI 博主半自动内容生产 + 质量校准系统",
    )
    parser.add_argument("--config", type=Path, default=None, help="配置文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = subparsers.add_parser("run", help="完整流程或从断点恢复")
    p_run.add_argument("--topic", type=str, help="选题标题")
    p_run.add_argument("--script-id", type=str, help="从已有脚本恢复")
    p_run.add_argument("--until", type=str, help="执行到指定步骤为止")
    p_run.add_argument("--force", action="store_true", help="忽略 checkpoint，从头重跑")
    p_run.set_defaults(func=cmd_run)

    # step
    p_step = subparsers.add_parser("step", help="单步执行")
    p_step.add_argument("step_name", type=str, help="步骤名称")
    p_step.add_argument("--topic", type=str, help="选题标题（script 步骤用）")
    p_step.add_argument("--category", type=str, default="ai-models", help="选题分类")
    p_step.add_argument("--take", type=int, default=10, help="选题数量")
    p_step.add_argument("--script-id", type=str, help="脚本 ID")
    p_step.add_argument("--force", action="store_true", help="强制重跑")
    p_step.set_defaults(func=cmd_step)

    # export
    p_export = subparsers.add_parser("export", help="导出发布包")
    p_export.add_argument("--script-id", type=str, required=True, help="脚本 ID")
    p_export.set_defaults(func=cmd_export)

    # retro
    p_retro = subparsers.add_parser("retro", help="复盘")
    p_retro.add_argument("--script-id", type=str, help="脚本 ID")
    p_retro.add_argument("--title", type=str, help="标题模糊搜索")
    p_retro.add_argument("--views", type=int, default=0, help="实际播放量")
    p_retro.add_argument("--likes", type=int, default=0, help="实际点赞数")
    p_retro.add_argument("--comments", type=int, default=0, help="实际评论数")
    p_retro.set_defaults(func=cmd_retro)

    # status
    p_status = subparsers.add_parser("status", help="显示进度")
    p_status.set_defaults(func=cmd_status)

    # stock
    p_stock = subparsers.add_parser("stock", help="外部素材管理")
    stock_sub = p_stock.add_subparsers(dest="stock_action", required=True)
    p_fill = stock_sub.add_parser("fill", help="补充外部素材")
    p_fill.add_argument("--script-id", type=str, required=True, help="脚本 ID")
    p_fill.add_argument("--provider", type=str, choices=["pexels", "pixabay"], help="指定 provider")
    p_fill.add_argument("--limit", type=int, help="每个关键词下载数量上限")
    p_fill.set_defaults(func=cmd_stock)

    # footage
    p_footage = subparsers.add_parser("footage", help="素材池管理")
    footage_sub = p_footage.add_subparsers(dest="footage_action", required=True)
    p_disable = footage_sub.add_parser("disable", help="禁用素材")
    p_disable.add_argument("path", type=str, help="素材文件路径")
    p_disable.add_argument("--reason", type=str, default="", help="禁用原因")
    p_disable.set_defaults(func=cmd_footage)
    p_enable = footage_sub.add_parser("enable", help="启用素材")
    p_enable.add_argument("path", type=str, help="素材文件路径")
    p_enable.set_defaults(func=cmd_footage)
    p_disabled = footage_sub.add_parser("disabled", help="查看黑名单")
    p_disabled.set_defaults(func=cmd_footage)
    p_stats = footage_sub.add_parser("stats", help="素材命中率统计")
    p_stats.add_argument("--script-id", type=str, help="指定脚本 ID")
    p_stats.set_defaults(func=cmd_footage)
    p_missed = footage_sub.add_parser("missed", help="未命中关键词 Top N")
    p_missed.add_argument("--top", type=int, default=20, help="显示前 N 个")
    p_missed.set_defaults(func=cmd_footage)

    args = parser.parse_args()
    config = load_config(args.config)
    validate_config(config, require_api=(args.command not in ("status", "stock", "footage")))
    ensure_directories(config)

    args.func(args, config)


if __name__ == "__main__":
    main()

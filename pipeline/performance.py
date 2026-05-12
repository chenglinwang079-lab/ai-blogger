"""历史表现数据库：per-script performance.json CRUD（append-only）"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pipeline import validate_script_id

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))


def _perf_path(script_id: str, config: dict) -> Path:
    return Path(config["paths"]["cheat_root"]) / "scripts" / script_id / "performance.json"


def _now_iso() -> str:
    return datetime.now(_CST).isoformat(timespec="seconds")


def load_performance(script_id: str, config: dict) -> dict | None:
    """读取 performance.json，不存在返回 None。"""
    validate_script_id(script_id)
    path = _perf_path(script_id, config)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_performance(data: dict, config: dict) -> None:
    """原子写入 performance.json。"""
    path = _perf_path(data["script_id"], config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def record_performance(
    script_id: str,
    config: dict,
    *,
    platform: str,
    views: int = 0,
    likes: int = 0,
    comments: int = 0,
    shares: int = 0,
    favorites: int = 0,
    source: str = "manual",
    captured_at: str | None = None,
) -> dict:
    """追加一条表现记录（按 captured_at 升序插入）。返回完整 performance dict。"""
    validate_script_id(script_id)
    data = load_performance(script_id, config)
    if data is None:
        data = {"script_id": script_id, "records": [], "updated_at": _now_iso()}

    record = {
        "platform": platform,
        "captured_at": captured_at or _now_iso(),
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "favorites": favorites,
        "source": source,
    }

    # 按 captured_at 升序插入
    records = data["records"]
    insert_idx = len(records)
    for i, r in enumerate(records):
        if r.get("captured_at", "") > record["captured_at"]:
            insert_idx = i
            break
    records.insert(insert_idx, record)

    data["updated_at"] = _now_iso()
    save_performance(data, config)
    logger.info(f"performance 记录已追加: {script_id} platform={platform} views={views}")
    return data


def get_latest(script_id: str, config: dict, *, platform: str | None = None) -> dict:
    """返回 {platform: latest_record}。指定平台也返回 {platform: record}。无数据返回 {}。"""
    data = load_performance(script_id, config)
    if not data or not data.get("records"):
        return {}

    # 按 platform 分组，取每组最后一条（records 已按 captured_at 升序）
    by_platform: dict[str, dict] = {}
    for r in data["records"]:
        by_platform[r["platform"]] = r

    if platform:
        rec = by_platform.get(platform)
        return {platform: rec} if rec else {}
    return by_platform


def list_all_performance(config: dict) -> list[dict]:
    """扫描所有 performance.json，返回 [{script_id, title, latest}]。
    title 从 manifest.json 补齐。"""
    scripts_dir = Path(config["paths"]["cheat_root"]) / "scripts"
    if not scripts_dir.exists():
        return []

    results = []
    for perf_file in sorted(scripts_dir.glob("*/performance.json")):
        data = json.loads(perf_file.read_text(encoding="utf-8"))
        sid = data.get("script_id", "?")

        # title from manifest
        manifest_path = perf_file.parent / "manifest.json"
        title = ""
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            title = manifest.get("title", "")

        # latest per platform
        latest: dict[str, dict] = {}
        for r in data.get("records", []):
            latest[r["platform"]] = r

        results.append({"script_id": sid, "title": title, "latest": latest})

    return results

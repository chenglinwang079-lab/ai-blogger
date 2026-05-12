"""发布队列管理：per-script publish.json CRUD"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pipeline import validate_script_id

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))

_VALID_STATUSES = {"draft", "ready", "scheduled", "published", "failed"}


def _publish_path(script_id: str, config: dict) -> Path:
    """返回 publish.json 的绝对路径。"""
    return Path(config["paths"]["cheat_root"]) / "scripts" / script_id / "publish.json"


def _now_iso() -> str:
    return datetime.now(_CST).isoformat(timespec="seconds")


def _new_publish(script_id: str, status: str = "ready") -> dict:
    """创建空的 publish.json 结构。"""
    now = _now_iso()
    return {
        "script_id": script_id,
        "status": status,
        "exported_at": now,
        "updated_at": now,
        "platforms": {},
    }


def _new_platform(status: str = "ready") -> dict:
    return {
        "status": status,
        "scheduled_at": None,
        "published_at": None,
        "post_url": "",
        "error": "",
    }


def load_publish(script_id: str, config: dict) -> dict | None:
    """读取 publish.json，不存在返回 None。"""
    validate_script_id(script_id)
    path = _publish_path(script_id, config)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_publish(data: dict, config: dict) -> None:
    """写入 publish.json（原子写：tmp + os.replace）。"""
    script_id = data["script_id"]
    path = _publish_path(script_id, config)
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


def init_or_update_status(script_id: str, config: dict, *, status: str = "ready") -> dict:
    """export 完成后调用。按 auto-set 规则更新状态。

    规则：
    - publish.json 不存在 → 创建，status=ready
    - 当前 status 为 draft/failed → 更新为 ready
    - 当前 status 为 scheduled/published → 不覆盖
    """
    validate_script_id(script_id)
    data = load_publish(script_id, config)

    if data is None:
        data = _new_publish(script_id, status)
        save_publish(data, config)
        logger.info(f"publish.json 已创建: {script_id} (status={status})")
        return data

    current = data.get("status", "draft")
    if current in ("draft", "failed"):
        data["status"] = status
        data["updated_at"] = _now_iso()
        save_publish(data, config)
        logger.info(f"publish.json 状态更新: {script_id} {current} → {status}")
    else:
        logger.debug(f"publish.json 状态保持不变: {script_id} (status={current})")

    return data


def update_publish_status(
    script_id: str,
    config: dict,
    *,
    status: str,
    platform: str | None = None,
    post_url: str | None = None,
    published_at: str | None = None,
    scheduled_at: str | None = None,
    error: str | None = None,
) -> dict:
    """手动更新发布状态。

    可同时更新 platform 级别字段。
    自动时间戳规则：
    - published 且未传 published_at → 自动填当前时间
    - scheduled 且未传 scheduled_at → 保留 null
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"无效状态: {status}，可选: {', '.join(sorted(_VALID_STATUSES))}")

    validate_script_id(script_id)
    data = load_publish(script_id, config)
    if data is None:
        data = _new_publish(script_id, status)
    else:
        data["status"] = status

    data["updated_at"] = _now_iso()

    if platform:
        plat = data["platforms"].get(platform, _new_platform(status))
        plat["status"] = status
        if post_url is not None:
            plat["post_url"] = post_url
        if published_at is not None:
            plat["published_at"] = published_at
        elif status == "published" and not plat.get("published_at"):
            plat["published_at"] = _now_iso()
        if scheduled_at is not None:
            plat["scheduled_at"] = scheduled_at
        elif status == "scheduled":
            # 未传 scheduled_at 时保留原值（可能为 null）
            pass
        if error is not None:
            plat["error"] = error
        data["platforms"][platform] = plat

    save_publish(data, config)
    logger.info(f"publish.json 已更新: {script_id} status={status}")
    return data


def list_publish_queue(config: dict, *, status: str | None = None) -> list[dict]:
    """扫描 cheat/scripts/*/publish.json，聚合为列表。可按 status 筛选。

    返回列表每项包含 publish.json 原始字段 + 从 manifest.json 补齐的 title。
    """
    scripts_dir = Path(config["paths"]["cheat_root"]) / "scripts"
    if not scripts_dir.exists():
        return []

    results = []
    for publish_file in sorted(scripts_dir.glob("*/publish.json")):
        data = json.loads(publish_file.read_text(encoding="utf-8"))
        # 从 manifest.json 补齐 title
        manifest_path = publish_file.parent / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["title"] = manifest.get("title", "")
        else:
            data["title"] = ""

        if status and data.get("status") != status:
            continue
        results.append(data)

    return results

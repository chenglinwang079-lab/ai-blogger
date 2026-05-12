"""脚本风格 catalog：加载 + 查找 + 列表。"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CATALOG_PATHS = [
    _PROJECT_ROOT / "assets" / "styles" / "style_catalog.json",
    _PROJECT_ROOT / "assets" / "styles" / "style_catalog.example.json",
]


def load_style_catalog() -> list[dict]:
    """优先读 style_catalog.json，不存在则读 .example.json，过滤 enabled=true。"""
    for path in _CATALOG_PATHS:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return [e for e in data if e.get("enabled", True)]
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"风格 catalog 解析失败 {path}: {e}")
                return []
    return []


def resolve_style(style_id: str) -> dict | None:
    """按 id 查找风格，未找到返回 None。"""
    for entry in load_style_catalog():
        if entry.get("id") == style_id:
            return entry
    return None


def list_styles() -> list[dict]:
    """返回所有已启用的风格条目。"""
    return load_style_catalog()

"""选题：AI HOT API"""

import hashlib
import re
from urllib.parse import urlparse
from datetime import datetime, timezone
from pathlib import Path

import requests


def normalize_title(title: str) -> str:
    return re.sub(r'\s+', ' ', title.strip()).lower()


def normalize_url_path(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return parsed.path.rstrip('/')


def make_candidate_id(source: str, title: str, url: str = "") -> str:
    raw = f"{source}|{normalize_title(title)}|{normalize_url_path(url)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def fetch_topics(category: str = "ai-models", take: int = 10, cheat_root: str = "cheat") -> list[dict]:
    """AI HOT API → 候选列表（每项含 candidate_id）。

    返回: [{candidate_id, title, source: "trend:aihot", snapshot_text, snapshot_at, url}]
    写入 cheat/candidates.md 前按 candidate_id 去重
    API 失败返回空列表 + stderr 警告
    """
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    url = f"https://aihot.virxact.com/api/public/items?mode=selected&category={category}&take={take}"

    try:
        resp = requests.get(url, headers={"User-Agent": ua}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError, KeyError) as e:
        import sys
        print(f"AI HOT API 调用失败: {e}", file=sys.stderr)
        return []

    results = []
    for item in data.get("items", []):
        title = item.get("title", "")
        source_url = item.get("url", "")
        cid = make_candidate_id("trend:aihot", title, source_url)
        results.append({
            "candidate_id": cid,
            "title": title,
            "source": "trend:aihot",
            "snapshot_text": item.get("summary", ""),
            "snapshot_at": item.get("publishedAt", datetime.now(timezone.utc).isoformat()),
            "url": source_url,
        })

    # 写入 cheat/candidates.md，按 candidate_id 去重
    _write_candidates(results, cheat_root=cheat_root)

    return results


def _write_candidates(new_items: list[dict], cheat_root: str = "cheat") -> None:
    """将候选选题追加到 cheat/candidates.md，按 candidate_id 去重。"""
    from pathlib import Path

    md_path = Path(cheat_root) / "candidates.md"

    # 读取已有 candidate_id
    existing_ids = set()
    existing_content = ""
    if md_path.exists():
        existing_content = md_path.read_text(encoding="utf-8")
        for line in existing_content.split("\n"):
            if line.startswith("<!-- cid:") and line.endswith("-->"):
                cid = line[9:-4].strip()
                existing_ids.add(cid)

    # 过滤重复
    new_unique = [item for item in new_items if item["candidate_id"] not in existing_ids]
    if not new_unique:
        return

    # 追加写入
    lines = []
    if not existing_content:
        lines.append("# 候选选题池\n")

    for item in new_unique:
        lines.append(f"<!-- cid:{item['candidate_id']} -->")
        lines.append(f"## {item['title']}")
        lines.append(f"- 来源: {item['source']}")
        lines.append(f"- 时间: {item['snapshot_at']}")
        lines.append(f"- 链接: {item['url']}")
        if item.get("snapshot_text"):
            lines.append(f"\n{item['snapshot_text'][:200]}")
        lines.append("")

    md_path.parent.mkdir(parents=True, exist_ok=True)
    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

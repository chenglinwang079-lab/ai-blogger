"""本地素材池索引和匹配。"""

from pathlib import Path
import json
import random
import re
import time

from pipeline.keyword_aliases import ALIASES as KEYWORD_ALIASES

_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi"}

# ── Blocklist ────────────────────────────────────────────────────────

def _blocklist_path(footage_dir: Path) -> Path:
    return footage_dir / "blocklist.json"


def load_blocklist(footage_dir: Path) -> set[str]:
    """返回被禁用素材的 resolved 路径集合。"""
    bp = _blocklist_path(footage_dir)
    if not bp.exists():
        return set()
    try:
        data = json.loads(bp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    resolved = set()
    base = footage_dir.parent.parent  # project root
    for item in data.get("disabled", []):
        p = item.get("path", "")
        if p:
            resolved.add(str((base / p).resolve()))
    return resolved


def add_to_blocklist(footage_dir: Path, path: str, reason: str = "") -> None:
    """添加素材到黑名单。JSON 存相对路径（POSIX /）。"""
    bp = _blocklist_path(footage_dir)
    data = {"disabled": []}
    if bp.exists():
        try:
            data = json.loads(bp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    base = footage_dir.parent.parent
    rel = Path(path).resolve().relative_to(base.resolve())
    rel_posix = rel.as_posix()

    # 去重
    for item in data["disabled"]:
        if item["path"] == rel_posix:
            return

    data["disabled"].append({
        "path": rel_posix,
        "reason": reason,
        "disabled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_from_blocklist(footage_dir: Path, path: str) -> bool:
    """从黑名单移除素材，返回是否找到。"""
    bp = _blocklist_path(footage_dir)
    if not bp.exists():
        return False
    try:
        data = json.loads(bp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    base = footage_dir.parent.parent
    rel = Path(path).resolve().relative_to(base.resolve())
    rel_posix = rel.as_posix()

    before = len(data["disabled"])
    data["disabled"] = [d for d in data["disabled"] if d["path"] != rel_posix]
    if len(data["disabled"]) == before:
        return False

    bp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def list_blocklist(footage_dir: Path) -> list[dict]:
    """返回黑名单条目列表。"""
    bp = _blocklist_path(footage_dir)
    if not bp.exists():
        return []
    try:
        data = json.loads(bp.read_text(encoding="utf-8"))
        return data.get("disabled", [])
    except (json.JSONDecodeError, OSError):
        return []


# ── 索引与匹配 ──────────────────────────────────────────────────────

def index_footage(footage_dir: Path, exclude: set[str] | None = None) -> list[dict]:
    """扫描素材目录，返回 [{path, tags: set[str]}]。
    exclude: resolved 路径集合，匹配到的素材会被过滤。
    """
    entries = []
    if not footage_dir.exists():
        return entries
    exclude = exclude or set()
    for f in footage_dir.rglob("*"):
        if f.suffix.lower() in _VIDEO_EXTS:
            resolved = str(f.resolve())
            if resolved in exclude:
                continue
            tags = set()
            rel = f.relative_to(footage_dir)
            for part in rel.parent.parts:
                tags.add(part.lower())
            stem = f.stem.lower()
            for token in re.split(r'[-_\s]+', stem):
                if len(token) >= 2:
                    tags.add(token)
            entries.append({"path": str(f), "tags": tags})
    return entries


def match_footage(keyword: str, index: list[dict]) -> str | None:
    """用 keyword 匹配素材，返回最佳匹配的文件路径或 None。"""
    if not keyword or not index:
        return None

    # 构建搜索 tokens
    tokens = set()
    for seg in re.split(r'[，。！？、\s+]+', keyword):
        seg = seg.strip().lower()
        if len(seg) >= 2:
            tokens.add(seg)
    clean = re.sub(r'[，。！？、+]', '', keyword).strip()
    if clean:
        tokens.add(clean.lower())

    # 中文别名展开
    expanded = set(tokens)
    for tok in tokens:
        for cn, en_set in KEYWORD_ALIASES.items():
            if cn in tok:
                expanded.update(en_set)
            for en in en_set:
                if en in tok:
                    expanded.add(cn)
    tokens = expanded

    # 匹配评分
    best_score = 0
    best_path = None
    for entry in index:
        score = 0
        for tag in entry["tags"]:
            for tok in tokens:
                if tag in tok or tok in tag:
                    score += 1
        if score > best_score:
            best_score = score
            best_path = entry["path"]

    if best_path:
        return best_path

    # fallback: abstract/ 目录下的素材
    abstracts = [e for e in index if "abstract" in e["tags"]]
    if abstracts:
        return random.choice(abstracts)["path"]

    return None


def match_footage_with_reason(keyword: str, index: list[dict]) -> dict:
    """返回 {"path": str|None, "reason": "matched"|"abstract_fallback"|"none"}。"""
    path = match_footage(keyword, index)
    if path is None:
        return {"path": None, "reason": "none"}
    parts = {p.lower() for p in Path(path).parts}
    reason = "abstract_fallback" if "abstract" in parts else "matched"
    return {"path": path, "reason": reason}

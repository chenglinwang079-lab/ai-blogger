"""本地素材池索引和匹配。"""

from pathlib import Path
import random
import re

from pipeline.keyword_aliases import ALIASES as KEYWORD_ALIASES

_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi"}


def index_footage(footage_dir: Path) -> list[dict]:
    """扫描素材目录，返回 [{path, tags: set[str]}]。"""
    entries = []
    if not footage_dir.exists():
        return entries
    for f in footage_dir.rglob("*"):
        if f.suffix.lower() in _VIDEO_EXTS:
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

"""pipeline/footage.py — _score_entry, match_footage_with_reason"""

import pytest
from pipeline.footage import _score_entry, match_footage_with_reason


# ── _score_entry ─────────────────────────────────────────────────────

class TestScoreEntry:
    def test_exact_match(self):
        entry = {"tags": {"technology", "abstract"}, "path": "/a.mp4"}
        score, best_tag = _score_entry(entry, {"technology"})
        assert score == 3
        assert best_tag == "technology"

    def test_substring_match(self):
        entry = {"tags": {"tech"}, "path": "/a.mp4"}
        score, best_tag = _score_entry(entry, {"technology"})
        # "tech" in "technology" → +1
        assert score == 1
        assert best_tag == "tech"

    def test_no_match(self):
        entry = {"tags": {"nature", "sky"}, "path": "/a.mp4"}
        score, best_tag = _score_entry(entry, {"technology"})
        assert score == 0
        assert best_tag is None

    def test_empty_tags(self):
        entry = {"tags": set(), "path": "/a.mp4"}
        score, best_tag = _score_entry(entry, {"technology"})
        assert score == 0
        assert best_tag is None

    def test_hex_hash_tag_skipped(self):
        """8 位十六进制 tag（文件名 hash）应被跳过。"""
        entry = {"tags": {"a1b2c3d4", "technology"}, "path": "/a.mp4"}
        score, best_tag = _score_entry(entry, {"a1b2c3d4"})
        # a1b2c3d4 是 hex hash → 跳过
        assert score == 0
        assert best_tag is None

    def test_hex_hash_case_insensitive_skip(self):
        entry = {"tags": {"A1B2C3D4"}, "path": "/a.mp4"}
        score, _ = _score_entry(entry, {"a1b2c3d4"})
        assert score == 0

    def test_mixed_match(self):
        """精确 + 子串同时命中，最高权重胜出。"""
        entry = {"tags": {"tech", "technology"}, "path": "/a.mp4"}
        score, best_tag = _score_entry(entry, {"technology"})
        # technology==technology → +3, tech in technology → +1, total=4
        assert score == 4
        assert best_tag == "technology"  # weight 3 > weight 1

    def test_empty_tokens(self):
        entry = {"tags": {"technology"}, "path": "/a.mp4"}
        score, best_tag = _score_entry(entry, set())
        assert score == 0
        assert best_tag is None


# ── match_footage_with_reason ────────────────────────────────────────

class TestMatchFootageWithReason:
    def _make_index(self, entries):
        """构建最小 index 格式。"""
        return [
            {"path": e["path"], "tags": e.get("tags", set())}
            for e in entries
        ]

    def test_empty_keyword(self):
        result = match_footage_with_reason("", [])
        assert result["reason"] == "none"
        assert result["path"] is None

    def test_empty_index(self):
        result = match_footage_with_reason("technology", [])
        assert result["reason"] == "none"
        assert result["path"] is None

    def test_normal_match(self):
        idx = self._make_index([
            {"path": "/clips/technology/a.mp4", "tags": {"technology"}},
        ])
        result = match_footage_with_reason("technology", idx)
        assert result["reason"] == "matched"
        assert result["reused"] is False
        assert result["path"] is not None

    def test_abstract_fallback(self):
        """无直接匹配但有 abstract 素材 → abstract_fallback。"""
        idx = self._make_index([
            {"path": "/clips/abstract/b.mp4", "tags": {"abstract"}},
        ])
        result = match_footage_with_reason("nonexistent", idx)
        assert result["reason"] == "abstract_fallback"
        assert result["path"] is not None

    def test_exclude_paths_round2(self):
        """所有匹配路径在 exclude_paths 中 → round 2 复用。"""
        idx = self._make_index([
            {"path": "/clips/tech/a.mp4", "tags": {"technology"}},
        ])
        result = match_footage_with_reason(
            "technology", idx,
            exclude_paths={"/clips/tech/a.mp4"},
        )
        assert result["reason"] == "matched"
        assert result["reused"] is True

    def test_exclude_empty_set_still_matches(self):
        """exclude_paths 为空集 → round 1 过滤掉 0 个，正常匹配。"""
        idx = self._make_index([
            {"path": "/clips/tech/a.mp4", "tags": {"technology"}},
        ])
        result = match_footage_with_reason(
            "technology", idx, exclude_paths=set(),
        )
        assert result["reason"] == "matched"
        assert result["reused"] is False

    def test_exclude_none_skips_round1(self):
        """exclude_paths=None → 跳过 round 1，直接 round 2 搜索全量。"""
        idx = self._make_index([
            {"path": "/clips/tech/a.mp4", "tags": {"technology"}},
        ])
        result = match_footage_with_reason("technology", idx, exclude_paths=None)
        # exclude=None 时 round 1 跳过（不筛选），round 2 搜全量 → 匹配
        assert result["reason"] == "matched"

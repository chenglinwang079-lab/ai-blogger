"""pipeline/video.py — 纯计算函数 + 脚本解析函数"""

import pytest
from pipeline.video import (
    _compute_quality_score,
    _highlight_keyword_segments,
    _normalize,
    _parse_script_segments,
    _extract_keywords_from_md,
)


# ── _compute_quality_score ───────────────────────────────────────────

class TestComputeQualityScore:
    def test_total_zero(self):
        assert _compute_quality_score(0, 0, 0) == 0.0

    def test_all_matched(self):
        assert _compute_quality_score(5, 0, 5) == 100.0

    def test_all_abstract_fallback(self):
        assert _compute_quality_score(0, 5, 5) == 40.0

    def test_mixed(self):
        # (2*100 + 1*40) / 5 = 240/5 = 48.0
        assert _compute_quality_score(2, 1, 5) == 48.0

    def test_one_matched_one_other(self):
        # (1*100 + 0*40) / 2 = 50.0
        assert _compute_quality_score(1, 0, 2) == 50.0

    def test_negative_total(self):
        """当前实现 total<=0 返回 0.0。"""
        assert _compute_quality_score(0, 0, -1) == 0.0


# ── _highlight_keyword_segments ──────────────────────────────────────

class TestHighlightKeywordSegments:
    def test_empty_keyword(self):
        result = _highlight_keyword_segments("hello world", "")
        assert result == [("hello world", False)]

    def test_keyword_not_found(self):
        result = _highlight_keyword_segments("hello world", "xyz")
        assert result == [("hello world", False)]

    def test_keyword_at_start(self):
        result = _highlight_keyword_segments("hello world", "hello")
        assert len(result) == 3
        assert result[0] == ("", False)  # 前缀为空
        assert result[1] == ("hello", True)
        assert result[2] == (" world", False)

    def test_keyword_at_end(self):
        result = _highlight_keyword_segments("hello world", "world")
        assert len(result) == 3
        assert result[0] == ("hello ", False)
        assert result[1] == ("world", True)
        assert result[2] == ("", False)  # 后缀为空

    def test_case_insensitive_match_returns_original_case(self):
        result = _highlight_keyword_segments("Hello World", "hello")
        assert result[1] == ("Hello", True)  # 原文大小写

    def test_only_first_occurrence_highlighted(self):
        result = _highlight_keyword_segments("abc abc abc", "abc")
        # 只高亮第一个
        assert result[1] == ("abc", True)
        assert result[2] == (" abc abc", False)

    def test_keyword_equals_text(self):
        result = _highlight_keyword_segments("hello", "hello")
        assert result == [("", False), ("hello", True), ("", False)]


# ── _normalize ───────────────────────────────────────────────────────

class TestNormalize:
    def test_bold_markers_removed(self):
        assert _normalize("**bold**") == "bold"

    def test_heading_markers_removed(self):
        assert _normalize("## 标题") == "标题"

    def test_whitespace_stripped(self):
        assert _normalize("  hello  world  ") == "helloworld"

    def test_empty_string(self):
        assert _normalize("") == ""

    def test_pure_whitespace(self):
        assert _normalize("   \t\n  ") == ""

    def test_mixed_cjk_ascii_markdown(self):
        result = _normalize("**人工智能** AI")
        assert "人" in result
        assert "AI" in result or "ai" in result.lower()
        assert "*" not in result


# ── _parse_script_segments ───────────────────────────────────────────

class TestParseScriptSegments:
    def test_standard_markdown(self):
        md = """## 段1
**画面**：科技实验室

这是第一段内容。

## 段2
**画面**：城市夜景

这是第二段内容。"""
        result = _parse_script_segments(md)
        assert len(result) == 2
        assert result[0][1] == "科技实验室"
        assert result[1][1] == "城市夜景"
        assert "第一段" in result[0][0]

    def test_no_visual_keyword(self):
        md = """## 段1

这是内容。"""
        result = _parse_script_segments(md)
        assert len(result) == 1
        assert result[0][1] == ""  # 无 keyword

    def test_empty_markdown(self):
        assert _parse_script_segments("") == []

    def test_keyword_only_no_body_not_output(self):
        """当前实现：keyword 有但 body 为空 → seg_text="" → if seg_text 为 False → 不输出。"""
        md = """## 段1
**画面**：科技实验室

## 段2
**画面**：城市夜景

有内容的段。"""
        result = _parse_script_segments(md)
        # 段1 只有 keyword 没有 body → 不输出
        # 段2 有 keyword + body → 输出
        assert len(result) == 1
        assert result[0][1] == "城市夜景"

    def test_body_without_keyword(self):
        md = """## 段1

这是没有画面指示的内容。"""
        result = _parse_script_segments(md)
        assert len(result) == 1
        assert result[0][1] == ""
        assert "没有画面" in result[0][0]

    def test_h3_heading_supported(self):
        """H3 段标题也应触发新段。"""
        md = """### 段1
**画面**：测试

内容。"""
        result = _parse_script_segments(md)
        assert len(result) == 1
        assert result[0][1] == "测试"


# ── _extract_keywords_from_md ────────────────────────────────────────

class TestExtractKeywordsFromMd:
    def test_multiple_keywords(self):
        md = """## 段1
**画面**：科技实验室

## 段2
**画面**：城市夜景"""
        result = _extract_keywords_from_md(md)
        assert result == ["科技实验室", "城市夜景"]

    def test_no_matches(self):
        md = "普通文本，没有画面指示。"
        assert _extract_keywords_from_md(md) == []

    def test_empty_input(self):
        assert _extract_keywords_from_md("") == []

    def test_chinese_colon(self):
        """中文冒号也应匹配。"""
        md = "**画面**：科技"
        result = _extract_keywords_from_md(md)
        assert result == ["科技"]

    def test_no_colon(self):
        """无冒号时也应匹配（冒号可选）。"""
        md = "**画面** 科技"
        result = _extract_keywords_from_md(md)
        assert result == ["科技"]

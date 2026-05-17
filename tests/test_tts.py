"""pipeline/tts.py — _split_text"""

import pytest
from pipeline.tts import _split_text


class TestSplitText:
    def test_short_text_single_element(self):
        """len < max_chars → 单元素列表。"""
        assert _split_text("hello", 100) == ["hello"]

    def test_exact_max_chars(self):
        """len == max_chars → 单元素。"""
        text = "a" * 50
        assert _split_text(text, 50) == [text]

    def test_no_punctuation_force_split(self):
        """无标点长文本 → 按 max_chars 强制切分。"""
        text = "a" * 250
        result = _split_text(text, 100)
        assert len(result) == 3
        assert all(len(seg) <= 100 for seg in result)

    def test_chinese_period_split(self):
        """中文句号分句：标点前 >=10 字符时切分。"""
        # 构造 >100 字符的文本，中间句号前 >=10 字符
        text = "这是一段足够长的测试文本用来验证分句功能。" * 5
        result = _split_text(text, 100)
        assert len(result) >= 2
        # 每段应在句号处切分
        assert any("。" in seg for seg in result[:-1])

    def test_punctuation_short_segment_not_split(self):
        """标点前不足 10 字符 → 不在该标点处切分（当前实现行为）。"""
        # "短。" → len("短。") = 2 < 10 → 不切分
        text = "短。" + "a" * 100
        result = _split_text(text, 100)
        # "短。" 不够 10 字符，继续累积到 max_chars 才切
        assert result[0].startswith("短。")

    def test_empty_string(self):
        """当前实现：len('') <= max_chars → return ['']。"""
        assert _split_text("", 100) == [""]

    def test_exclamation_mark(self):
        text = "你好世界！" + "a" * 20
        result = _split_text(text, 100)
        assert len(result) == 1  # 不足 10 字符在感叹号处，不切

    def test_long_sentence_with_period(self):
        """长文本在句号处切分（标点前 >=10 字符）。"""
        text = "这是一段很长的内容用来测试分句功能是否正确。" * 5
        result = _split_text(text, 100)
        assert len(result) >= 2
        assert any("。" in seg for seg in result[:-1])

    def test_mixed_punctuation(self):
        """混合标点（句号、感叹号）切分。"""
        text = "第一段话足够长了需要切分。" * 3 + "第二段也足够长了！" * 2 + "最后。"
        result = _split_text(text, 50)
        assert len(result) >= 2

    def test_max_chars_very_small(self):
        """max_chars 很小时强制切分。"""
        text = "abcdef"
        result = _split_text(text, 2)
        # len("ab") = 2 >= max_chars → 切分
        assert len(result) == 3
        assert result == ["ab", "cd", "ef"]

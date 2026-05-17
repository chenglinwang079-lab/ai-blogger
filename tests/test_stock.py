"""pipeline/stock.py — _slugify, _is_duplicate, _pick_pexels_file, _pick_pixabay_file"""

import pytest
from pipeline.stock import _slugify, _is_duplicate, _pick_pexels_file, _pick_pixabay_file


# ── _slugify ─────────────────────────────────────────────────────────

class TestSlugify:
    def test_simple_english(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars_removed(self):
        assert _slugify("foo@bar!baz") == "foobarbaz"

    def test_spaces_to_hyphens(self):
        assert _slugify("a  b  c") == "a-b-c"

    def test_underscores_to_hyphens(self):
        assert _slugify("a_b_c") == "a-b-c"

    def test_multiple_hyphens_collapsed(self):
        assert _slugify("a---b") == "a-b"

    def test_leading_trailing_hyphens_stripped(self):
        assert _slugify("-hello-") == "hello"

    def test_empty_result_returns_unknown(self):
        """纯特殊字符/空格 → 空结果 → fallback 'unknown'。"""
        assert _slugify("   ") == "unknown"
        assert _slugify("@@!!") == "unknown"

    def test_empty_string(self):
        assert _slugify("") == "unknown"

    def test_cjk_preserved(self):
        """Python \\w 匹配 Unicode（含 CJK），CJK 文字保留。"""
        result = _slugify("人工智能")
        assert len(result) > 0
        assert "人工智能" in result or result != "unknown"

    def test_numbers_preserved(self):
        assert _slugify("test123") == "test123"

    def test_single_word(self):
        assert _slugify("hello") == "hello"


# ── _is_duplicate ────────────────────────────────────────────────────

class TestIsDuplicate:
    def test_empty_list(self):
        assert _is_duplicate([], "key1") is False

    def test_match_found(self):
        sources = [{"dedupe_key": "abc"}, {"dedupe_key": "xyz"}]
        assert _is_duplicate(sources, "abc") is True

    def test_no_match(self):
        sources = [{"dedupe_key": "abc"}, {"dedupe_key": "xyz"}]
        assert _is_duplicate(sources, "not_there") is False

    def test_missing_dedupe_key_in_entry(self):
        """entry 无 dedupe_key → .get 返回 None，不匹配字符串。"""
        sources = [{"other": "value"}]
        assert _is_duplicate(sources, "abc") is False

    def test_mixed_entries(self):
        sources = [{"other": "val"}, {"dedupe_key": "target"}, {"dedupe_key": "other"}]
        assert _is_duplicate(sources, "target") is True


# ── _pick_pexels_file ────────────────────────────────────────────────

class TestPickPexelsFile:
    @pytest.fixture
    def stock_config(self):
        return {"min_width": 720, "min_height": 1280, "orientation": "portrait"}

    def test_empty_list(self, stock_config):
        assert _pick_pexels_file([], stock_config) is None

    def test_no_link(self, stock_config):
        videos = [{"width": 1920, "height": 1080, "quality": "hd"}]
        assert _pick_pexels_file(videos, stock_config) is None

    def test_portrait_beats_landscape(self, stock_config):
        """portrait (+50) 应胜过 landscape（即使面积更小）。"""
        landscape = {"width": 1920, "height": 1080, "quality": "sd", "link": "L"}
        portrait = {"width": 720, "height": 1280, "quality": "sd", "link": "P"}
        assert _pick_pexels_file([landscape, portrait], stock_config) == "P"

    def test_hd_beats_sd(self, stock_config):
        """HD (+30) 应胜过 SD（同为 portrait）。"""
        sd = {"width": 720, "height": 1280, "quality": "sd", "link": "S"}
        hd = {"width": 720, "height": 1280, "quality": "hd", "link": "H"}
        assert _pick_pexels_file([sd, hd], stock_config) == "H"

    def test_min_dimensions_bonus(self, stock_config):
        """满足 min_width + min_height → +100 分。"""
        below = {"width": 640, "height": 1080, "quality": "hd", "link": "B"}
        above = {"width": 720, "height": 1280, "quality": "hd", "link": "A"}
        assert _pick_pexels_file([below, above], stock_config) == "A"

    def test_single_video(self, stock_config):
        v = {"width": 720, "height": 1280, "quality": "sd", "link": "only"}
        assert _pick_pexels_file([v], stock_config) == "only"


# ── _pick_pixabay_file ───────────────────────────────────────────────

class TestPickPixabayFile:
    @pytest.fixture
    def stock_config(self):
        return {"min_width": 720, "min_height": 1280}

    def test_empty_dict(self, stock_config):
        assert _pick_pixabay_file({}, stock_config) is None

    def test_no_url(self, stock_config):
        videos = {"large": {"width": 1920, "height": 1080}}
        assert _pick_pixabay_file(videos, stock_config) is None

    def test_large_tier_preferred(self, stock_config):
        """large 优先级最高：严格尺寸都不满足时，fallback 按 large→medium→small→tiny 返回第一个有 url 的。"""
        videos = {
            "small": {"width": 480, "height": 640, "url": "S"},
            "large": {"width": 640, "height": 480, "url": "L"},
            "medium": {"width": 640, "height": 640, "url": "M"},
        }
        # 所有 tier 都不满足 720x1280 → fallback → large 先被检查
        result = _pick_pixabay_file(videos, stock_config)
        assert result == "L"

    def test_dimension_filter_strict(self, stock_config):
        """满足 min dimensions 的 tier 优先。"""
        videos = {
            "large": {"width": 1920, "height": 1080, "url": "L"},  # h<1280
            "medium": {"width": 1080, "height": 1920, "url": "M"},  # w>=720, h>=1280 ✓
        }
        # medium 满足严格尺寸，large 不满足
        assert _pick_pixabay_file(videos, stock_config) == "M"

    def test_fallback_to_any_url(self, stock_config):
        """所有 tier 都不满足尺寸 → fallback 到第一个有 url 的。"""
        videos = {
            "tiny": {"width": 320, "height": 240, "url": "T"},
            "small": {"width": 480, "height": 360, "url": "S"},
        }
        # 都不满足 720x1280，但 tiny 优先级最低 → small 先被检查
        result = _pick_pixabay_file(videos, stock_config)
        assert result in ("T", "S")  # 任一有 url 的

    def test_missing_tier_skipped(self, stock_config):
        """缺少的 tier 不报错。"""
        videos = {"large": {"width": 1920, "height": 1080, "url": "L"}}
        result = _pick_pixabay_file(videos, stock_config)
        # large 不满足严格尺寸 → fallback → 返回 L
        assert result == "L"

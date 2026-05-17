import pytest


@pytest.fixture
def minimal_config(tmp_path):
    """最小 config dict，路径指向 tmp_path。"""
    cheat = tmp_path / "cheat"
    output = tmp_path / "dist"
    cheat.mkdir()
    output.mkdir()
    return {
        "paths": {
            "cheat_root": str(cheat),
            "output_dir": str(output),
            "footage_dir": str(tmp_path / "assets" / "footage"),
            "fonts_dir": str(tmp_path / "assets" / "fonts"),
            "bgm_dir": str(tmp_path / "assets" / "bgm"),
        },
        "stock": {
            "min_width": 720,
            "min_height": 1280,
            "orientation": "portrait",
        },
        "quality": {"score_threshold": 7.5, "max_rewrites": 3, "score_scale": 10},
        "video": {"resolution": "1080x1920"},
        "tts": {"max_segment_chars": 100, "backend": "edge-tts"},
        "llm": {"max_retries": 3, "retry_delay_seconds": 5, "timeout_seconds": 60},
    }

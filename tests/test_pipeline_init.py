"""pipeline/__init__.py — validate_script_id + update_manifest"""

import json
import pytest
from pathlib import Path

from pipeline import validate_script_id, update_manifest


# ── validate_script_id ──────────────────────────────────────────────

class TestValidateScriptId:
    def test_valid_lowercase_hex(self):
        assert validate_script_id("a1b2c3d4e5f6") == "a1b2c3d4e5f6"

    def test_valid_all_digits(self):
        assert validate_script_id("000000000000") == "000000000000"

    def test_valid_all_f(self):
        assert validate_script_id("ffffffffffff") == "ffffffffffff"

    def test_uppercase_hex_rejected(self):
        """当前实现 r'^[a-f0-9]{12}$' 仅接受小写，大写应 ValueError。"""
        with pytest.raises(ValueError):
            validate_script_id("ABCDEF123456")

    def test_mixed_case_rejected(self):
        with pytest.raises(ValueError):
            validate_script_id("aBcDeF123456")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            validate_script_id("")

    def test_too_short(self):
        with pytest.raises(ValueError):
            validate_script_id("a1b2c3d4e5f")  # 11 chars

    def test_too_long(self):
        with pytest.raises(ValueError):
            validate_script_id("a1b2c3d4e5f6a")  # 13 chars

    def test_path_traversal(self):
        with pytest.raises(ValueError):
            validate_script_id("../etc/passwd")

    def test_special_chars(self):
        with pytest.raises(ValueError):
            validate_script_id("a1b2-c3d4e5f")

    def test_underscore(self):
        with pytest.raises(ValueError):
            validate_script_id("a1b2_c3d4e5f")

    def test_space(self):
        with pytest.raises(ValueError):
            validate_script_id("a1b2 c3d4e5f")

    def test_non_hex_letter(self):
        with pytest.raises(ValueError):
            validate_script_id("g1b2c3d4e5f6")  # 'g' not hex


# ── update_manifest ──────────────────────────────────────────────────

class TestUpdateManifest:
    def _make_manifest(self, script_dir: Path, *, completed=None, current="score"):
        manifest = {
            "completed_steps": completed or [],
            "current_step": current,
        }
        (script_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest

    def test_append_new_step(self, tmp_path):
        self._make_manifest(tmp_path, completed=["score"])
        update_manifest(tmp_path, "tts")
        result = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert "tts" in result["completed_steps"]
        assert result["current_step"] == "render"

    def test_no_duplicate_step(self, tmp_path):
        self._make_manifest(tmp_path, completed=["score", "tts"])
        update_manifest(tmp_path, "tts")
        result = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert result["completed_steps"].count("tts") == 1

    def test_current_step_mapping_tts_to_render(self, tmp_path):
        self._make_manifest(tmp_path)
        update_manifest(tmp_path, "tts")
        result = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert result["current_step"] == "render"

    def test_current_step_mapping_render_to_export(self, tmp_path):
        self._make_manifest(tmp_path)
        update_manifest(tmp_path, "render")
        result = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert result["current_step"] == "export"

    def test_current_step_mapping_export_to_done(self, tmp_path):
        self._make_manifest(tmp_path)
        update_manifest(tmp_path, "export")
        result = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert result["current_step"] == "done"

    def test_unknown_step_sets_current_to_self(self, tmp_path):
        """未知 step 不在 _STEP_NEXT 中，current_step 设为 step 自身。"""
        self._make_manifest(tmp_path)
        update_manifest(tmp_path, "custom_step")
        result = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert result["current_step"] == "custom_step"

    def test_manifest_not_exists_silent_return(self, tmp_path):
        """manifest.json 不存在时静默返回，不报错。"""
        update_manifest(tmp_path, "tts")  # 不应抛异常
        assert not (tmp_path / "manifest.json").exists()

    def test_predict_step_maps_to_self(self, tmp_path):
        """predict 不在 _STEP_NEXT 中，current_step 为 predict 自身。"""
        self._make_manifest(tmp_path)
        update_manifest(tmp_path, "predict")
        result = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert result["current_step"] == "predict"

    def test_score_step_maps_to_self(self, tmp_path):
        """score 不在 _STEP_NEXT 中，current_step 为 score 自身。"""
        self._make_manifest(tmp_path)
        update_manifest(tmp_path, "score")
        result = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert result["current_step"] == "score"

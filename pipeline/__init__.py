import json
import os
import re
import tempfile
from pathlib import Path

STEP_ORDER = ["topic", "script", "score", "predict", "tts", "render", "export"]

_STEP_NEXT = {"tts": "render", "render": "export", "export": "done"}


def validate_script_id(sid: str) -> str:
    """校验 script_id 格式（12 位十六进制），防止路径遍历攻击。"""
    if not re.match(r'^[a-f0-9]{12}$', sid):
        raise ValueError(f"Invalid script_id: {sid!r}（必须为 12 位十六进制）")
    return sid


def update_manifest(script_dir, step: str) -> None:
    """原子更新 manifest.json 的 completed_steps 和 current_step。"""
    script_dir = Path(script_dir)
    manifest_path = script_dir / "manifest.json"
    if not manifest_path.exists():
        return

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("completed_steps", [])
    if step not in manifest["completed_steps"]:
        manifest["completed_steps"].append(step)
    manifest["current_step"] = _STEP_NEXT.get(step, step)

    fd, tmp_path = tempfile.mkstemp(dir=str(script_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, manifest_path)
    except:
        Path(tmp_path).unlink(missing_ok=True)
        raise

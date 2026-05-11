import re

STEP_ORDER = ["topic", "script", "score", "predict", "tts", "render", "export"]

_STEP_NEXT = {"tts": "render", "render": "export", "export": "done"}


def validate_script_id(sid: str) -> str:
    """校验 script_id 格式（12 位十六进制），防止路径遍历攻击。"""
    if not re.match(r'^[a-f0-9]{12}$', sid):
        raise ValueError(f"Invalid script_id: {sid!r}（必须为 12 位十六进制）")
    return sid


def update_manifest(script_dir, step: str) -> None:
    """更新 manifest.json 的 completed_steps 和 current_step。"""
    import json
    from pathlib import Path

    manifest_path = Path(script_dir) / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if step not in manifest["completed_steps"]:
            manifest["completed_steps"].append(step)
        manifest["current_step"] = _STEP_NEXT.get(step, step)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

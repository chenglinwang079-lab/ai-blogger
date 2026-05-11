STEP_ORDER = ["topic", "script", "score", "predict", "tts", "render", "export"]

_STEP_NEXT = {"tts": "render", "render": "export", "export": "done"}


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

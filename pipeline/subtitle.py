"""字幕：SRT 生成（render 内部调用）"""

import srt
from datetime import timedelta
from pathlib import Path


def generate_srt(script_id: str, config: dict) -> str:
    """根据 timestamps.json 生成 SRT 文件。

    返回 SRT 文件路径。
    """
    import json

    output_dir = Path(config["paths"]["output_dir"]) / script_id
    timestamps_path = output_dir / "timestamps.json"
    srt_path = output_dir / "subtitle.srt"

    if not timestamps_path.exists():
        raise FileNotFoundError(f"timestamps 不存在: {timestamps_path}")

    timestamps = json.loads(timestamps_path.read_text(encoding="utf-8"))

    subtitles = []
    for i, ts in enumerate(timestamps, 1):
        start = timedelta(seconds=ts["start"])
        end = timedelta(seconds=ts["end"])
        subtitles.append(srt.Subtitle(index=i, start=start, end=end, content=ts["text"]))

    srt_path.write_text(srt.compose(subtitles), encoding="utf-8")
    return str(srt_path)

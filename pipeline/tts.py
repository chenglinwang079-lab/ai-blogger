"""TTS：VoxCPM2（降级 edge-tts）"""

import json
import logging
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


def _split_text(text: str, max_chars: int) -> list[str]:
    """按标点或长度拆分文本。"""
    if len(text) <= max_chars:
        return [text]

    segments = []
    current = ""
    for char in text:
        current += char
        if char in "。！？.!?" and len(current) >= 10:
            segments.append(current.strip())
            current = ""
        elif len(current) >= max_chars:
            segments.append(current.strip())
            current = ""
    if current.strip():
        segments.append(current.strip())
    return segments


def _generate_voxcpm2(texts: list[str], config: dict) -> tuple[np.ndarray, int]:
    """VoxCPM2 生成，返回 (audio_array, sample_rate)。"""
    import torch
    from voxcpm import VoxCPM

    model_path = config["paths"]["voxcpm_model"]
    sr = config["tts"]["sample_rate"]

    model = VoxCPM.from_pretrained(model_path, load_denoiser=False)

    all_audio = []
    for text in texts:
        wav = model.generate(text=text, cfg_value=2.0, inference_timesteps=10)
        all_audio.append(wav)

    combined = np.concatenate(all_audio)

    # 清理 VRAM
    del model
    torch.cuda.empty_cache()

    return combined, sr


def _generate_edge_tts(texts: list[str], config: dict) -> tuple[np.ndarray, int]:
    """edge-tts 降级方案，返回 (audio_array, sample_rate)。"""
    import asyncio
    import edge_tts
    from moviepy.audio.io.AudioFileClip import AudioFileClip

    sr = config["tts"]["sample_rate"]
    all_audio = []

    async def _gen(text: str, output_path: str):
        communicate = edge_tts.Communicate(text, "zh-CN-YunxiNeural")
        await communicate.save(output_path)

    for i, text in enumerate(texts):
        mp3_path = None
        clip = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                mp3_path = f.name

            asyncio.run(_gen(text, mp3_path))

            clip = AudioFileClip(mp3_path)
            audio = clip.to_soundarray(fps=sr)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            all_audio.append(audio)
        finally:
            if clip is not None:
                clip.close()
            if mp3_path is not None:
                Path(mp3_path).unlink(missing_ok=True)

    return np.concatenate(all_audio), sr


def generate_audio(script_id: str, config: dict) -> dict:
    """生成音频。

    - 读取 cheat/scripts/<script_id>/final.md
    - 单段超 max_segment_chars → 自动拆分
    - OOM 降级：VoxCPM2 → edge-tts
    - 输出 dist/<script_id>/audio.wav + timestamps.json
    """
    from pipeline import update_manifest, validate_script_id

    validate_script_id(script_id)
    cheat_root = Path(config["paths"]["cheat_root"])
    script_dir = cheat_root / "scripts" / script_id
    output_dir = Path(config["paths"]["output_dir"]) / script_id

    final_path = script_dir / "final.md"
    if not final_path.exists():
        raise FileNotFoundError(f"最终脚本不存在: {final_path}")

    import re
    text = final_path.read_text(encoding="utf-8")
    # 去掉 markdown 标题行、画面指示行，清理 markdown 格式
    lines = []
    for l in text.split("\n"):
        s = l.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("**画面**"):
            continue
        # 去掉 **bold** / *italic* 标记
        s = re.sub(r'\*+', '', s)
        lines.append(s)
    full_text = " ".join(lines)

    max_chars = config["tts"]["max_segment_chars"]
    segments = _split_text(full_text, max_chars)

    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / "audio.wav"
    timestamps_path = output_dir / "timestamps.json"

    # 尝试 VoxCPM2，OOM 降级 edge-tts
    try:
        audio, sr = _generate_voxcpm2(segments, config)
        backend = "voxcpm2"
    except Exception as e:
        # 捕获 VoxCPM2 所有失败（OOM、ImportError、RuntimeError 等）
        logger.warning(f"VoxCPM2 失败 ({e})，降级到 edge-tts")
        audio, sr = _generate_edge_tts(segments, config)
        backend = "edge-tts"

    sf.write(str(audio_path), audio, sr)

    # 更新 manifest
    update_manifest(script_dir, "tts")

    # timestamps — 用实际音频时长按字符比例分配
    info = sf.info(str(audio_path))
    total_duration = info.duration
    total_chars = sum(len(s) for s in segments) or 1

    timestamps = []
    offset = 0.0
    for seg in segments:
        duration = total_duration * len(seg) / total_chars
        timestamps.append({
            "text": seg,
            "start": round(offset, 2),
            "end": round(offset + duration, 2),
        })
        offset += duration

    timestamps_path.write_text(
        json.dumps(timestamps, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "audio_path": str(audio_path),
        "timestamps_path": str(timestamps_path),
        "backend": backend,
        "duration": round(offset, 2),
    }

"""TTS：VoxCPM2（降级 edge-tts）"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# VoxCPM2 模型缓存（避免每次 TTS 重载）
_voxcpm_cache: dict[str, object] = {}


def _get_voxcpm_model(model_path: str):
    """获取缓存的 VoxCPM2 模型，不存在则加载。"""
    if model_path not in _voxcpm_cache:
        from voxcpm import VoxCPM
        _voxcpm_cache[model_path] = VoxCPM.from_pretrained(model_path, load_denoiser=False)
    return _voxcpm_cache[model_path]


def clear_voxcpm_cache() -> None:
    """手动释放 VoxCPM2 模型缓存和 VRAM。"""
    _voxcpm_cache.clear()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


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


def _generate_voxcpm2(texts: list[str], config: dict) -> tuple[np.ndarray, int, list[float]]:
    """VoxCPM2 生成，返回 (audio_array, sample_rate, durations)。"""
    model_path = config["paths"]["voxcpm_model"]
    sr = config["tts"]["sample_rate"]

    model = _get_voxcpm_model(model_path)

    all_audio = []
    durations = []
    for text in texts:
        wav = model.generate(text=text, cfg_value=2.0, inference_timesteps=10)
        all_audio.append(wav)
        durations.append(len(wav) / sr)

    combined = np.concatenate(all_audio)
    return combined, sr, durations


def _run_async_safely(coro):
    """在无 event loop 时用 asyncio.run()，有时在独立线程中运行。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import threading
    result, error = {}, {}

    def runner():
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:
            error["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in error:
        raise error["error"]
    return result["value"]


async def _generate_edge_tts_async(texts: list[str], sr: int) -> tuple[list[np.ndarray], list[float]]:
    """批量 edge-tts 生成，返回 (audio_list, duration_list)。"""
    import edge_tts
    from moviepy.audio.io.AudioFileClip import AudioFileClip

    all_audio = []
    durations = []
    for text in texts:
        mp3_path = None
        clip = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                mp3_path = f.name
            communicate = edge_tts.Communicate(text, "zh-CN-YunxiNeural")
            await communicate.save(mp3_path)
            clip = AudioFileClip(mp3_path)
            audio = clip.to_soundarray(fps=sr)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            all_audio.append(audio)
            durations.append(len(audio) / sr)
        finally:
            if clip is not None:
                clip.close()
            if mp3_path is not None:
                Path(mp3_path).unlink(missing_ok=True)
    return all_audio, durations


def _generate_edge_tts(texts: list[str], config: dict) -> tuple[np.ndarray, int, list[float]]:
    """edge-tts 降级方案，返回 (audio_array, sample_rate, durations)。"""
    sr = config["tts"]["sample_rate"]
    if not texts:
        return np.array([], dtype=np.float32), sr, []
    all_audio, durations = _run_async_safely(_generate_edge_tts_async(texts, sr))
    if not all_audio:
        return np.array([], dtype=np.float32), sr, []
    return np.concatenate(all_audio), sr, durations


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
        audio, sr, durations = _generate_voxcpm2(segments, config)
        backend = "voxcpm2"
    except Exception as e:
        logger.warning(f"VoxCPM2 失败 ({e})，降级到 edge-tts")
        audio, sr, durations = _generate_edge_tts(segments, config)
        backend = "edge-tts"

    # 校验 durations 与 segments 长度一致
    if len(durations) != len(segments):
        raise ValueError(
            f"durations length ({len(durations)}) must match script segments ({len(segments)})"
        )

    sf.write(str(audio_path), audio, sr)

    # 更新 manifest
    update_manifest(script_dir, "tts")

    # timestamps — 用真实音频段时长
    timestamps = []
    offset = 0.0
    for seg, dur in zip(segments, durations):
        timestamps.append({
            "text": seg,
            "start": round(offset, 2),
            "end": round(offset + dur, 2),
        })
        offset += dur

    timestamps_path.write_text(
        json.dumps(timestamps, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "audio_path": str(audio_path),
        "timestamps_path": str(timestamps_path),
        "backend": backend,
        "duration": round(offset, 2),
    }

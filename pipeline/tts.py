"""TTS：VoxCPM2 子进程隔离 + edge-tts fallback"""

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# VoxCPM2 模型缓存（避免每次 TTS 重载）
_voxcpm_cache: dict[str, object] = {}


def _get_voxcpm_model(model_path: str, optimize: bool = True):
    """获取缓存的 VoxCPM2 模型，不存在则加载。"""
    cache_key = f"{model_path}|opt={optimize}"
    if cache_key not in _voxcpm_cache:
        from voxcpm import VoxCPM
        _voxcpm_cache[cache_key] = VoxCPM.from_pretrained(
            model_path, load_denoiser=False, optimize=optimize,
        )
    return _voxcpm_cache[cache_key]


def clear_voxcpm_cache() -> None:
    """手动释放 VoxCPM2 模型缓存和 VRAM。"""
    _voxcpm_cache.clear()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _is_oom_error(exc: Exception) -> bool:
    """判断是否为 OOM 相关错误。"""
    msg = str(exc).lower()
    return "out of memory" in msg or ("cuda" in msg and "memory" in msg)


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


def _generate_segment_voxcpm2(text: str, model, sr: int) -> tuple[np.ndarray, float]:
    """单段 VoxCPM2 生成，返回 (audio_array, duration)。"""
    wav = model.generate(text=text, cfg_value=2.0, inference_timesteps=10)
    wav = np.asarray(wav, dtype=np.float32)
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    duration = len(wav) / sr
    # 显式释放 GPU 中间张量（仅释放推理缓存，模型本体仍在 _voxcpm_cache）
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
    return wav, duration


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


async def _generate_segment_edge_tts_async(text: str, sr: int) -> tuple[np.ndarray, float]:
    """单段 edge-tts 生成。"""
    import edge_tts
    from moviepy.audio.io.AudioFileClip import AudioFileClip

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
        audio = np.asarray(audio, dtype=np.float32)
        duration = len(audio) / sr
        return audio, duration
    finally:
        if clip is not None:
            clip.close()
        if mp3_path is not None:
            Path(mp3_path).unlink(missing_ok=True)


def _generate_segment_edge_tts(text: str, sr: int) -> tuple[np.ndarray, float]:
    """单段 edge-tts 同步包装。"""
    return _run_async_safely(_generate_segment_edge_tts_async(text, sr))


def _concatenate_segments(temp_dir: Path, n: int, sr: int, output_path: Path) -> None:
    """从磁盘逐段拼接到最终 WAV，避免全量加载到内存。"""
    with sf.SoundFile(str(output_path), mode="w", samplerate=sr, channels=1, format="WAV") as out:
        for i in range(n):
            data, file_sr = sf.read(str(temp_dir / f"seg_{i:03d}.wav"), dtype="float32")
            if file_sr != sr:
                raise ValueError(f"Segment sample rate mismatch: {file_sr} != {sr}")
            if data.ndim > 1:
                data = data.mean(axis=1)
            out.write(data)


def _run_voxcpm2_subprocess(
    segments: list[str], config: dict, script_id: str
) -> tuple[list[float], list[str], str | None]:
    """在子进程中运行 VoxCPM2，隔离 native crash。

    Returns:
        (durations, backends, fallback_reason)
        fallback_reason 非 None 表示失败，调用方应 fallback 到 edge-tts。
    """
    output_dir = Path(config["paths"]["output_dir"]) / script_id
    worker_dir = output_dir / "_tts_voxcpm2"
    shutil.rmtree(worker_dir, ignore_errors=True)
    worker_dir.mkdir(parents=True, exist_ok=True)

    input_data = {
        "segments": segments,
        "sample_rate": config["tts"]["sample_rate"],
        "model_path": config["paths"]["voxcpm_model"],
        "optimize": config["tts"].get("voxcpm_optimize", True),
        "output_dir": str(worker_dir),
    }
    input_path = worker_dir / "input.json"
    input_path.write_text(json.dumps(input_data, ensure_ascii=False), encoding="utf-8")

    timeout = config["tts"].get("voxcpm_timeout_seconds", 180)
    worker_script = Path(__file__).resolve().parent.parent / "scripts" / "voxcpm_worker.py"

    logger.info(f"启动 VoxCPM2 子进程: {len(segments)} 段, timeout={timeout}s")

    try:
        proc = subprocess.run(
            [sys.executable, str(worker_script), str(input_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"VoxCPM2 子进程超时 ({timeout}s)")
        return [], [], "voxcpm_subprocess_timeout"

    if proc.returncode != 0:
        stderr = proc.stderr.strip()[-200:] if proc.stderr else ""
        logger.warning(f"VoxCPM2 子进程退出码 {proc.returncode}: {stderr}")
        return [], [], "voxcpm_subprocess_crashed"

    result_path = worker_dir / "result.json"
    if not result_path.exists():
        logger.warning("VoxCPM2 子进程未生成 result.json")
        return [], [], "voxcpm_missing_output"

    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not result.get("ok"):
        logger.warning(f"VoxCPM2 worker 失败: {result.get('error')}")
        return [], [], result.get("fallback_reason", "voxcpm_worker_error")

    durations = result["durations"]
    backends = result["backends"]

    # 校验 WAV 文件完整性
    for i in range(len(segments)):
        wav_path = worker_dir / f"seg_{i:03d}.wav"
        if not wav_path.exists():
            logger.warning(f"VoxCPM2 输出缺失: {wav_path.name}")
            return [], [], "voxcpm_missing_output"

    logger.info(f"VoxCPM2 子进程成功: {len(segments)} 段, 总时长 {sum(durations):.1f}s")
    return durations, backends, None


def _generate_hybrid(
    segments: list[str], config: dict, script_id: str
) -> tuple[str, list[float], list[str], str | None]:
    """混合 TTS：VoxCPM2 子进程优先，失败整批 fallback edge-tts。

    Returns:
        (audio_path, durations, backends, fallback_reason)
        - fallback_reason: None | "config_edge_tts" | "voxcpm_subprocess_*" | ...
    """
    if not segments:
        raise ValueError("No text segments to generate audio")

    sr = config["tts"]["sample_rate"]
    output_dir = Path(config["paths"]["output_dir"]) / script_id
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = output_dir / "_tts_segments"

    # 清理可能残留的临时目录
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 读取 backend 配置，必须在任何 VoxCPM2 import/load 之前
    backend_cfg = config["tts"].get("backend", "voxcpm2").lower()
    if backend_cfg in ("edge-tts", "edge", "edge_tts"):
        logger.info("TTS backend=edge-tts，跳过 VoxCPM2，直接使用 edge-tts")
        backend_cfg = "edge-tts"
    elif backend_cfg not in ("voxcpm2", "hybrid"):
        logger.warning(f"未知 TTS backend={backend_cfg}，回退 edge-tts")
        backend_cfg = "edge-tts"

    durations: list[float] = []
    backends: list[str] = []
    fallback_reason: str | None = None
    voxcpm_worker_dir: Path | None = None

    if backend_cfg == "voxcpm2":
        # ── 子进程隔离 VoxCPM2 ──
        durations, backends, fallback_reason = _run_voxcpm2_subprocess(segments, config, script_id)
        if fallback_reason is None:
            # 成功：从 worker 目录复制 WAV 到 temp_dir
            voxcpm_worker_dir = Path(config["paths"]["output_dir"]) / script_id / "_tts_voxcpm2"
            for i in range(len(segments)):
                src = voxcpm_worker_dir / f"seg_{i:03d}.wav"
                dst = temp_dir / f"seg_{i:03d}.wav"
                shutil.copy2(str(src), str(dst))
        else:
            logger.warning(f"VoxCPM2 子进程失败 ({fallback_reason})，整批 fallback 到 edge-tts")
    else:
        fallback_reason = "config_edge_tts"

    # ── edge-tts fallback（整批或配置指定）──
    if fallback_reason is not None:
        durations = []
        backends = []
        for i, text in enumerate(segments):
            seg_path = temp_dir / f"seg_{i:03d}.wav"
            try:
                wav, dur = _generate_segment_edge_tts(text, sr)
            except Exception as e:
                logger.error(f"段 {i} edge-tts 也失败: {e}")
                raise
            sf.write(str(seg_path), wav, sr)
            del wav
            durations.append(dur)
            backends.append("edge-tts")

    # 校验
    if len(durations) != len(segments) or len(backends) != len(segments):
        raise ValueError(
            f"TTS durations/backends length mismatch: "
            f"{len(durations)}/{len(backends)} vs {len(segments)} segments"
        )

    # 从磁盘拼接（流式）
    audio_path = output_dir / "audio.wav"
    _concatenate_segments(temp_dir, len(segments), sr, audio_path)

    # 清理临时文件
    shutil.rmtree(temp_dir, ignore_errors=True)
    if voxcpm_worker_dir is not None:
        shutil.rmtree(voxcpm_worker_dir, ignore_errors=True)

    return str(audio_path), durations, backends, fallback_reason


def generate_audio(script_id: str, config: dict) -> dict:
    """生成音频。

    - 读取 cheat/scripts/<script_id>/final.md
    - 单段超 max_segment_chars → 自动拆分
    - 每段独立：VoxCPM2 优先，失败段自动降级 edge-tts
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

    # 混合 TTS（每段独立，失败段自动降级）
    audio_path, durations, backends, fallback_reason = _generate_hybrid(segments, config, script_id)

    audio_path = Path(audio_path)

    # 更新 manifest
    update_manifest(script_dir, "tts")

    # timestamps — 用真实音频段时长，新增 backend 字段
    timestamps_path = output_dir / "timestamps.json"
    timestamps = []
    offset = 0.0
    for seg, dur, be in zip(segments, durations, backends):
        timestamps.append({
            "text": seg,
            "start": round(offset, 2),
            "end": round(offset + dur, 2),
            "backend": be,
        })
        offset += dur

    timestamps_path.write_text(
        json.dumps(timestamps, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 统计
    voxcpm_count = backends.count("voxcpm2")
    edge_count = backends.count("edge-tts")
    overall_backend = (
        "voxcpm2" if edge_count == 0
        else "edge-tts" if voxcpm_count == 0
        else "mixed"
    )

    return {
        "audio_path": str(audio_path),
        "timestamps_path": str(timestamps_path),
        "backend": overall_backend,
        "duration": round(offset, 2),
        "segments_total": len(segments),
        "segments_voxcpm2": voxcpm_count,
        "segments_edge_tts": edge_count,
        "fallback_reason": fallback_reason,
    }

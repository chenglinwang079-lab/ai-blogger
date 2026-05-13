"""VoxCPM2 子进程 worker — 隔离 native crash，保护主进程。

用法（由 pipeline/tts.py 通过 subprocess 调用）：
    python scripts/voxcpm_worker.py <input.json>

输入 JSON：
{
  "segments": ["text1", "text2", ...],
  "sample_rate": 48000,
  "model_path": "D:\\voxcpm\\pretrained_models\\VoxCPM2",
  "optimize": false,
  "output_dir": "dist/<script_id>/_tts_voxcpm2"
}

输出：
- output_dir/seg_000.wav, seg_001.wav, ...
- output_dir/result.json  {"ok": true, "durations": [...], "backends": [...]}
  或 {"ok": false, "error": "...", "fallback_reason": "..."}
"""

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def main() -> None:
    if len(sys.argv) != 2:
        print("用法: python voxcpm_worker.py <input.json>", file=sys.stderr)
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"输入文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    cfg = json.loads(input_path.read_text(encoding="utf-8"))
    segments: list[str] = cfg["segments"]
    sr: int = cfg["sample_rate"]
    model_path: str = cfg["model_path"]
    optimize: bool = cfg.get("optimize", True)
    output_dir = Path(cfg["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    result_path = output_dir / "result.json"

    def fail(error: str, reason: str) -> None:
        result_path.write_text(
            json.dumps({"ok": False, "error": error, "fallback_reason": reason}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(error, file=sys.stderr)
        sys.exit(1)

    # ── 加载模型（仅在本进程内 import voxcpm）──
    try:
        from voxcpm import VoxCPM
        model = VoxCPM.from_pretrained(model_path, load_denoiser=False, optimize=optimize)
    except Exception as e:
        fail(f"VoxCPM2 模型加载失败: {e}", "voxcpm_worker_error")

    # ── 逐段生成 ──
    durations: list[float] = []
    backends: list[str] = []

    for i, text in enumerate(segments):
        seg_path = output_dir / f"seg_{i:03d}.wav"
        try:
            wav = model.generate(text=text, cfg_value=2.0, inference_timesteps=10)
            wav = np.asarray(wav, dtype=np.float32)
            if wav.ndim > 1:
                wav = wav.mean(axis=1)
            dur = len(wav) / sr
            sf.write(str(seg_path), wav, sr)
            del wav
            durations.append(dur)
            backends.append("voxcpm2")
            # 释放 GPU 中间张量
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
        except Exception as e:
            fail(f"段 {i} VoxCPM2 生成失败: {e}", "voxcpm_worker_error")

    # ── 校验 ──
    if len(durations) != len(segments):
        fail(
            f"段数不匹配: 生成 {len(durations)} / 输入 {len(segments)}",
            "voxcpm_missing_output",
        )

    # ── 写入成功结果 ──
    result_path.write_text(
        json.dumps(
            {"ok": True, "durations": durations, "backends": backends},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"VoxCPM2 worker 完成: {len(segments)} 段, 总时长 {sum(durations):.1f}s")


if __name__ == "__main__":
    main()

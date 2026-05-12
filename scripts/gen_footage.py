#!/usr/bin/env python3
"""
用 ffmpeg 生成抽象科技背景素材
粒子、光线、数据流、网络节点、脉冲波等
"""
import subprocess
import os
import sys

BASE = r"D:\ai-blogger\assets\footage"

# 通用参数: 1080x1920 竖屏, 10秒, 30fps
W, H, DUR, FPS = 1080, 1920, 10, 30

def run(cmd, desc):
    print(f"\n>>> {desc}")
    print(f"    {cmd[:120]}...")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"    [FAIL] {r.stderr[:300]}")
        return False
    print(f"    [OK]")
    return True

# ============================================================
# abstract 目录 — 通用科技背景
# ============================================================

def gen_abstract():
    outdir = os.path.join(BASE, "abstract")
    ok = 0

    # 1) 蓝色粒子漂浮 (life 细胞自动机 + 蓝色调色)
    cmd = (
        f'ffmpeg -y -f lavfi -i "life=s={W//4}x{H//4}:mold=10:r={FPS}:ratio=0.1:death_color=0x000020:life_color=0x4488ff:mold_color=0x001133"'
        f' -vf "scale={W}:{H}:flags=lanczos,eq=brightness=-0.1:saturation=2,colorbalance=bs=0.8:bm=0.3"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/abstract-blue-particles-06.mp4"'
    )
    ok += run(cmd, "abstract-blue-particles-06 (粒子漂浮)")

    # 2) 绿色数据流 (life 滤镜 + 绿色调)
    cmd = (
        f'ffmpeg -y -f lavfi -i "life=s={W//4}x{H//4}:mold=8:r={FPS}:ratio=0.05:death_color=0x001000:life_color=0x00ff44:mold_color=0x003300"'
        f' -vf "scale={W}:{H}:flags=lanczos,eq=brightness=-0.15:saturation=2.5,colorbalance=gs=0.8:gm=0.2"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/abstract-green-dataflow-07.mp4"'
    )
    ok += run(cmd, "abstract-green-dataflow-07 (绿色数据流)")

    # 3) 紫蓝光线扫描 (mandelbrot 分形 + 色调)
    cmd = (
        f'ffmpeg -y -f lavfi -i "mandelbrot=s={W}x{H}:r={FPS}:maxiter=120:start_scale=0.02:end_scale=0.001:start_x=-0.745:start_y=0.186"'
        f' -vf "eq=brightness=-0.2:saturation=3,colorbalance=bs=0.6:rs=-0.3:gm=-0.2,curves=lighter"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/abstract-purple-light-08.mp4"'
    )
    ok += run(cmd, "abstract-purple-light-08 (紫蓝光线)")

    # 4) 青色网络节点 (life + 青色调)
    cmd = (
        f'ffmpeg -y -f lavfi -i "life=s={W//4}x{H//4}:mold=12:r={FPS}:ratio=0.08:death_color=0x001010:life_color=0x00cccc:mold_color=0x003333"'
        f' -vf "scale={W}:{H}:flags=lanczos,eq=brightness=-0.1:saturation=2,colorbalance=bs=0.4:gs=0.5"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/abstract-cyan-network-09.mp4"'
    )
    ok += run(cmd, "abstract-cyan-network-09 (青色网络)")

    # 5) 暗红脉冲波 (geq 方程生成同心圆扩散)
    cmd = (
        f'ffmpeg -y -f lavfi -i "color=c=black:s={W}x{H}:r={FPS}:d={DUR}"'
        f' -vf "geq=lum=\'clip(128+80*sin(2*PI*(hypot(X-{W//2},Y-{H//2})/80-T*N/{FPS})),0,255)\':cb=128:cr=160,eq=brightness=-0.3:saturation=1.5"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/abstract-red-pulse-10.mp4"'
    )
    ok += run(cmd, "abstract-red-pulse-10 (暗红脉冲)")

    # 6) 蓝色波纹扩散
    cmd = (
        f'ffmpeg -y -f lavfi -i "color=c=black:s={W}x{H}:r={FPS}:d={DUR}"'
        f' -vf "geq=lum=\'clip(100+90*sin(2*PI*(hypot(X-{W//2},Y-{H//2})/60-T*N/{FPS}*0.8)),0,255)\':cb=160:cr=128,eq=brightness=-0.2:saturation=2"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/abstract-blue-wave-11.mp4"'
    )
    ok += run(cmd, "abstract-blue-wave-11 (蓝色波纹)")

    # 7) 矩阵雨效果 (cellauto 竖条纹 + 绿色)
    cmd = (
        f'ffmpeg -y -f lavfi -i "cellauto=s={W//8}x{H//8}:rule=30:rate={FPS}:random_fill_ratio=0.02:scroll=1"'
        f' -vf "scale={W}:{H}:flags=neighbor,eq=brightness=-0.3:saturation=3,colorbalance=gs=0.9:gm=0.3"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/abstract-matrix-rain-12.mp4"'
    )
    ok += run(cmd, "abstract-matrix-rain-12 (矩阵雨)")

    # 8) 星空粒子 (life 稀疏 + 高亮)
    cmd = (
        f'ffmpeg -y -f lavfi -i "life=s={W//4}x{H//4}:mold=6:r={FPS}:ratio=0.02:death_color=0x000005:life_color=0xffffff:mold_color=0x000010"'
        f' -vf "scale={W}:{H}:flags=lanczos,eq=brightness=-0.2:saturation=0.5,colorbalance=bs=0.2"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/abstract-starfield-13.mp4"'
    )
    ok += run(cmd, "abstract-starfield-13 (星空粒子)")

    return ok

# ============================================================
# ai 目录 — AI 聊天界面风格背景
# ============================================================

def gen_ai():
    outdir = os.path.join(BASE, "ai")
    ok = 0

    # 1) 蓝紫渐变流动 (mandelbrot 缓慢缩放)
    cmd = (
        f'ffmpeg -y -f lavfi -i "mandelbrot=s={W}x{H}:r={FPS}:maxiter=80:start_scale=0.005:end_scale=0.0005:start_x=-0.7:start_y=0.27"'
        f' -vf "eq=brightness=-0.15:saturation=2.5,colorbalance=bs=0.5:rs=-0.2:bm=0.2"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/ai-neural-flow-06.mp4"'
    )
    ok += run(cmd, "ai-neural-flow-06 (神经网络流)")

    # 2) 蓝色细胞自动机 (AI 神经元风格)
    cmd = (
        f'ffmpeg -y -f lavfi -i "life=s={W//4}x{H//4}:mold=10:r={FPS}:ratio=0.06:death_color=0x000015:life_color=0x3366ff:mold_color=0x001144"'
        f' -vf "scale={W}:{H}:flags=lanczos,eq=brightness=-0.1:saturation=2,colorbalance=bs=0.7:bm=0.2"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/ai-neuron-fire-07.mp4"'
    )
    ok += run(cmd, "ai-neuron-fire-07 (神经元激活)")

    # 3) 数据脉冲 (同心圆向外扩散)
    cmd = (
        f'ffmpeg -y -f lavfi -i "color=c=0x000020:s={W}x{H}:r={FPS}:d={DUR}"'
        f' -vf "geq=lum=\'clip(80+70*sin(2*PI*(hypot(X-{W//2},Y-{H//2})/50-T*N/{FPS}*1.2)),0,255)\':cb=160:cr=128"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/ai-data-pulse-08.mp4"'
    )
    ok += run(cmd, "ai-data-pulse-08 (数据脉冲)")

    return ok

# ============================================================
# code 目录 — 代码/终端风格背景
# ============================================================

def gen_code():
    outdir = os.path.join(BASE, "code")
    ok = 0

    # 1) 绿色终端矩阵 (cellauto + 滚动)
    cmd = (
        f'ffmpeg -y -f lavfi -i "cellauto=s={W//8}x{H//8}:rule=110:rate={FPS}:random_fill_ratio=0.03:scroll=1"'
        f' -vf "scale={W}:{H}:flags=neighbor,eq=brightness=-0.4:saturation=2.5,colorbalance=gs=0.8:gm=0.3"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/code-terminal-matrix-06.mp4"'
    )
    ok += run(cmd, "code-terminal-matrix-06 (终端矩阵)")

    # 2) 蓝色代码流 (life 模拟代码滚动)
    cmd = (
        f'ffmpeg -y -f lavfi -i "life=s={W//4}x{H//4}:mold=8:r={FPS}:ratio=0.04:death_color=0x000008:life_color=0x00aaff:mold_color=0x001122"'
        f' -vf "scale={W}:{H}:flags=lanczos,eq=brightness=-0.2:saturation=2,colorbalance=bs=0.6"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/code-blue-stream-07.mp4"'
    )
    ok += run(cmd, "code-blue-stream-07 (蓝色代码流)")

    return ok

# ============================================================
# data 目录 — 数据可视化风格背景
# ============================================================

def gen_data():
    outdir = os.path.join(BASE, "data")
    ok = 0

    # 1) 蓝绿数据网络 (life + 青蓝色调)
    cmd = (
        f'ffmpeg -y -f lavfi -i "life=s={W//4}x{H//4}:mold=10:r={FPS}:ratio=0.07:death_color=0x000808:life_color=0x00cccc:mold_color=0x002222"'
        f' -vf "scale={W}:{H}:flags=lanczos,eq=brightness=-0.15:saturation=2.5,colorbalance=bs=0.3:gs=0.5"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/data-network-graph-06.mp4"'
    )
    ok += run(cmd, "data-network-graph-06 (数据网络)")

    # 2) 数据波形 (geq 生成多个叠加正弦波)
    cmd = (
        f'ffmpeg -y -f lavfi -i "color=c=0x000810:s={W}x{H}:r={FPS}:d={DUR}"'
        f' -vf "geq=lum=\'clip(60+50*sin(2*PI*(X/100+T*N/{FPS}*0.5))+40*sin(2*PI*(Y/80-T*N/{FPS}*0.3)),0,255)\':cb=140:cr=120"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/data-waveform-07.mp4"'
    )
    ok += run(cmd, "data-waveform-07 (数据波形)")

    return ok

# ============================================================
# chip 目录 — 芯片/硬件风格背景
# ============================================================

def gen_chip():
    outdir = os.path.join(BASE, "chip")
    ok = 0

    # 1) 电路板纹理 (cellauto + 橙金色调)
    cmd = (
        f'ffmpeg -y -f lavfi -i "cellauto=s={W//6}x{H//6}:rule=90:rate={FPS}:random_fill_ratio=0.01:scroll=0"'
        f' -vf "scale={W}:{H}:flags=neighbor,eq=brightness=-0.35:saturation=1.5,colorbalance=rs=0.4:gs=0.2:bs=-0.2"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/chip-circuit-board-06.mp4"'
    )
    ok += run(cmd, "chip-circuit-board-06 (电路板)")

    # 2) 蓝色散热气流 (life + 冷色调)
    cmd = (
        f'ffmpeg -y -f lavfi -i "life=s={W//4}x{H//4}:mold=10:r={FPS}:ratio=0.05:death_color=0x000010:life_color=0x2255cc:mold_color=0x001133"'
        f' -vf "scale={W}:{H}:flags=lanczos,eq=brightness=-0.15:saturation=2,colorbalance=bs=0.6:bm=0.2"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/chip-cooling-flow-07.mp4"'
    )
    ok += run(cmd, "chip-cooling-flow-07 (散热气流)")

    return ok

# ============================================================
# robot 目录 — 机器人/数字人风格背景
# ============================================================

def gen_robot():
    outdir = os.path.join(BASE, "robot")
    ok = 0

    # 1) 红外扫描线 (geq 水平扫描线)
    cmd = (
        f'ffmpeg -y -f lavfi -i "color=c=0x080000:s={W}x{H}:r={FPS}:d={DUR}"'
        f' -vf "geq=lum=\'clip(40+35*sin(2*PI*(Y/30+T*N/{FPS}*2))+20*sin(2*PI*(X/200-T*N/{FPS}*0.5)),0,255)\':cb=120:cr=180,eq=brightness=-0.3:saturation=2"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/robot-infrared-scan-05.mp4"'
    )
    ok += run(cmd, "robot-infrared-scan-05 (红外扫描)")

    # 2) 蓝色电子网格 (life + 冷蓝)
    cmd = (
        f'ffmpeg -y -f lavfi -i "life=s={W//4}x{H//4}:mold=8:r={FPS}:ratio=0.04:death_color=0x000010:life_color=0x4488ff:mold_color=0x001133"'
        f' -vf "scale={W}:{H}:flags=lanczos,eq=brightness=-0.15:saturation=2,colorbalance=bs=0.7:bm=0.2"'
        f' -c:v libx264 -preset fast -crf 23 -t {DUR} "{outdir}/robot-electric-grid-06.mp4"'
    )
    ok += run(cmd, "robot-electric-grid-06 (电子网格)")

    return ok

# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    total = 0
    print("=" * 60)
    print("ffmpeg 科技背景素材生成器")
    print("=" * 60)

    for name, fn in [
        ("abstract", gen_abstract),
        ("ai", gen_ai),
        ("code", gen_code),
        ("data", gen_data),
        ("chip", gen_chip),
        ("robot", gen_robot),
    ]:
        print(f"\n{'='*40} {name} {'='*40}")
        n = fn()
        print(f"  {name}: 生成 {n} 个")
        total += n

    print(f"\n{'='*60}")
    print(f"总计生成 {total} 个素材")
    print(f"{'='*60}")

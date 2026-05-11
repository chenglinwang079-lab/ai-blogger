# 本地素材池

将视频素材放到对应子目录，渲染时自动匹配脚本段落的 `visual_keyword`。

## 目录结构

```
footage/
├── ai/           # AI 产品、模型、界面
├── code/         # 代码、开发、终端
├── robot/        # 机器人、数字人
├── chip/         # 芯片、算力、硬件
├── data/         # 数据、图表、网络
└── abstract/     # 通用科技背景（未匹配时 fallback）
```

## 支持格式

`.mp4` / `.mov` / `.webm` / `.avi`

## 命名建议

文件名和目录名会自动作为匹配标签：

- `ai/gpt4-demo.mp4` → tags: `ai`, `gpt4`, `demo`
- `chip/gpu-nvidia.mp4` → tags: `chip`, `gpu`, `nvidia`

中文关键词会自动展开为英文别名（如"芯片" → `chip`）。

## 建议

- 竖屏素材（9:16）效果最佳，横屏会自动居中裁剪
- 5-20 秒为宜，短素材会自动循环
- 使用无版权或自有授权素材

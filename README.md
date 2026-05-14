# AI Blogger

半自动 AI 短视频内容生产系统。从选题到发布包，一条命令打通全流程。

```
选题 → 脚本 → 质控 → TTS → 渲染 → 导出 → 复盘
```

## 功能

- **智能选题**：从 AI 热榜自动拉取候选话题，LLM 打分排序 Top N 推荐
- **脚本生成**：5 种风格模板（严肃科普 / 轻松吐槽 / 商业分析 / 故事叙事 / 快讯解读），LLM 自动生成 60-90 秒分段脚本
- **质量校准**：10 分制多维度打分，低于阈值自动重写（最多 3 轮），盲预测流量档位
- **TTS 语音**：默认 edge-tts（稳定无需 GPU），可选 VoxCPM2 子进程隔离（RTX 4070 实测 ~4 it/s），失败自动 fallback
- **视频渲染**：深色渐变背景 + 关键词大字 + 字幕烧录 + BGM 智能匹配
- **发布包导出**：抖音 / 快手 / B站 平台适配（标题截断、标签格式、封面尺寸）
- **运营闭环**：发布队列管理、表现数据录入、预测 vs 实际复盘
- **WebUI 工作台**：Gradio 9-tab 界面，一键跑完全流程

## 快速开始

### 环境要求

- Python 3.12+
- CUDA（仅 VoxCPM2 需要，edge-tts 模式无需 GPU）
- FFmpeg

### 安装

```bash
git clone https://github.com/chenglinwang079-lab/ai-blogger.git
cd ai-blogger
pip install -r requirements.txt
```

### 配置

```bash
cp config.example.toml config.toml
```

编辑 `config.toml`，填入 API key：

```toml
[api]
openai_api_key = ""  # 或设置环境变量 MIMO_API_KEY
openai_base_url = "https://your-api-endpoint/v1"

[paths]
voxcpm_model = "path/to/VoxCPM2"  # 仅 VoxCPM2 模式需要
```

或通过环境变量（推荐，不入库）：

```bash
export MIMO_API_KEY="your-key"
```

### CLI 使用

```bash
# 完整流程
python main.py run --topic "Claude 4 发布"

# 从已有脚本恢复（断点续跑）
python main.py run --script-id f6e5d4c3b2a1

# 单步执行
python main.py step topic                          # 拉取选题
python main.py step script --topic "Claude 4 发布" # 生成脚本
python main.py step score --script-id <id>         # 质控打分
python main.py step tts --script-id <id>           # 语音合成
python main.py step render --script-id <id>        # 视频渲染
python main.py export --script-id <id>             # 导出发布包

# 平台适配导出
python main.py export --script-id <id> --platform douyin
python main.py export --script-id <id> --all-platforms

# 指定风格
python main.py step script --topic "..." --style casual_sarcasm

# BGM 管理
python main.py bgm list
python main.py bgm suggest --script-id <id>

# 复盘
python main.py retro --script-id <id> --views 5000 --likes 200 --comments 30
python main.py retro --batch --platform douyin

# 状态查询
python main.py status
```

### WebUI

```bash
python app.py
# 浏览器打开 http://localhost:7860
```

9 个 Tab：状态监控 / 选题 / 脚本 / 质控 / 发布包 / 复盘 / 素材管理 / 发布管理 / 一键工作流。

## 架构

```
main.py (CLI)  ─┬─→ pipeline/topic.py      # 选题
                 ├─→ pipeline/script.py     # 脚本生成
                 ├─→ pipeline/quality.py    # 质控打分
                 ├─→ pipeline/tts.py        # TTS 语音
                 ├─→ pipeline/video.py      # 视频渲染
                 ├─→ pipeline/export_pkg.py # 导出
                 └─→ adapter/cheat.py       # 质量校准 adapter

app.py (WebUI) ──→ Gradio 9-tab 工作台
```

### 设计原则

1. **磁盘持久化**：所有中间产物写磁盘，不依赖进程内存
2. **checkpoint/resume**：每步完成记录 manifest.json，失败可从断点恢复
3. **降级兜底**：LLM 重试、VoxCPM2 崩溃 fallback edge-tts、API 失败不阻塞手动输入
4. **子进程隔离**：VoxCPM2 在独立子进程运行，native crash 不影响主进程

### TTS 双引擎

| 引擎 | 优势 | 适用场景 |
|------|------|---------|
| edge-tts | 稳定、无需 GPU、零配置 | 生产默认 |
| VoxCPM2 | 本地推理、音质更好 | 有 GPU 时可选 |

VoxCPM2 通过 `scripts/voxcpm_worker.py` 子进程隔离运行，失败自动整批 fallback 到 edge-tts。

```toml
[tts]
backend = "edge-tts"    # "edge-tts" | "voxcpm2" | "hybrid"
```

### ID 体系

| ID | 用途 | 生成 |
|----|------|------|
| `candidate_id` | 候选选题 | `sha256(source + title + url)[:12]` |
| `script_id` | 脚本 | `sha256(title + script_text)[:12]` |
| `run_id` | 执行 | `YYYYMMDD_HHmmss` |

## 目录结构

```
ai-blogger/
├── main.py                    # CLI 入口
├── app.py                     # Gradio WebUI
├── config.toml                # 配置（.gitignore）
├── config.example.toml        # 配置模板
├── pipeline/                  # 核心管线
│   ├── topic.py               # 选题
│   ├── script.py              # 脚本生成
│   ├── quality.py             # 质控
│   ├── tts.py                 # TTS 双引擎
│   ├── video.py               # 视频渲染
│   ├── export_pkg.py          # 导出
│   ├── bgm.py                 # BGM 匹配
│   ├── styles.py              # 风格模板
│   ├── recommend.py           # 每日推荐
│   ├── publish.py             # 发布队列
│   ├── performance.py         # 表现数据
│   └── ...
├── adapter/                   # cheat-on-content adapter
├── scripts/                   # VoxCPM2 worker
├── cheat/                     # 数据根目录
│   ├── scripts/<script_id>/   # 脚本工作区
│   ├── predictions/           # 盲预测
│   └── candidates.md          # 候选池
├── dist/<script_id>/          # 输出（视频、音频、发布包）
└── assets/
    ├── fonts/
    ├── bgm/                   # BGM 素材
    ├── footage/               # 视频素材池
    └── styles/                # 风格 catalog
```

## 素材管理

```bash
# 补充外部素材（Pexels/Pixabay）
export PEXELS_API_KEY="your-key"
python main.py stock fill --script-id <id>

# 素材统计
python main.py footage stats
python main.py footage missed --top 10
python main.py footage health
```

## 配置参考

完整配置见 [`config.example.toml`](config.example.toml)，主要 section：

| Section | 说明 |
|---------|------|
| `[api]` | API endpoint 和 key |
| `[models]` | LLM 模型选择 |
| `[tts]` | TTS 引擎配置 |
| `[video]` | 渲染参数（分辨率、字体、BGM 音量） |
| `[quality]` | 质控阈值和重写次数 |
| `[stock]` | 外部素材下载配置 |
| `[export.*]` | 各平台导出规格 |

## 依赖

```
openai>=1.0          # LLM 调用
moviepy              # 视频处理
edge-tts             # TTS（默认引擎）
soundfile            # 音频读写
Pillow               # 图像处理
numpy                # 数值计算
srt                  # SRT 字幕
requests             # HTTP
imageio-ffmpeg       # FFmpeg 后端
```

可选：`voxcpm`（VoxCPM2 TTS，需 CUDA）、`gradio`（WebUI）、`python-dotenv`（.env 支持）

## License

MIT

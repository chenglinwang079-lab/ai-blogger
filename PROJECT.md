# AI 博主半自动内容生产 + 质量校准系统

## 定位

半自动内容生产 + 质量校准系统。核心闭环：每条内容走完 topic → script → score → predict → tts → render → export → retro。

**不做**：自动发布、素材 API、数字人 MVP。先打通内容生产与质量校准闭环。

## 设计原则

1. **磁盘持久化**：所有中间产物写磁盘，不依赖进程内存。step 和 run 共享同一套 IO 逻辑。
2. **checkpoint/resume**：每步完成后记录 progress，失败后可从断点恢复，不浪费 LLM 调用。
3. **cheat-on-content 协议对齐**：adapter 层对齐 candidate-schema、prediction template、state-management 协议。
4. **降级兜底**：LLM 调用有重试，VoxCPM2 OOM 回退 edge-tts，API 失败不阻塞手动输入。

## 已确认资产

| 资产 | 路径 | 说明 |
|------|------|------|
| 项目目录 | `D:\ai-blogger\` | 已创建 |
| Python 环境 | `D:\voxcpm\venv\` | Python 3.12 + PyTorch CUDA 12.6 |
| VoxCPM2 权重 | `D:\voxcpm\pretrained_models\VoxCPM2\` | 2B TTS 模型，48kHz |
| cheat-on-content | GitHub: XBuilderLAB/cheat-on-content | 质量校准 skill |

## ID 体系

三类 ID 职责明确，不可混用：

| ID | 用途 | 生成算法 | 示例 |
|---|---|---|---|
| `candidate_id` | 候选选题的唯一标识 | `sha256(source + normalized_title + url_path)[:12]` | `a1b2c3d4e5f6` |
| `script_id` | 一篇脚本的唯一标识 | `sha256(title + script_text)[:12]` | `f6e5d4c3b2a1` |
| `run_id` | 一次 CLI 执行的唯一标识 | `YYYYMMDD_HHmmss` | `20260511_143022` |

CLI `--script-id` 只能指向最终脚本，不能指向候选选题。

### candidate_id 生成规范

```python
import hashlib
from urllib.parse import urlparse

def normalize_title(title: str) -> str:
    """去首尾空白，连续空白合并为单空格，全小写"""
    import re
    return re.sub(r'\s+', ' ', title.strip()).lower()

def normalize_url_path(url: str) -> str:
    """提取 path 部分，去除尾斜杠；空 URL 返回空字符串"""
    if not url:
        return ""
    parsed = urlparse(url)
    return parsed.path.rstrip('/')

def make_candidate_id(source: str, title: str, url: str = "") -> str:
    """candidate_id = sha256(source|normalized_title|url_path)[:12]
    分隔符固定为 |，source 格式为 <adapter-type>:<source-name>
    """
    raw = f"{source}|{normalize_title(title)}|{normalize_url_path(url)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]

def make_script_id(title: str, script_text: str) -> str:
    """script_id = sha256(title + script_text)[:12]"""
    return hashlib.sha256(f"{title}{script_text}".encode()).hexdigest()[:12]
```

## 目录结构

```
D:\ai-blogger\
├── main.py                        # CLI 入口
├── config.toml                    # 配置
├── requirements.txt
├── pipeline/
│   ├── __init__.py                # STEP_ORDER 定义
│   ├── llm_utils.py               # 统一 LLM 调用封装（重试、超时）
│   ├── topic.py                   # 选题：AI HOT API
│   ├── script.py                  # 脚本：LLM 生成
│   ├── quality.py                 # 质控：打分 + 重写循环 + 盲预测
│   ├── tts.py                     # TTS：VoxCPM2（降级 edge-tts）
│   ├── subtitle.py                # 字幕：SRT 生成（render 内部调用）
│   ├── video.py                   # 渲染：模板视频 + 字幕 + BGM
│   ├── export_pkg.py              # 导出：发布包（支持平台适配）
│   ├── publish.py                 # Phase J-1: 发布队列 CRUD
│   ├── performance.py             # Phase J-2: 表现数据 CRUD
│   └── platform_profiles.py       # Phase J-3: 平台规格加载
├── adapter/
│   ├── __init__.py
│   └── cheat.py                   # cheat-on-content 本地 adapter
├── cheat/                         # cheat_root，所有 cheat-on-content 数据在此
│   ├── .cheat-state.json
│   ├── rubric_notes.md            # 评分标准
│   ├── scripts/                   # 脚本工作区
│   │   └── <script_id>/
│   │       ├── manifest.json      # 元数据（title, candidate_id, created_at, steps, platform_exports）
│   │       ├── draft.md           # 初始脚本
│   │       ├── final.md           # 质控通过的最终版
│   │       ├── best.md            # 重写未通过时的最高分版本
│   │       ├── score.json         # 打分缓存
│   │       ├── publish.json       # Phase J-1: 发布状态（.gitignore）
│   │       └── performance.json   # Phase J-2: 表现数据（.gitignore）
│   ├── predictions/               # 盲预测（immutable）
│   │   ├── <script_id>.json       # 机器源（结构化 JSON）
│   │   └── <script_id>.md         # 人工审阅副本
│   ├── videos/                    # 复盘报告
│   │   └── <script_id>/
│   │       └── report.md
│   └── candidates.md              # 候选选题池
├── dist/                          # 输出（每次 export 覆盖）
│   └── <script_id>/
│       ├── final.mp4
│       ├── audio.wav
│       ├── title.txt
│       ├── description.txt
│       ├── tags.txt
│       ├── thumbnail.png
│       ├── prediction.json        # 从 cheat/predictions/<script_id>.json 拷贝
│       └── platforms/             # Phase J-3：平台适配导出
│           ├── douyin/
│           │   ├── metadata.json  # 结构化元数据（自动发布用）
│           │   ├── title.txt
│           │   ├── description.txt
│           │   ├── tags.txt
│           │   └── thumbnail.png  # 1080x1920
│           ├── kuaishou/
│           └── bilibili/          # thumbnail 1920x1080
├── assets/
│   ├── fonts/
│   └── bgm/
│       ├── bgm_catalog.json         # BGM 配置（.gitignore，本地维护）
│       └── bgm_catalog.example.json # BGM 配置模板（入库）
└── skills/
    └── cheat-on-content/          # 可选：克隆的 skill
```

**cheat_root 说明**：adapter 只走 `cheat/` 根目录。上游 skill 如需兼容，在项目根目录创建 symlink 或设置环境变量 `CHEAT_ROOT=cheat/`。

## config.toml

```toml
[api]
openai_api_key = "sk-xxx"
openai_base_url = "https://api.openai.com/v1"

[models]
script_model = "gpt-4o"
score_model = "gpt-4o"
predict_model = "gpt-4o"

[paths]
voxcpm_model = "D:/voxcpm/pretrained_models/VoxCPM2"
fonts_dir = "assets/fonts"
bgm_dir = "assets/bgm"
output_dir = "dist"
cheat_root = "cheat"               # cheat-on-content 数据根目录

[quality]
score_scale = 10                   # 评分量纲：10 分制（0-10）
score_threshold = 7.5              # composite 低于此值触发重写
max_rewrites = 3                   # 最多重写轮数

[llm]
max_retries = 3
retry_delay_seconds = 5            # 初始重试间隔（指数退避）
timeout_seconds = 60

[tts]
backend = "voxcpm2"
fallback = "edge-tts"
max_segment_chars = 100            # 单段超长自动拆分阈值
sample_rate = 48000                # 统一输出采样率

[video]
resolution = "1080x1920"
bgm_volume = 0.15
subtitle_font = "Microsoft YaHei"
subtitle_fontsize = 42
```

## 核心接口设计

### pipeline/topic.py
```python
def fetch_topics(category: str = "ai-models", take: int = 10) -> list[dict]:
    """AI HOT API → 候选列表（每项含 candidate_id）
    返回: [{candidate_id, title, source: "trend:aihot", snapshot_text, snapshot_at, url}]
    写入 cheat/candidates.md 前按 candidate_id 去重
    API 失败返回空列表 + stderr 警告
    """
```

### pipeline/script.py
```python
def generate_script(topic: dict) -> dict:
    """LLM 生成分段脚本
    输入: topic dict（至少含 title, snapshot_text）
    输出: {
        script_id: str,
        title: str,
        segments: [{text, visual_keyword, duration_est}],
        script_text: str,         # 纯文本版，供打分用
        script_dir: str           # cheat/scripts/<script_id>/
    }
    创建 cheat/scripts/<script_id>/manifest.json + draft.md
    """
```

### pipeline/quality.py
```python
def quality_check(script_id: str, config: dict) -> dict:
    """质控主流程
    流程:
      1. 读取 cheat/scripts/<script_id>/draft.md
      2. adapter.score_script() 打分 → 写 score.json
      3. composite < threshold → 重写（最多 max_rewrites 轮）
      4. 通过后 adapter.write_prediction() 写 predictions/<script_id>.json + .md
      5. 最终版写 final.md + 更新 manifest.json
    返回: {passed, score, rewrites}
    重写 3 次仍未通过 → passed=false，保存 best.md
    """
```

### adapter/cheat.py
```python
def load_rubric() -> dict:
    """读取 cheat/rubric_notes.md → 结构化 rubric"""

def score_script(script_text: str, rubric: dict) -> dict:
    """LLM 按 rubric 打分（10 分制）
    → {dimensions: [{name, score, reason}], composite}
    composite = 各维度均值（v0 等权）
    """

def write_prediction(script_id: str, score: dict, prediction: dict, force: bool = False):
    """写盲预测（immutable）
    已存在 predictions/<script_id>.json 时拒绝覆盖，除非 force=True
    quality_check 流程中自动调用；step predict 为独立补写/重生成入口
    同时写 cheat/predictions/<script_id>.json（机器源）和 .md（人工审阅）
    prediction schema: {
        "views": {"p50": int, "p80": int},
        "likes": {"p50": int, "p80": int},
        "comments": {"p50": int, "p80": int},
        "bucket": "S/A/B/C",          # 预期流量档位
        "probability_distribution": {"viral": 0.05, "high": 0.2, "medium": 0.5, "low": 0.25},
        "drivers": ["..."],            # 看好因素
        "risks": ["..."],              # 风险因素
        "platform": "douyin",
        "horizon_days": 3,
        "confidence": float,
        "rationale": str,
        "rubric_version": "v0",
        "created_at": "ISO-8601"
    }
    """

def retro(script_id: str, actual: dict) -> dict:
    """输入真实数据 → 对比预测 → 写 cheat/videos/<script_id>/report.md
    更新 .cheat-state.json（calibration_samples, last_retro_at）
    """
```

### pipeline/tts.py
```python
def generate_audio(script_id: str, config: dict) -> dict:
    """VoxCPM2 逐段生成
    - 读取 cheat/scripts/<script_id>/final.md
    - 单段超 max_segment_chars → 自动拆分
    - OOM 降级：VoxCPM2 → edge-tts（mp3→wav 转码 + 统一 sample_rate）
    - 输出 dist/<script_id>/audio.wav + timestamps.json
    """
```

### pipeline/video.py
```python
def render_video(script_id: str, config: dict, *, bgm_id: str | None = None, mute: bool = False) -> str:
    """模板视频（内部调用 subtitle.py 生成 SRT）
    - 深色渐变背景 + 关键词大字 + 标题卡
    - 字幕烧录 + BGM 混音
    - 输出 dist/<script_id>/final.mp4
    - bgm_id: 手动指定 BGM id（无效时回退自动匹配）
    - mute: 静音模式（不加 BGM）
    """
```

### pipeline/export_pkg.py
```python
def export_package(script_id: str, config: dict, *, platform: str | None = None) -> str:
    """导出发布资料包到 dist/<script_id>/（覆盖已有文件）
    platform=None: 根目录导出（向后兼容）
    platform=<name>: 根目录导出 + 平台适配导出到 platforms/<platform>/
    prediction.json 从 cheat/predictions/<script_id>.json 直接拷贝
    """
```

## CLI 命令

```bash
# 完整流程
python main.py run --topic "Claude 4发布"
python main.py run --topic "Claude 4发布" --until predict
python main.py run --topic "Claude 4发布" --until export
python main.py run --script-id f6e5d4c3b2a1        # 从已有脚本恢复
python main.py run --topic "Claude 4发布" --force   # 强制从头重跑

# 单步（统一 --script-id）
python main.py step topic
python main.py step topic --category ai-products --take 20
python main.py step script --topic "Claude 4发布"
python main.py step script --script-id f6e5d4c3b2a1   # 重写/续写已有脚本
python main.py step score --script-id f6e5d4c3b2a1
python main.py step predict --script-id f6e5d4c3b2a1          # 补写/重生成预测（已存在则拒绝，除非 --force）
python main.py step tts --script-id f6e5d4c3b2a1
python main.py step render --script-id f6e5d4c3b2a1
python main.py step render --script-id f6e5d4c3b2a1 --mute            # 静音渲染
python main.py step render --script-id f6e5d4c3b2a1 --bgm-id xxx      # 手动指定 BGM
python main.py export --script-id f6e5d4c3b2a1

# BGM 管理
python main.py bgm list                    # 列出可用 BGM
python main.py bgm suggest --script-id xxx # 推荐 BGM（自动匹配）

# 复盘
python main.py retro --script-id f6e5d4c3b2a1 --views 5000 --likes 200 --comments 30
python main.py retro --title "Claude 4" --views 5000 --likes 200 --comments 30

# 状态查询
python main.py status

# 发布队列（Phase J-1）
python main.py queue list
python main.py queue show --script-id f6e5d4c3b2a1
python main.py queue update --script-id f6e5d4c3b2a1 --status published --post-url "https://..."

# 表现数据（Phase J-2）
python main.py performance record --script-id f6e5d4c3b2a1 --platform douyin --views 1200 --likes 86
python main.py performance latest --script-id f6e5d4c3b2a1
python main.py retro --batch --platform douyin

# 平台适配导出（Phase J-3）
python main.py export --script-id f6e5d4c3b2a1 --platform douyin
python main.py export --script-id f6e5d4c3b2a1 --all-platforms
```

## 步骤依赖（DAG）

```
topic → script → quality_check → tts → render → export
                                  ↓
                               predict
```

MVP 采用线性执行，不并行。subtitle 作为 render 内部步骤，不单独暴露。

## checkpoint/resume

`cheat/scripts/<script_id>/manifest.json`：
```json
{
  "script_id": "f6e5d4c3b2a1",
  "candidate_id": "a1b2c3d4e5f6",
  "title": "Claude 4 发布",
  "created_at": "2026-05-11T14:30:00Z",
  "completed_steps": ["topic", "script", "score", "predict"],
  "current_step": "tts"
}
```

`candidate_id` 可为 `null`（手动 `--topic` 输入时无候选 ID）。

- `run` 启动时查 manifest.json，跳过已完成步骤
- `--force` 忽略 checkpoint，从头重跑
- `--script-id` 通过 `cheat/scripts/<script_id>/` 目录直接定位，无需额外索引

## 依赖

```
openai>=1.0
requests
moviepy
srt
Pillow
numpy
soundfile               # WAV 读写（VoxCPM2 输出）
imageio-ffmpeg          # MoviePy ffmpeg 后端
edge-tts                # 备用 TTS
```

edge-tts 输出 mp3，tts.py 负责转码为 WAV 并统一 sample_rate（48kHz）后再拼接。

（Python 3.12 内置 tomllib，不需要 tomli）

## VRAM 管理

- VoxCPM2 单独加载，逐段生成后 `torch.cuda.empty_cache()`
- 视频渲染纯 CPU（MoviePy + ffmpeg）
- 不与其他 GPU 模型同时运行

---

## 实施步骤

### Phase 1: 核心校准引擎（优先级最高）

- [ ] **Step 0: main.py CLI 骨架 + 基础设施**
  - argparse 实现 run / step / export / retro / status
  - --until / --force / --script-id 参数
  - config.toml 加载（tomllib）+ validate_config() 校验必填 key
  - ensure_directories() 自动创建所有子目录
  - pipeline/llm_utils.py：统一 call_llm() 封装（指数退避重试、429 尊重 Retry-After、content filter 不重试）
  - pipeline/__init__.py 中定义 STEP_ORDER = ["topic", "script", "score", "predict", "tts", "render", "export"]

- [ ] **Step 1: 项目初始化**
  - 创建子目录结构（含 cheat/scripts/<script_id>/ 嵌套）
  - requirements.txt + config.toml（API key 留空）
  - 安装依赖到 D:\voxcpm\venv
  - 初始化 cheat/rubric_notes.md（v0 等权版，10 分制）
  - 初始化 cheat/.cheat-state.json
  - 文件约定：UTF-8 无 BOM + LF 换行

- [ ] **Step 2: 选题 — pipeline/topic.py**
  - AI HOT API 调用（UA 必须带浏览器标识）
  - 每项含 candidate_id（按规范生成）
  - 写入 cheat/candidates.md 前按 candidate_id 去重
  - API 失败返回空列表 + stderr 警告

- [ ] **Step 3: 脚本 — pipeline/script.py**
  - LLM 生成分段脚本（模型从 config 读取）
  - System prompt：AI 博主口语化，60-90 秒，每段 10-15 秒
  - 创建 cheat/scripts/<script_id>/manifest.json + draft.md
  - script_id = sha256(title + script_text)[:12]

- [ ] **Step 4: 质控 — pipeline/quality.py + adapter/cheat.py**
  - load_rubric() 读取评分标准（10 分制）
  - score_script(script_text) → score.json
  - composite < 7.5 → 重写（最多 3 轮）
  - 通过后 write_prediction() → predictions/<script_id>.json + .md
  - 最终版写 final.md + 更新 manifest.json

### Phase 2: 内容资产生成层

- [ ] **Step 5: TTS — pipeline/tts.py**
  - 读取 final.md，逐段 VoxCPM2 生成
  - 单段超长自动拆分
  - OOM 降级 → edge-tts（mp3→wav 转码 + 48kHz 统一）
  - 输出 dist/<script_id>/audio.wav + timestamps.json

- [ ] **Step 6: 渲染 — pipeline/video.py**
  - 内部调用 subtitle.py 生成 SRT
  - 深色渐变背景 + 关键词大字 + 标题卡 + 字幕烧录 + BGM
  - 输出 dist/<script_id>/final.mp4

- [ ] **Step 7: 导出 — pipeline/export_pkg.py**
  - 发布包：final.mp4 + audio.wav + title.txt + description.txt + tags.txt + thumbnail.png + prediction.json
  - prediction.json 从 cheat/predictions/<script_id>.json 直接拷贝
  - dist/<script_id>/ 已存在时覆盖

### Phase 3: 长期价值

- [ ] **Step 8: 复盘 — adapter/cheat.py retro()**
  - 支持 --script-id / --title 模糊搜索
  - 更新 .cheat-state.json
  - 3 次同方向 miss → 提示 cheat-bump

- [ ] **Step 9: 状态查询 — main.py status**
  - 扫描 cheat/scripts/*/manifest.json
  - 显示最近进度 + cheat/ 文件清单

## 验证清单

1. `python main.py step topic` → 输出 5+ 话题（含 candidate_id）
2. `python main.py step script --topic "xxx"` → 创建 cheat/scripts/\<script_id\>/draft.md
3. `python main.py step score --script-id xxx` → 输出 10 分制打分 JSON
4. `python main.py step predict --script-id xxx` → 写入 predictions/\<script_id\>.json + predictions/\<script_id\>.md
5. `python main.py step tts --script-id xxx` → 输出 WAV（无 OOM）
6. `python main.py step render --script-id xxx` → 输出 MP4
7. `python main.py export --script-id xxx` → 输出完整发布包
8. `python main.py retro --script-id xxx --views 1000 --likes 50` → 写入复盘报告

## Phase H：外部素材补全器

自动检测缺失素材 → 调用 Pexels/Pixabay API 搜索下载 → 写入本地素材池 → 渲染自动生效。

### 使用方法

```powershell
# 1. 设置 API key（环境变量，不写入文件）
$env:PEXELS_API_KEY = "your_pexels_key"
$env:PIXABAY_API_KEY = "your_pixabay_key"   # 可选

# 2. 补充素材（指定 script-id）
D:\voxcpm\venv\Scripts\python.exe main.py stock fill --script-id <script_id>

# 3. 可选参数
--provider pexels    # 只用 Pexels
--provider pixabay   # 只用 Pixabay
--limit 1            # 每个关键词最多下载 1 个
```

### 渲染验证

```powershell
# footage 模式渲染，检查 matched 是否增加
D:\voxcpm\venv\Scripts\python.exe main.py step render --script-id <id> --force
# 查看报告
cat dist/<id>/render_report.json
```

### 安全说明

- **API key 只走环境变量**，不写入 config.toml 或代码
- `assets/footage/external/`、`.cache/`、`sources.json` 已加入 `.gitignore`
- 素材文件不进 git，本地使用

### 配置（config.toml）

```toml
[stock]
auto_fill_enabled = false    # 未来自动补素材开关
provider_order = ["pexels", "pixabay"]
max_downloads_per_keyword = 2
cache_hours = 24
orientation = "portrait"
min_width = 720
min_height = 1280
```

## 后续扩展（不在 MVP）

- ~~素材混剪（Pexels/Pixabay）~~ ✅ Phase H 已完成
- ~~素材池质量与命中率优化~~ ✅ Phase I 已完成
- ~~运营闭环基础版~~ ✅ Phase J 已完成
- ~~缩略图质量升级~~ ✅ Phase K-1 已完成
- ~~BGM 智能匹配~~ ✅ Phase K-2 / K-2.1 已完成
- 数字人口播（Wav2Lip/MuseTalk）
- Playwright 自动发布
- 每日定时生成
- 步骤并行执行（tts + predict）

## Phase I：素材池质量与命中率优化

修复 source tracking bug、统一别名表、blocklist 禁用机制、统计面板、未命中关键词分析、健康检查。

### CLI 命令

```powershell
# 素材命中率统计
D:\voxcpm\venv\Scripts\python.exe main.py footage stats
D:\voxcpm\venv\Scripts\python.exe main.py footage stats --script-id <id>

# 未命中关键词 Top N
D:\voxcpm\venv\Scripts\python.exe main.py footage missed --top 10

# 外部素材健康检查
D:\voxcpm\venv\Scripts\python.exe main.py footage health

# 禁用/启用素材
D:\voxcpm\venv\Scripts\python.exe main.py footage disable "assets/footage/abstract/xxx.mp4" --reason "太暗"
D:\voxcpm\venv\Scripts\python.exe main.py footage disabled
D:\voxcpm\venv\Scripts\python.exe main.py footage enable "assets/footage/abstract/xxx.mp4"

# 统一别名表查看
D:\voxcpm\venv\Scripts\python.exe main.py footage aliases
D:\voxcpm\venv\Scripts\python.exe main.py footage aliases --category tech
```

### 统计口径（固定定义）

- `matched`: 直接关键词命中（非 abstract/ 目录）
- `abstract_fallback`: 命中 abstract/ 目录素材
- `gradient_fallback`: 无素材可用，渐变背景兜底
- 命中率 = matched / segments_total（不含 abstract_fallback）

### 新增文件

| 文件 | 说明 |
|------|------|
| `pipeline/keyword_aliases.py` | 统一别名表（ALIASES + SEARCH_QUERIES + CATEGORIES） |
| `pipeline/footage_stats.py` | 统计 + 未命中关键词分析 |
| `assets/footage/blocklist.json` | 素材黑名单（.gitignore，不进 git） |

### 关键改动

- `pipeline/video.py`: source tracking 改用 `match_footage_with_reason()`，keyword 保留原始值
- `pipeline/footage.py`: `index_footage()` 新增 `exclude` 参数，blocklist 函数
- `pipeline/stock.py`: 新增 `health_check_external()`，import 改用 keyword_aliases
- `main.py`: `footage` 子命令组（stats/missed/health/disable/enable/disabled/aliases）
- `app.py`: Tab 7 素材管理面板（统计/未命中/健康检查按钮）

## Phase J：运营闭环基础版 ✅

发布队列 → 表现数据录入 → 批量复盘 → 平台适配导出，打通从"内容生产完成"到"发布就绪"的闭环。

### J-1：发布队列管理

per-script `publish.json` 跟踪发布状态（draft/ready/scheduled/published/failed），export 时自动 set ready（不覆盖 scheduled/published）。

```powershell
python main.py queue list
python main.py queue show --script-id <id>
python main.py queue update --script-id <id> --status published --post-url "https://..."
python main.py queue init --script-id <id>
```

### J-2：历史表现数据库 + retro 自动化

per-script `performance.json`（append-only，按 captured_at 升序），retro 支持 `--from-performance` 和 `--batch`。

```powershell
python main.py performance record --script-id <id> --platform douyin --views 1200 --likes 86 --comments 12 --shares 5
python main.py performance latest --script-id <id>
python main.py performance list
python main.py retro --script-id <id> --from-performance --platform douyin
python main.py retro --batch --platform douyin
```

### J-3：平台适配导出

LLM 适配标题/描述/标签到各平台规范，规则截断 fallback。每个平台目录含 `metadata.json`（自动发布用）。

| 平台 | 标题上限 | 标签格式 | 封面尺寸 |
|------|---------|---------|---------|
| douyin | 55 | `#话题` 空格分隔 | 1080×1920 |
| kuaishou | 30 | `#话题` 空格分隔 | 1080×1920 |
| bilibili | 80 | 逗号分隔 | 1920×1080 |

```powershell
python main.py export --script-id <id> --platform douyin
python main.py export --script-id <id> --all-platforms
```

### 新增文件

| 文件 | 说明 |
|------|------|
| `pipeline/publish.py` | 发布队列 CRUD |
| `pipeline/performance.py` | 表现数据 CRUD |
| `pipeline/platform_profiles.py` | 平台规格加载 |

### 关键改动

- `pipeline/export_pkg.py`: root/platform 拆分，`_adapt_metadata()` LLM 适配 + fallback
- `main.py`: queue/performance 子命令组，export `--platform` / `--all-platforms`
- `app.py`: Tab 8 发布管理（队列 + 表现数据 + 批量复盘），Tab 5 平台选择导出
- `config.toml` + `config.example.toml`: 新增 `[export.douyin/kuaishou/bilibili]`

### 下一步

Phase K-2.1 完成，进入下一阶段规划。

## Phase K：质量升级 ✅

### K-1：缩略图质量升级 ✅

视觉层次、3-stop 渐变、描边效果、accent line、底部遮罩、像素级换行、4 级字体 fallback、平台标题源。

### K-2：BGM 智能匹配 ✅

catalog 加载 → 脚本情绪推断（4 mood: tech/tense/warm/upbeat）→ 匹配链（mood → energy → first_available）→ CLI `bgm suggest` → WebUI 推荐展示。

### K-2.1：BGM 手动选择与预览 ✅

三种控制模式：自动匹配 / 手动指定 / 静音。

| 组件 | 改动 |
|------|------|
| `pipeline/bgm.py` | `list_available_bgm()` + `resolve_bgm_by_id()` |
| `pipeline/video.py` | `render_video(bgm_id, mute)` + render_report 追加 BGM mode/id/reason |
| `main.py` | `step --bgm-id`/`--mute` + `bgm list` |
| `app.py` | Radio + Dropdown + Audio 预览 + mode 切换回调 |

render_report.json BGM 字段：
```json
{"mode": "manual", "id": "iceman_instrumental_01", "reason": null}
```

mode 枚举：`mute` | `manual` | `auto_fallback` | `auto`

### BGM 本地配置

`assets/bgm/bgm_catalog.json`（.gitignore，不入库）：

```json
[
  {
    "id": "iceman_instrumental_01",
    "path": "D:/CloudMusic/电台节目/Mkuag - Drake - ICEMAN (Instrumental).mp3",
    "mood": "tense",
    "energy": "high",
    "bpm": 140,
    "tags": ["说唱", "节奏", "冲突", "科技", "紧张"],
    "enabled": true
  }
]
```

- `path` 支持绝对路径和相对路径（相对于 `assets/bgm/`）
- `mood` 枚举：tech / tense / warm / upbeat
- `energy` 枚举：low / medium / high
- `enabled: false` 可临时禁用条目

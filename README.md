# 🎬 视频智能剪辑系统

自动处理视频：**删除静音片段** + **语音转文字** + **按关键词删除片段**。

## 核心功能

| 功能 | 说明 |
|------|------|
| 🔇 删除静音 | 用 ffmpeg `silencedetect` 检测无声片段，自动切除（阈值/最短时长可调） |
| 📝 语音转文字 | 基于 faster-whisper 本地转写（中文），支持导入 SRT/VTT 字幕 |
| ✂️ 关键词删片 | 输入黑名单关键词，命中词的字幕段整段删除（可调前后留白） |
| 🔗 组合剪辑 | 静音删除 + 关键词删除同时执行，取交集 |

## 技术栈

- **FastAPI** + **Jinja2** + **SQLite/SQLAlchemy**
- **faster-whisper**（本地语音转文字，CPU 友好）
- **ffmpeg**（静音检测 + trim/concat 精确剪辑）

## 架构设计

三个需求统一为「计算保留区间 → ffmpeg 切片合并」：

```
静音删除   →  keep_ranges = 有声区间
关键词删除 →  keep_ranges = 不含关键词的字幕段
组合       →  两个 keep_ranges 取交集
                          ↓
          clipper.clip_by_ranges()  ← 统一剪辑引擎
          (filter_complex trim+concat，帧级精确)
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 确保系统有 ffmpeg
ffmpeg -version

# 启动
chmod +x start.sh
./start.sh
# 或: PYTHONPATH=. python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 打开浏览器
# http://localhost:8000
```

## 使用流程

1. **上传视频**：拖拽或点击上传（mp4/mkv/avi/mov/webm）
2. **语音转文字**：点击「开始转写」（CPU 模式 medium 模型约 1:1 时长），或导入已有 SRT/VTT 字幕
3. **智能剪辑**：
   - 调整静音阈值（-30dB 宽松 / -40dB 严格）和最短静音时长
   - 输入要删除的关键词（逗号分隔），可「预览命中」查看哪些段会被删
   - 选择：① 仅删静音 / ② 仅删关键词 / ③ 组合
4. **下载结果**：剪辑完成后直接下载

## 配置说明

编辑 `config.yaml`：

```yaml
whisper:
  model_size: medium    # tiny/base/small/medium/large-v3（越大越准越慢）
  device: cpu           # 有 GPU 可改 cuda
  compute_type: int8    # CPU用int8，GPU用float16

silence:
  noise_db: -30         # 静音阈值，越小越严格
  min_duration: 0.5     # 短于此值的静音不删除
```

## 项目结构

```
video-clipper/
├── app/
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置加载
│   ├── database.py             # 数据模型 (Video/Transcript/ClipResult)
│   ├── routers/videos.py       # API 路由
│   ├── services/
│   │   ├── silence_detector.py # 静音检测 → 有声保留区间
│   │   ├── transcriber.py      # 语音转文字 (faster-whisper)
│   │   ├── keyword_filter.py   # 关键词过滤 → 保留区间
│   │   └── clipper.py          # 统一剪辑引擎 (trim+concat)
│   ├── templates/index.html    # Web UI
│   └── static/                 # CSS/JS
├── config.yaml
├── requirements.txt
└── start.sh
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/videos` | 视频列表 |
| POST | `/api/videos/upload` | 上传视频 |
| POST | `/api/videos/{id}/transcribe` | 语音转文字 |
| POST | `/api/videos/{id}/upload-subtitle` | 导入字幕 |
| GET | `/api/videos/{id}/transcript` | 获取字幕 |
| POST | `/api/videos/{id}/clip-silence` | 删除静音 |
| POST | `/api/videos/{id}/clip-keywords` | 按关键词删除 |
| POST | `/api/videos/{id}/clip-combo` | 组合剪辑 |
| GET | `/api/videos/{id}/results` | 剪辑结果列表 |
| GET | `/api/videos/{id}/download/{result_id}` | 下载结果 |

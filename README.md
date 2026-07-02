# OpenRouter Data Scraper

定期抓取 [OpenRouter](https://openrouter.ai) 模型活动数据的 CLI 工具。

## 为什么需要这个工具

OpenRouter 是目前衡量 LLM 市场欢迎度最好的公开代理指标——月处理 100T+ tokens、覆盖 70+ 供应商、400+ 模型、4.2M+ 用户。它的 `/rankings` 页面和每个模型页面的 "Activity" 图表展示了按天粒度的 token / request 用量。

但这些数据有两个限制：
1. **没有官方 data API**——只有前端渲染的图表，背后是未公开的 `/api/frontend/v1/stats/model-activity` 端点
2. **31 天 trailing window**——单次请求最多只能拿到最近 31 天数据

本工具通过定期抓取 + 本地 SQLite 去重存储，突破这两个限制，长期累积历史时间序列。

## 安装

```bash
uv venv .venv
uv pip install -e '.[viz,dev]'
```

## 使用

### 1. Discover — 发现 Top-N 模型

```bash
ords discover --top 20
```

从 OpenRouter rankings 获取按 weekly token 排序的 Top-20 模型列表，含 canonical_slug（用于后续 fetch）。

### 2. Fetch — 抓取单模型用量

```bash
ords fetch z-ai/glm-5.2
ords fetch openai/gpt-5.5 --variant nitro
```

抓取指定模型的逐日 token / request / tool_call 用量并打印。支持 short slug（自动解析为 canonical_slug）。

### 3. Archive — 去重存储

```bash
ords archive --top 20                          # 抓取 Top-20 并存入 SQLite
ords archive --slug openai/gpt-5.5            # 抓取单模型并存储
ords archive --top 20 --variants standard,nitro  # 多变体
```

抓取数据并以 `(variant_permaslug, date, variant)` 为主键去重写入 SQLite。重复执行不会产生重复行。

### 4. Query — 查询历史数据

```bash
ords query --slug glm-5.2
ords query --slug glm-5.2 --from 2026-06-01 --to 2026-07-01
```

### 5. Dashboard — 可视化

```bash
ords dashboard --slug glm-5.2
ords dashboard --top 10                       # 对比 Top-10 模型
```

从 SQLite 读取数据并生成 PNG 图表（prompt tokens + completion tokens 双图）。

## 定期运行

每周执行一次 `ords archive --top 20` 即可累积历史数据。具体部署方案见 [docs/deployment.md](docs/deployment.md)。

## 数据字段

每次抓取包含以下字段（每天一条记录）：

| 字段 | 含义 |
|---|---|
| `total_prompt_tokens` | 输入 prompt token |
| `total_completion_tokens` | 输出 completion token |
| `total_native_tokens_reasoning` | reasoning 思考 token |
| `total_native_tokens_cached` | 缓存命中 token |
| `count` | 当天请求总数 |
| `total_tool_calls` | tool call 总数 |
| `requests_with_tool_call_errors` | tool call 错误请求数 |
| `num_media_prompt` / `num_media_completion` | 多模态 token |
| `num_video_prompt` / `video_output_seconds` | 视频 token |
| `stt_transcript_characters` | 语音转文字字符数 |
| `num_audio_prompt` | 音频 prompt token |
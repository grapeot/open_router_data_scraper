# RFC — OpenRouter Data Scraper

## 架构

```
┌──────────────────────────────────────────────┐
│  Process Launcher (launcher.yaml periodic)   │
│  每周五 09:00 触发                             │
│  command: scripts/run_periodic.sh            │
│  → opencode_skill submit --prompt-file        │
│  → prompts/weekly_scrape.md                   │
└──────────────┬───────────────────────────────┘
               ▼
┌──────────────────────────────────────────────┐
│  OpenCode Session (cwd: open_router_data_scraper/) │
│                                              │
│  1. ords archive --top 20   (两层抓取)        │
│  2. ords snapshot           (快照+图表)       │
│  3. ords query              (sanity check)    │
│  4. ords dashboard          (更新 PNG)        │
│  5. if 失败: 调查 + 修复                      │
│  6. if 失败 or dry_run: 发邮件                │
└──────┬───────────────────┬───────────────────┘
       │                   │
       ▼                   ▼
┌──────────────┐   ┌──────────────┐
│  SQLite DB    │   │  Resend Email │
│  data/ords.db │   │  → Outlook    │
└──────────────┘   └──────────────┘
```

## 数据端点

### Rolling data（按天/周累积，INSERT OR IGNORE 去重）

| 端点 | 用途 | 时间分辨率 | 回溯窗口 |
|---|---|---|---|
| `/api/frontend/v1/rankings/models?view=month` | 全模型逐日活动（批量层） | 天 | 17 天 |
| `/api/frontend/v1/stats/model-activity?permaslug=<slug>&variant=standard` | 单模型逐日活动（补全层，含 reasoning/cached） | 天 | 31 天 |
| `/api/frontend/v1/rankings/model-rankings-chart` | Top-10 模型排名 | 周 | 52 周 |
| `/api/frontend/v1/rankings/tools` | tool call 用量 | 不规律 | ~3 个月 |
| `/api/frontend/v1/rankings/images` | 图片生成用量 | 不规律 | ~3 个月 |
| `/api/frontend/v1/rankings/modality-chart` | 按 modality 请求量 | 不规律 | ~1 年 |
| `/api/frontend/v1/rankings/natural-language` | 按自然语言用量 | 不规律 | ~1 个月 |
| `/api/frontend/v1/rankings/programming-language` | 按编程语言用量 | 不规律 | ~1 个月 |
| `/api/frontend/v1/rankings/context-length` | 按 context length bucket | 不规律 | ~6 个月 |

### Snapshot data（每周快照，存 snapshot_date）

| 端点 | 用途 |
|---|---|
| `/api/frontend/v1/rankings/task-spend` | 30 天 spend 份额（按用例分类） |
| `/api/frontend/v1/rankings/performance` | 模型 latency/throughput 快照 |
| `/api/frontend/v1/rankings/benchmarks` | benchmark 分数快照 |
| `/api/frontend/v1/rankings/apps` | app 排名（day/week/month） |

## 两层抓取策略

`archive` 命令分两层：

1. **批量层**：`rankings/models?view=month` — 1 次请求拿到 400+ 模型 17 天数据。reasoning/cached 字段为 0（端点不提供），但 prompt/completion/count 与 model-activity 完全一致。
2. **补全层**：对 Top-20 逐个请求 `model-activity` — 20 次请求，补全 reasoning/cached 字段 + 当天数据。由于 PK 相同，重复行被 INSERT OR IGNORE 去重。

总请求：21 次。数据覆盖：400+ 模型（基础字段）+ Top-20（完整字段）。

## 每周一次的安全性

`rankings/models?view=month` 的 17 天窗口 > 7 天间隔，两次爬取间有 10 天重叠。即使某天数据滞后，下次爬取仍在窗口内。不会漏天级数据。

## canonical_slug 解析

`model-activity` 端点要求 canonical_slug（带日期后缀）。CLI 自动解析：末尾 8 位数字 → 已是 canonical；否则从 `/api/v1/models` 查 `canonical_slug` 字段。

## SQLite Schema

- `model_activity` — PK=(variant_permaslug, date, variant)，INSERT OR IGNORE 去重
- `chart_series` — PK=(endpoint, date, slug)，时间序列去重
- `snapshot_task_spend` — PK=(snapshot_date, category_key)
- `snapshot_performance` — PK=(snapshot_date, slug)
- `snapshot_benchmarks` — PK=(snapshot_date, category, uid)
- `snapshot_apps` — PK=(snapshot_date, window, app_id)

## 测试策略

- **Unit tests**（离线）: mock httpx，测试 client 所有端点 + store 去重/快照
- **Integration tests**（mock HTTP）: httpx MockTransport，测试 CLI 全子命令流程
- **Live tests**（需 `ORDS_ENABLE_LIVE_TESTS=1`）: 真实请求 OpenRouter API
- **CI**（GitHub Actions）: unit + integration，不跑 live

## 依赖

- `httpx` — HTTP client
- `matplotlib`（optional `[viz]`）— dashboard
- `pytest`（optional `[dev]`）— 测试
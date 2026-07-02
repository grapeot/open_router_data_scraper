# PRD — OpenRouter Data Scraper

## 问题

OpenRouter 是目前衡量 LLM 市场欢迎度最好的公开代理指标（月处理 100T+ tokens、覆盖 70+ 供应商、400+ 模型）。它提供按天粒度的模型 token / request 用量数据，但有两个限制：

1. **没有官方 data API**——只有前端渲染的图表，背后是未公开的前端 stats 端点
2. **31 天 trailing window**——单次请求最多只能拿到最近 31 天数据

## 目标

通过每周定期抓取 + 本地 SQLite 去重存储，突破这两个限制，长期累积历史时间序列。

## 功能范围

### 核心 CLI（`ords`）

1. **discover** — 列出 Top-N 模型（默认 20）
2. **fetch** — 抓取单模型逐日用量并打印
3. **archive** — 两层抓取存入 SQLite：
   - 层 1（批量）：`rankings/models?view=month`，一次请求获取全 400+ 模型 17 天数据
   - 层 2（补全）：对 Top-20 逐个请求 `model-activity`，补全 reasoning/cached 字段
4. **snapshot** — 抓取所有快照端点（task-spend/performance/benchmarks/apps）+ 图表时间序列
5. **query** — 从 SQLite 查询历史数据
6. **models** — 列出 DB 中已追踪模型
7. **dashboard** — 生成 PNG 图表

### 定期任务

- **触发方式**: Background Process Manager 的 `periodic_jobs`（launcher.yaml 声明式），每周五 09:00 本地时间
- **执行方式**: 调用 `opencode_skill submit` 启动 OpenCode session，session 自动完成 archive + snapshot → sanity check → dashboard → 邮件通知
- **成功时**: 静默，不发邮件
- **失败时**: session 自行调查、尝试修复，不论修复成功与否都发邮件到 `grapeot@outlook.com`
- **dry run 时**: 不论成功失败都发邮件（验证邮件通道）

## 非目标

- 不接外部网络服务——数据只存本地 SQLite
- 不做实时查询——数据每周快照一次
- 不做多人协作——单用户、单机部署

## 成功标准

1. `ords discover --top 20` 正确返回 Top-20 模型
2. `ords archive --top 20` 两层抓取去重写入 SQLite
3. `ords snapshot` 抓取所有快照+图表端点存入 SQLite
4. 每周五 09:00 自动触发，session 完成全流程
5. 失败时收到邮件，成功时安静
6. Unit test + integration test 在 GitHub Actions CI 中通过
7. Live test 需 `ORDS_ENABLE_LIVE_TESTS=1` 才执行
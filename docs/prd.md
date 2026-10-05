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
   - 层 1（批量）：`rankings/models?view=day`，一次请求获取 400+ 模型前一日数据
   - 层 2（补全）：对 Top-20 逐个请求 `model-activity`，补全 31 天 reasoning/cached 字段
4. **snapshot** — 抓取所有快照端点（task-spend/performance/benchmarks/apps）+ 图表时间序列
5. **digest** — 从 SQLite 计算确定性的每周 digest packet，写出 `data/digest_<date>.md` + 同名 `.json`，内含数据质量门、市场总量与迁移、top-N 面板、新面孔与异常、外部富集候选
6. **query** — 从 SQLite 查询历史数据
7. **models** — 列出 DB 中已追踪模型
8. **dashboard** — 生成 PNG 图表

### Digest Packet（算与说分离）

`ords digest` 是"算"：把混合来源的 `model_activity` 归档转成一份可审计的 packet，严格遵守 `schema/model_activity.schema.json` 的解释契约。下游 agent 是"说"：读 packet 与 schema 契约，写成人的话，不自行重算指标。packet 编码的关键正确性规则：

- 全市场总量与 provider 份额只用「干净」的全市场日（`COUNT(*) >= 300` 且非混批日，`market-snapshot-days`）；更小的日期是变化的 top-N 面板，不得求和当市场总量。
- 缓存率是 `cached / prompt`，不是 `cached / (prompt + completion)`。
- 某日聚合 cached 超过 aggregate prompt 属物理不可能，标记为混批日并从所有趋势中剔除（`contaminated_days`）。
- 除全市场日外，最新日从趋势中剔除（`partial-current-day`）。

### 每周解读

周期任务在抓取后新增解读环节：`ords digest` 产出 packet → 对新高量/异常模型做 Firecrawl 外部富集 → Antigravity起草中文解读 → 主 Agent 事实审核 → 落 `docs/weekly/<date>.md` → 每周发 digest 邮件。

### 定期任务

- **触发方式**: Background Process Manager 的 `periodic_jobs`（launcher.yaml 声明式），每周五 09:00 本地时间
- **执行方式**: 调用 `opencode_skill submit` 启动 OpenCode session，session 自动完成 archive + snapshot → sanity check → digest → 外部富集 → Antigravity 成文 → dashboard → 邮件
- **成功时**: 发 digest 邮件（解读全文），收件人由 `ORDS_NOTIFY_EMAIL` 环境变量指定
- **失败时**: session 自行调查、尝试修复，不论修复成功与否都发失败说明邮件
- **dry run 时**: 与正常模式一样发邮件（验证邮件通道）

## 非目标

- 不接外部网络服务——数据只存本地 SQLite
- 不做实时查询——数据每周快照一次
- 不做多人协作——单用户、单机部署

## 成功标准

1. `ords discover --top 20` 正确返回 Top-20 模型
2. `ords archive --top 20` 两层抓取去重写入 SQLite
3. `ords snapshot` 抓取所有快照+图表端点存入 SQLite
4. `ords digest --top 20` 产出确定性 packet（md + json），且对混批日、partial day、结构性 0 的处理符合 schema 契约
5. 每周五 09:00 自动触发，session 完成抓取 → digest → 富集 → 成文全流程
6. 每周收到 digest 邮件；失败时收到失败说明
7. Unit test + integration test 在 GitHub Actions CI 中通过
8. Live test 需 `ORDS_ENABLE_LIVE_TESTS=1` 才执行

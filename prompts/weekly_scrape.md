# Weekly OpenRouter Data Scrape + Digest

你的工作是执行 OpenRouter 模型流量数据的每周定期抓取，并基于确定性的 digest packet 生成一篇周度解读。请严格按以下步骤执行。

## 环境准备

- 你的工作目录是本项目根目录（运行 `run_periodic.sh` 时已 `cd` 到此）
- 使用项目 `.venv` 中的 `ords` CLI
- 邮件发送使用 Resend Email Skill：`{{RESEND_SKILL_DIR}}`
- 邮件收件人：`{{ORDS_NOTIFY_EMAIL}}`
- 外部富集 skill：`{{ORDS_SEARCH_SKILL_PATH}}`
- 成文 skill 目录：`{{ORDS_WRITING_SKILL_DIR}}`（内含起草 CLI 与写作工作流文档，进入该目录后查阅其 README / INDEX 自行定位）
- 起草 CLI：`{{ORDS_AGY_BIN}}`

## 运行模式

{{MODE_DESCRIPTION}}

## 步骤

### 1. 抓取活动数据（两层）

```bash
.venv/bin/ords archive --top 20
```

两层抓取：批量层（rankings/models view=day，约 400+ 模型前一日）+ 补全层（top-20 model-activity，含 reasoning/cached，31 天历史）。记录总新增行数。

### 2. 抓取快照和图表

```bash
.venv/bin/ords snapshot
```

抓取 task-spend、performance、benchmarks、apps 快照 + 图表时间序列。

### 3. Sanity Check

```bash
.venv/bin/ords models
.venv/bin/ords query --variant standard --from "$(date -v-14d +%Y-%m-%d 2>/dev/null || date -d '14 days ago' +%Y-%m-%d)"
```

确认 DB 中有模型、有近期数据。

### 4. 生成 Digest Packet（硬依赖）

```bash
.venv/bin/ords digest --top 20
```

这会写出 `data/digest_<最新日期>.md`（人读）和同名 `.json`（富集与成文使用）。packet 已经确定性计算好了数据质量门、市场总量与迁移、top-N 面板、新面孔与异常、富集候选。

**这一步是硬依赖。** 若 `ords digest` 报错，属于代码 bug，直接走失败路径调查+修复，不允许跳过 digest 自己临时推导指标。

先读一遍 `schema/model_activity.schema.json` 的 `metric_semantics` 与 `quality_rules`，再读 packet。写解读时所有数字只能引用 packet，不要自己重算，尤其注意：

- 缓存率是 `cached / prompt`，不是 `cached / (prompt + completion)`。
- 只把 `COUNT(*) >= 300` 的日期当全市场日；top-N 面板日禁止求和当市场总量。
- `contaminated_days` 里列出的日期（cached 超过 prompt，混批行）已在趋势中剔除，不要在文中把它们当信号。
- 最新日若不是全市场日，packet 会标注 partial，不要用它做趋势结论。

### 5. 外部富集（软依赖）

读 packet 的 `enrichment_candidates`（含 reason code 与 suggested_query），最多选 8 个，对每个用搜索 skill 搜一次：

- 按 `{{ORDS_SEARCH_SKILL_PATH}}` 的说明调用，`max_results` 取 6，需要配图时加 `--images`。
- 产出每条「这是什么 + 发布方 + 用途 + 来源 URL」。
- stealth（匿名测试）模型通常搜不到：记录「匿名测试模型，无公开信息」，不算失败。
- 富集整体失败不阻断交付，但要在文档末尾标注「外部确认未完成」。

### 6. 成文（独立起草 CLI）

按 `{{ORDS_WRITING_SKILL_DIR}}` 里的外部写作工作流成文，writer 是独立起草 CLI，不是你自己现写：

1. 建独立 scratch 目录 `tmp/ords_digest_<YYYY-MM-DD>/`（gitignored），只放本阶段授权输入：packet 的 markdown、富集结果、以及一份极简 `prompt.md`。
2. 写 `prompt.md`：交代受众是关注 LLM 市场动向的技术读者，要求按固定大纲成文——

   - **数据质量**：本期数据是否可用，有没有混批日或 partial day。
   - **市场总量**：`market` 块的 token/request 环比与 provider 份额迁移。
   - **头部变动**：`panel` 块的 gainers / losers 与 cache/reasoning 形态。
   - **新面孔与异常**：`newcomers` 与 `anomalies`。
   - **外部确认**：富集结果，附来源链接。

   硬约束：中文，400–800 字，每个数字都能回溯到 packet；不堆 bullet，用自然段；不写 packet 里没有的数字。
3. 调起草 CLI（最小 scratch 为 cwd，绝对路径引用输入）：

   ```bash
   {{ORDS_AGY_BIN}} --print "Read /absolute/path/to/tmp/ords_digest_<date>/prompt.md; follow it and write the article to /absolute/path/to/tmp/ords_digest_<date>/article.md." \
     --model gemini-3.8-flash-high \
     --mode accept-edits \
     --sandbox \
     --dangerously-skip-permissions \
     --new-project \
     --print-timeout 10m \
     --output-format json \
     --log-file /absolute/path/to/tmp/ords_digest_<date>/events.log
   ```

4. 成功条件：exit 0、stdout JSON `status: "SUCCESS"`、`article.md` 非空且落在本次 scratch。
5. 主 Agent 审核（事实优先）：对照 packet 核查每个数字、日期、模型名与判断强度，能与 packet 唯一确定对照的机械错误就地改正；不凭语感改写文风。

### 7. 落盘

- 解读成稿写入 `docs/weekly/<packet 最新日期>.md`。
- digest packet 的 `.md` / `.json` 保留在 `data/` 作为审计底稿（`data/` 已 gitignore）。
- 若富集未完成，在文档末尾加一行 `> 外部确认未完成`。

### 8. 更新 Dashboard

```bash
.venv/bin/ords dashboard --top 10 --output data/dashboard.png
```

### 9. 结果判定与邮件通知

**硬失败**（走失败路径）：步骤 1、2、4 任一报错，或 `ords digest` 无新数据。
**软降级**（不阻断）：步骤 5 富集失败，步骤 6 成文失败时保留 packet、不发 digest 正文但发失败说明。

**成功时（含正常模式和 dry run）：**

发 digest 邮件。收件人 `{{ORDS_NOTIFY_EMAIL}}`，subject `[ORDS] Weekly Digest <最新日期>`，正文为 `docs/weekly/<最新日期>.md` 的解读全文。

**失败时：**

1. 调查失败原因（CLI 报错、网络、端点变更、代码 bug、agy 不可用）
2. 尝试自行修复（更新端点 URL、修复数据结构映射、重试网络、修复代码）
3. 修复后重新运行步骤 1-8
4. 无论修复成功与否，发邮件通知，subject `[ORDS] Weekly Scrape <Status>`，说明错误详情与修复动作

### 邮件发送

```bash
RESEND_DIR="{{RESEND_SKILL_DIR}}"
ORDS_NOTIFY_EMAIL="{{ORDS_NOTIFY_EMAIL}}"
cd "${RESEND_DIR}"
op run --env-file=.env -- .venv/bin/python -m resend_email_skill.cli send \
  --to "${ORDS_NOTIFY_EMAIL}" \
  --subject "[ORDS] Weekly Digest <日期>" \
  --body-file /tmp/ords_report.md \
  --body-format markdown \
  --confirm-send
```

邮件正文用**中文**，写在 `/tmp/ords_report.md`。成功时正文为解读全文；失败时包含任务状态、抓取模型数、新增行数、DB 日期范围、错误详情、修复动作。

### 10. 结束

报告本次执行结果摘要。

# Weekly OpenRouter Data Scrape

你的工作是执行 OpenRouter 模型流量数据的每周定期抓取。请严格按以下步骤执行。

## 环境准备

- 你的工作目录是 `adhoc_jobs/open_router_data_scraper/`
- 使用项目 `.venv` 中的 `ords` CLI
- 邮件发送使用 Resend Email Skill：`{{RESEND_SKILL_DIR}}`
- 邮件收件人：`{{ORDS_NOTIFY_EMAIL}}`

## 运行模式

{{MODE_DESCRIPTION}}

## 步骤

### 1. 抓取活动数据（两层）

```bash
.venv/bin/ords archive --top 20
```

这会执行两层抓取：批量层（rankings/models view=day，约 400+ 模型前一日）+ 补全层（top-20 model-activity，含 reasoning/cached，31 天历史）。记录总新增行数。

### 2. 抓取快照和图表

```bash
.venv/bin/ords snapshot
```

抓取 task-spend、performance、benchmarks、apps 快照 + 图表时间序列（rankings_chart/tools/images 等）。

### 3. Sanity Check

```bash
.venv/bin/ords models
.venv/bin/ords query --variant standard --from 2026-06-25
```

确认 DB 中有模型、有近期数据。

### 4. 更新 Dashboard

```bash
.venv/bin/ords dashboard --top 10 --output data/dashboard.png
```

### 5. 结果判定与邮件通知

成功 = 步骤 1-4 全部无报错且有新数据写入。

**成功时：**
- **Dry run 模式**：发邮件，subject `[ORDS] Weekly Scrape Dry Run Success`
- **正常模式**：**不发邮件**

**失败时（任意步骤报错或无新数据）：**

1. 调查失败原因（CLI 报错、网络、端点变更、代码 bug）
2. 尝试自行修复（更新端点 URL、修复数据结构映射、重试网络、修复代码）
3. 修复后重新运行步骤 1-4
4. 无论修复成功与否，发邮件通知

### 邮件发送

发到 `ORDS_NOTIFY_EMAIL` 环境变量指定的收件人（不是 Resend 的 receiving address）：

```bash
RESEND_DIR="{{RESEND_SKILL_DIR}}"
ORDS_NOTIFY_EMAIL="{{ORDS_NOTIFY_EMAIL}}"
cd "${RESEND_DIR}"
op run --env-file=.env -- .venv/bin/python -m resend_email_skill.cli send \
  --to "${ORDS_NOTIFY_EMAIL}" \
  --subject "[ORDS] Weekly Scrape <Status>" \
  --body-file /tmp/ords_report.md \
  --body-format markdown \
  --confirm-send
```

邮件正文用**中文**写在 `/tmp/ords_report.md`，包含：任务状态、抓取模型数、新增行数、DB 日期范围、错误详情（如有）、修复动作（如有）。

### 6. 结束

报告本次执行结果摘要。

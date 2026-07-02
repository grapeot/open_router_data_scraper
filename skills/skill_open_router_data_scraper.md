# OpenRouter Data Scraper Skill

## 元数据

- **类型**: API Guide
- **适用场景**: 需要获取 OpenRouter 模型流量数据（token 用量、请求数、排名、benchmark、performance）、定期抓取并存入本地 SQLite 突破 31 天 trailing window 时
- **创建日期**: 2026-07-02

## 目标

通过逆向 OpenRouter 前端 stats 端点，按天/周粒度抓取模型活动数据，存入本地 SQLite 去重累积，突破 OpenRouter 31 天 trailing window 限制，长期构建 LLM 市场流量历史时间序列。

## 可用资源

- **CLI**: `.venv/bin/ords`，工作目录 `adhoc_jobs/open_router_data_scraper/`
- **Python 环境**: `uv venv .venv && uv pip install -e '.[viz,dev]'`
- **数据端点**: 所有端点无需鉴权，仅需 `Referer: https://openrouter.ai/`。详见 `docs/rfc.md` 端点清单
- **SQLite**: `data/ords.db`（.gitignore），schema 详见 `src/open_router_data_scraper/store.py`

## CLI 命令

```bash
ords discover --top 20               # Top-N 模型列表
ords fetch <slug>                    # 抓取单模型逐日用量并打印
ords archive --top 20                # 两层抓取存入 SQLite（批量 + 补全）
ords snapshot                        # 快照 + 图表时间序列存入 SQLite
ords query --slug <slug>             # 从 DB 查询历史
ords models                          # 列出 DB 中已追踪模型
ords dashboard --top 10              # 生成 PNG 图表
```

## 两层抓取策略

`ords archive` 分两层，总 21 次请求：
1. **批量层**: `rankings/models?view=month` — 1 次请求，400+ 模型 17 天数据（reasoning/cached 为 0）
2. **补全层**: Top-20 逐个 `model-activity` — 20 次请求，补全 reasoning/cached + 当天数据

重复行通过 PK `(variant_permaslug, date, variant)` + `INSERT OR IGNORE` 去重。每周一次不会漏数据（17 天窗口 > 7 天间隔）。

## 验收标准

1. `ords archive --top 20` 执行后 SQLite 中有 400+ 模型的活动数据
2. `ords snapshot` 执行后 SQLite 中有图表时间序列 + 快照数据
3. 重复执行 `ords archive` 不产生重复行
4. `ords dashboard` 生成有效 PNG
5. `pytest tests/ --ignore=tests/test_live.py` 全部通过
6. `ORDS_ENABLE_LIVE_TESTS=1 pytest tests/test_live.py` 对真实 API 验证通过

## 已知陷阱

| 陷阱 | 表现 | 应对 |
|------|------|------|
| 短 slug 不被 model-activity 接受 | 返回空数组 | CLI 自动解析 canonical_slug（末尾 8 位日期）；手动传时需用 canonical_slug |
| rankings/models 的 reasoning/cached 为 0 | 补全层数据与批量层不一致 | 补全层（model-activity）覆盖补全，PK 去重保证只保留有值的版本 |
| OpenCode submit 阻塞 | `--send-timeout 300` 导致脚本等 5 分钟 | 用 `--send-timeout 5`，session 已创建即可 |
| 邮件发到 Resend receiving address | 邮件到了 `@example.resend.app` 而非用户邮箱 | `--to` 硬编码用户实际收件箱（Outlook），正文用中文 |
| rankings/models?view=month 返回累积值 | 数字比实际大 12 倍 | 用 `view=day` 拿当天实际值，不用 view=month/week |
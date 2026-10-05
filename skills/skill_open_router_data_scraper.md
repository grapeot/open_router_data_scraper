# OpenRouter Data Scraper Skill

## 元数据

- **类型**: API Guide
- **适用场景**: 需要获取 OpenRouter 模型流量数据（token 用量、请求数、排名、benchmark、performance）、定期抓取并存入本地 SQLite 突破 31 天 trailing window 时
- **创建日期**: 2026-07-02

## 目标

通过逆向 OpenRouter 前端 stats 端点，按天/周粒度抓取模型活动数据，存入本地 SQLite 去重累积，突破 OpenRouter 31 天 trailing window 限制，长期构建 LLM 市场流量历史时间序列。

## 可用资源

- **CLI**: `.venv/bin/ords`，工作目录为本项目根目录
- **Python 环境**: `uv venv .venv && uv pip install -e '.[viz,dev]'`
- **数据端点**: 所有端点无需鉴权，仅需 `Referer: https://openrouter.ai/`。详见 `docs/rfc.md` 端点清单
- **SQLite**: `data/ords.db`（.gitignore），schema 详见 `src/open_router_data_scraper/store.py`

## CLI 命令

```bash
ords discover --top 20               # Top-N 模型列表
ords fetch <slug>                    # 抓取单模型逐日用量并打印
ords archive --top 20                # 两层抓取存入 SQLite（批量 + 补全）
ords snapshot                        # 快照 + 图表时间序列存入 SQLite
ords digest --top 20                  # 计算确定性周度 packet（data/digest_<date>.md + .json）
ords query --slug <slug>             # 从 DB 查询历史
ords models                          # 列出 DB 中已追踪模型
ords dashboard --top 10              # 生成 PNG 图表
```

## 周度解读（digest）

`ords digest` 是「算」：从 SQLite 确定性产出 packet，含数据质量门（partial day / 混批日 / 结构性 0）、市场总量与 provider 份额、top-N 固定 cohort、新面孔与形态异常、富集候选。它是后续成文的唯一数字来源。

关键口径（都已被测试锁定，也是踩过坑的教训）：缓存率是 `cached/prompt` 不是 `/total`；只有 `COUNT(*)>=300` 且非混批的日期算「干净全市场日」，市场环比只用这些天；聚合 cached 超过 prompt 的日期是混批行，必须从面板和市场趋势一并剔除（如 09-25）；reasoning 率用同窗口的 `reasoning/completion`。下游 agent 读 packet 成文，不自行重算指标。

## 两层抓取策略

`ords archive` 分两层，总 21 次请求：
1. **批量层**: `rankings/models?view=day` — 1 次请求，400+ 模型前一日数据（reasoning/cached 为 0）
2. **补全层**: Top-20 逐个 `model-activity` — 20 次请求，补全 reasoning/cached + 当天数据

重复行通过 PK `(variant_permaslug, date, variant)` 去重；补全层在冲突时刷新完整 telemetry。补全层 31 天窗口保证 Top-20 日级连续，全市场批量层是每周横截面。分析规则见 `schema/model_activity.schema.json`。

## 验收标准

1. `ords archive --top 20` 执行后 SQLite 中有 400+ 模型的活动数据
2. `ords snapshot` 执行后 SQLite 中有图表时间序列 + 快照数据
3. `ords digest --top 20` 产出 `data/digest_<date>.md` 与 `.json`，且质量门与口径符合 schema 契约（pytest tests/test_digest.py）
4. 重复执行 `ords archive` 不产生重复行
5. `ords dashboard` 生成有效 PNG
6. 空库时 `ords digest` 打印清晰错误并返回 1，不抛 traceback
5. `pytest tests/ --ignore=tests/test_live.py` 全部通过
6. `ORDS_ENABLE_LIVE_TESTS=1 pytest tests/test_live.py` 对真实 API 验证通过

## 已知陷阱

| 陷阱 | 表现 | 应对 |
|------|------|------|
| 短 slug 不被 model-activity 接受 | 返回空数组 | CLI 自动解析 canonical_slug（末尾 8 位日期）；手动传时需用 canonical_slug |
| rankings/models 的 reasoning/cached 为 0 | 直接分析会把缺失 telemetry 当真实 0 | 补全层在主键冲突时更新；历史数据仍按 `schema/model_activity.schema.json` 识别结构性 0 |
| OpenCode submit 阻塞 | `--send-timeout 300` 导致脚本等 5 分钟 | 用 `--send-timeout 5`，session 已创建即可 |
| 邮件发到 Resend receiving address | 邮件到了 `@example.resend.app` 而非用户邮箱 | `--to` 用 `ORDS_NOTIFY_EMAIL` 环境变量，正文用中文 |
| rankings/models?view=month 返回累积值 | 数字比实际大 12 倍 | 用 `view=day` 拿当天实际值，不用 view=month/week |

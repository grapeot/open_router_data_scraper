# AGENTS.md — open_router_data_scraper

## 项目概述

定期抓取 OpenRouter 模型活动数据（token 用量、请求数等）的工具。通过逆向 OpenRouter 前端 stats 端点获取按天粒度的模型流量数据，存入本地 SQLite，突破 OpenRouter 31 天 trailing window 的限制，长期累积历史时间序列。

## 项目结构

```
open_router_data_scraper/
├── pyproject.toml
├── AGENTS.md
├── README.md
├── docs/
│   └── deployment.md          # 部署方案讨论
├── src/open_router_data_scraper/
│   ├── __init__.py
│   ├── client.py               # HTTP client（4 个端点封装）
│   ├── store.py                # SQLite 存储层 + 去重
│   └── cli.py                  # CLI 入口 (ords)
├── data/                       # SQLite DB + dashboard PNG（.gitignore）
└── tests/
```

## 开发规则

- Python: uv 管理 venv，`.venv/` 在项目根目录
- 安装: `uv pip install -e '.[viz,dev]'`
- 测试: `.venv/bin/python -m pytest tests/ -v`
- CLI 测试: `.venv/bin/ords <command>`

## CLI 命令

```bash
ords discover --top 20                    # 列出 Top-N 模型
ords fetch z-ai/glm-5.2                   # 抓取单模型用量并打印
ords archive --top 20                     # 抓取 Top-N 并存入 SQLite（去重）
ords query --slug glm-5.2                 # 从 DB 查询历史数据
ords models                              # 列出 DB 中已追踪的模型
ords dashboard --slug glm-5.2             # 生成 PNG 图表
```

## 技术细节

- 所有 OpenRouter 端点无需鉴权，仅需 `Referer: https://openrouter.ai/`
- `permaslug` 必须是 canonical_slug（带日期后缀，如 `z-ai/glm-5.2-20260616`），CLI 自动解析
- SQLite PK = `(variant_permaslug, date, variant)`；批量层去重，补全层更新同主键的 enrichment telemetry
- 服务端有缓存（`cachedAt` 字段），短时间内重复请求结果一致

"""OpenRouter model activity data scraper.

提供三个核心功能：
  - discover:  从 rankings chart 获取 Top-N 模型列表
  - fetch:     抓取指定模型的逐日 token / request 用量
  - archive:   去重写入本地 SQLite，突破 31 天 trailing window

CLI 入口: `ords`
"""

__version__ = "0.1.0"
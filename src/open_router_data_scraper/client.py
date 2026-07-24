"""HTTP client for OpenRouter's undocumented frontend stats endpoints.

端点分两类：
  - Rolling data（按天/周累积，按主键合并）：
    - /api/frontend/v1/rankings/models?view=day    — 全模型前一日活动（批量层）
    - /api/frontend/v1/stats/model-activity       — 单模型逐日活动（补全层，含 reasoning/cached）
    - /api/frontend/v1/rankings/model-rankings-chart — Top-10 模型周度排名 52 周
    - /api/frontend/v1/rankings/tools              — tool call 用量时间序列
    - /api/frontend/v1/rankings/natural-language   — 按自然语言的用量时间序列
    - /api/frontend/v1/rankings/programming-language — 按编程语言的用量时间序列
    - /api/frontend/v1/rankings/modality-chart      — 按 modality 的请求量时间序列
    - /api/frontend/v1/rankings/context-length      — 按 context length bucket 的用量
    - /api/frontend/v1/rankings/images              — 图片生成用量时间序列

  - Snapshot data（每周快照，无时间序列，存 snapshot_date）：
    - /api/frontend/v1/rankings/task-spend   — 30 天 spend 份额
    - /api/frontend/v1/rankings/performance   — 模型 latency/throughput
    - /api/frontend/v1/rankings/benchmarks    — benchmark 分数
    - /api/frontend/v1/rankings/apps          — app 排名（day/week/month）

所有端点无需鉴权，仅需 Referer header。服务端有缓存（cachedAt 字段）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://openrouter.ai"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": BASE_URL,
    "Accept": "*/*",
}
TIMEOUT = 30.0


@dataclass
class ActivityRow:
    """model-activity 和 rankings/models 共用的逐日活动记录。"""
    date: str
    model_permaslug: str
    variant: str
    total_prompt_tokens: int
    total_completion_tokens: int
    total_native_tokens_reasoning: int
    total_native_tokens_cached: int
    count: int
    total_tool_calls: int
    requests_with_tool_call_errors: int
    num_media_prompt: int
    num_media_completion: int
    image_output_requests: int
    num_video_prompt: int
    video_output_seconds: int
    rerank_documents: int
    stt_transcript_characters: int
    num_audio_prompt: int
    variant_permaslug: str

    @classmethod
    def from_dict(cls, r: dict) -> "ActivityRow":
        return cls(
            date=r["date"],
            model_permaslug=r["model_permaslug"],
            variant=r["variant"],
            total_prompt_tokens=r["total_prompt_tokens"],
            total_completion_tokens=r["total_completion_tokens"],
            total_native_tokens_reasoning=r.get("total_native_tokens_reasoning", 0),
            total_native_tokens_cached=r.get("total_native_tokens_cached", 0),
            count=r["count"],
            total_tool_calls=r.get("total_tool_calls", 0),
            requests_with_tool_call_errors=r.get("requests_with_tool_call_errors", 0),
            num_media_prompt=r.get("num_media_prompt", 0),
            num_media_completion=r.get("num_media_completion", 0),
            image_output_requests=r.get("image_output_requests", 0),
            num_video_prompt=r.get("num_video_prompt", 0),
            video_output_seconds=r.get("video_output_seconds", 0),
            rerank_documents=r.get("rerank_documents", 0),
            stt_transcript_characters=r.get("stt_transcript_characters", 0),
            num_audio_prompt=r.get("num_audio_prompt", 0),
            variant_permaslug=r.get("variant_permaslug", r["model_permaslug"]),
        )


@dataclass
class ChartPoint:
    """时间序列端点的单个数据点。"""
    date: str
    values: dict[str, int]  # {slug_or_label: token_count}


class OpenRouterClient:
    """轻量 HTTP client，封装所有 rankings/stats 端点。"""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self._client = httpx.Client(
            base_url=base_url, headers=HEADERS, timeout=TIMEOUT
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OpenRouterClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _get(self, path: str, **params: Any) -> Any:
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    # ── Rolling: 活动数据 ──────────────────────────────────────────

    def get_rankings_models(self, view: str = "day") -> list[ActivityRow]:
        """批量层：一次请求获取全模型活动数据。

        ⚠️ view=month/week 返回的是滚动窗口累积值，不是单日值！
        view=day 返回当天实际值（393 模型），与 model-activity 单日数据一致。
        view=week 返回 ~7 天累积，view=month 返回 ~17 天累积。
        用 view=day 做批量层，每周累积 7 天。
        """
        data = self._get("/api/frontend/v1/rankings/models", view=view)
        return [ActivityRow.from_dict(r) for r in data.get("data", [])]

    def get_model_activity(
        self, permaslug: str, variant: str = "standard"
    ) -> list[ActivityRow]:
        """补全层：单模型逐日活动，含 reasoning/cached 字段。

        permaslug 可以是短 slug（自动解析为 canonical_slug）。
        """
        if not self._is_canonical(permaslug):
            permaslug = self.resolve_canonical_slug(permaslug)
        data = self._get(
            "/api/frontend/v1/stats/model-activity",
            permaslug=permaslug, variant=variant,
        )
        return [ActivityRow.from_dict(r) for r in data.get("data", {}).get("analytics", [])]

    def resolve_canonical_slug(self, slug: str) -> str:
        if self._is_canonical(slug):
            return slug
        data = self._get("/api/v1/models")
        for m in data.get("data", []):
            if m.get("id") == slug:
                return m.get("canonical_slug") or slug
        return slug

    @staticmethod
    def _is_canonical(slug: str) -> bool:
        parts = slug.rsplit("-", 1)
        return len(parts) == 2 and len(parts[1]) == 8 and parts[1].isdigit()

    # ── Rolling: 时间序列图表 ──────────────────────────────────────

    def get_rankings_chart(self) -> list[ChartPoint]:
        """Top-10 模型周度排名，52 周。"""
        return self._chart("/api/frontend/v1/rankings/model-rankings-chart")

    def get_tools_chart(self) -> list[ChartPoint]:
        return self._chart("/api/frontend/v1/rankings/tools")

    def get_images_chart(self) -> list[ChartPoint]:
        return self._chart("/api/frontend/v1/rankings/images")

    def get_modality_chart(self, route_segment: str = "text") -> list[ChartPoint]:
        return self._chart(
            "/api/frontend/v1/rankings/modality-chart", routeSegment=route_segment
        )

    def get_natural_language_chart(self, tag: str = "English") -> list[ChartPoint]:
        return self._chart("/api/frontend/v1/rankings/natural-language", tag=tag)

    def get_programming_language_chart(self, tag: str = "Python") -> list[ChartPoint]:
        return self._chart("/api/frontend/v1/rankings/programming-language", tag=tag)

    def get_context_length_chart(self, bucket: str = "10K") -> list[ChartPoint]:
        return self._chart("/api/frontend/v1/rankings/context-length", bucket=bucket)

    def _chart(self, path: str, **params: Any) -> list[ChartPoint]:
        data = self._get(path, **params)
        points = data.get("data", [])
        if isinstance(points, dict):
            points = points.get("data", [])
        return [
            ChartPoint(date=p["x"], values=p["ys"])
            for p in points
            if isinstance(p, dict) and "x" in p and "ys" in p
        ]

    # ── Snapshot: 每周快照 ─────────────────────────────────────────

    def get_task_spend(self) -> dict:
        """30 天 spend 份额（按用例分类）。"""
        return self._get("/api/frontend/v1/rankings/task-spend")

    def get_performance(self) -> list[dict]:
        """模型 latency/throughput 快照。"""
        return self._get("/api/frontend/v1/rankings/performance").get("data", [])

    def get_benchmarks(self) -> dict:
        """benchmark 分数快照。"""
        return self._get("/api/frontend/v1/rankings/benchmarks")

    def get_apps_ranking(self) -> dict:
        """app 排名快照（含 day/week/month 三个时间窗）。"""
        return self._get("/api/frontend/v1/rankings/apps")

    # ── 工具：模型列表 ─────────────────────────────────────────────

    def get_top_models(self, limit: int = 20) -> list[dict]:
        """按 weekly token 排序的 Top-N 模型卡片列表。"""
        data = self._get(
            "/api/frontend/v1/models/find",
            active="true", fmt="cards", order="top-weekly",
        )
        return data.get("data", {}).get("models", [])[:limit]

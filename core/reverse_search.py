"""以图搜图：SerpApi(google_lens) 反向检索 + 结果格式化。

只使用 Google Lens（SerpApi）一种引擎，多 Key 轮询由 SerpApiClient 处理。
结果不主动发送卡片，统一以结构化 JSON 返回，交由 LLM 自行展示。
"""

from __future__ import annotations

from dataclasses import dataclass

from astrbot.api import logger

from .serpapi_client import SerpApiClient


@dataclass
class SearchResultItem:
    """单个以图搜图结果项。"""

    title: str
    url: str
    source: str = ""


async def google_lens_search(
    client: SerpApiClient,
    image_url: str,
    max_results: int,
    hl: str,
) -> list[SearchResultItem]:
    """通过 SerpApi google_lens 引擎做反向图片检索。

    Args:
        client: 共享的 SerpApiClient（含多 Key 轮询）。
        image_url: 待检索图片的公网 URL。
        max_results: 最多返回的结果数。
        hl: 界面语言。

    Returns:
        结果列表。SerpApi 出错时由 client 抛出 SerpApiError。
    """
    if not image_url.startswith(("http://", "https://")):
        logger.warning("[serpapi_imgsearch] google_lens 仅支持公网 URL 图片")
        return []

    data = await client.get("google_lens", {"url": image_url, "hl": hl})

    results: list[SearchResultItem] = []
    for match in (data.get("visual_matches") or [])[: max(1, max_results)]:
        title = match.get("title", "")
        link = match.get("link", "")
        if not title or not link:
            continue
        results.append(
            SearchResultItem(
                title=title,
                url=link,
                source=match.get("source", ""),
            )
        )

    logger.info(f"[serpapi_imgsearch] google_lens 返回 {len(results)} 条结果")
    return results


def build_llm_payload(items: list[SearchResultItem]) -> dict:
    """构建返回给 LLM 的结构化结果（不主动发送，交由 LLM 展示）。"""
    items_data = [
        {
            "index": idx,
            "title": item.title,
            "url": item.url,
            "source": item.source,
        }
        for idx, item in enumerate(items, start=1)
    ]
    instruction = (
        "请用纯文本向用户展示以下结果（标题 + 来源 + 完整链接），并结合你对图片本身的视觉辨识"
        "交叉印证，给出最终可能的作者 / 作品名 / 出处。请勿使用 Markdown 链接语法，直接输出完整 URL。"
    )
    return {
        "success": True,
        "count": len(items),
        "items": items_data,
        "instruction": instruction,
    }

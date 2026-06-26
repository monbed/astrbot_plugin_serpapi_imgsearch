"""AstrBot SerpApi 搜图插件：仅注册两个 LLM 函数工具。

- ``pic_search``：文字搜图，SerpApi(google_images) 抓候选图 → VLM 淘汰赛选优 → 直接发图。
- ``reverse_image_search``：以图搜图，取消息中的图 → 转公网 URL → google_lens 反向检索。

不注册指令、不做被动监听；错误统一以 ``error: ...`` 字符串返回给 LLM，不抛出。
"""

from __future__ import annotations

import json
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star

from .core.composer import download_image
from .core.forward_search import fetch_image_urls, run_tournament
from .core.image_utils import HttpService, extract_image_from_event
from .core.reverse_search import (
    build_llm_payload,
    google_lens_search,
)
from .core.serpapi_client import SerpApiClient, SerpApiError


class SerpApiImageSearchPlugin(Star):
    """SerpApi 搜图 / 以图搜图工具集（仅 llm_tool 触发）。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = self._config_to_dict(config)

        # 网络配置（代理 / UA / 权限开关）封装进 HttpService，随插件实例持有
        network = self._get_nested("network", default={}) or {}
        self.http = HttpService(
            proxy_url=network.get("proxy_url", ""),
            user_agent=network.get("user_agent", ""),
            allow_image_upload=network.get("allow_image_upload", True),
        )

        # 共享 SerpApi 客户端（搜图与以图搜图共用）
        keys = self._get_nested("api_keys", "serpapi_keys", default=[])
        if isinstance(keys, str):
            keys = [keys]
        elif not isinstance(keys, list):
            keys = []
        self.client = SerpApiClient(keys, self.http)

        # 文字搜图配置
        self.vlm_provider_id = str(self.config.get("vlm_provider_id") or "").strip()
        # 拼图为 4 列网格，batch_size 过大会生成超大图片，钳制上限
        self.batch_size = min(self._safe_int(self.config.get("batch_size"), 16), 64)
        self.default_scrape_count = self._safe_int(
            self.config.get("default_scrape_count"), 16
        )

        # 以图搜图配置
        self.max_results = self._safe_int(self.config.get("max_results"), 5)

        # 搜索地区/语言（地区默认美国，界面语言默认 zh-cn）
        self.gl = str(self._get_nested("region", "gl", default="us") or "us").strip()
        self.hl = str(self._get_nested("region", "hl", default="zh-cn") or "zh-cn").strip()

        if not self.client.has_keys():
            logger.warning(
                "[serpapi_imgsearch] 未配置 SerpApi Key（api_keys.serpapi_keys），搜图功能不可用。"
            )

    # ------------------------------------------------------------------ #
    # 配置辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _config_to_dict(config: Any) -> dict:
        """把 AstrBotConfig 转为普通 dict（AstrBotConfig 本身即 dict 子类）。"""
        try:
            return dict(config)
        except (TypeError, ValueError):
            logger.warning("[serpapi_imgsearch] 无法解析配置对象，使用空配置")
            return {}

    def _get_nested(self, *keys: str, default: Any = None) -> Any:
        """读取嵌套配置值。"""
        value: Any = self.config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
            if value is None:
                return default
        return value

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            n = int(value)
            return n if n > 0 else default
        except (TypeError, ValueError):
            return default

    def _get_vlm_provider(self, event: AstrMessageEvent):
        """获取用于选图的视觉模型 Provider。

        优先用配置的 vlm_provider_id，否则用当前会话的对话模型（需支持图片输入）。
        """
        if self.vlm_provider_id:
            provider = self.context.get_provider_by_id(self.vlm_provider_id)
            if provider:
                return provider
            logger.warning(
                f"[serpapi_imgsearch] 未找到 ID 为 '{self.vlm_provider_id}' 的 Provider，"
                "回退到当前会话默认模型。"
            )
        return self.context.get_using_provider(umo=event.unified_msg_origin)

    # ------------------------------------------------------------------ #
    # LLM 工具 1：文字搜图并发图
    # ------------------------------------------------------------------ #
    @filter.llm_tool(name="pic_search")
    async def pic_search(self, event: AstrMessageEvent, query: str = "") -> str:
        """根据关键词搜索一张最匹配的图片，并直接发送给用户。

        Args:
            query(string): 要搜索的图片关键词。
        """
        if not self.client.has_keys():
            return (
                "error: 未配置 SerpApi API Key。请在 AstrBot 管理面板的本插件配置 "
                "api_keys.serpapi_keys 中填写后重试。"
            )

        query = (query or "").strip()
        if not query:
            return "error: query（搜索关键词）不能为空。"

        # 抓取数量完全由面板配置决定（仅做安全上限钳制），不接受 LLM 指定
        scrape_count = max(1, min(self.default_scrape_count, 200))

        vlm_provider = self._get_vlm_provider(event)
        if not vlm_provider:
            return (
                "error: 未找到可用的视觉模型(VLM)。请在插件配置 vlm_provider_id 指定一个"
                "支持图片输入的模型，或为当前会话配置多模态模型。"
            )

        try:
            image_urls = await fetch_image_urls(
                self.client, query, scrape_count, self.hl, self.gl
            )
        except SerpApiError as e:
            return f"error: 搜索图片失败：{e}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"[serpapi_imgsearch] google_images 抓取异常: {e}", exc_info=True)
            return f"error: 搜索图片失败：{e}"

        if not image_urls:
            return f"未找到与「{query}」相关的图片，建议调整关键词后重试。"

        try:
            winner_url = await run_tournament(
                image_urls, query, vlm_provider, self.http, self.batch_size
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[serpapi_imgsearch] VLM 淘汰赛异常: {e}", exc_info=True)
            return f"error: 筛选图片时出错：{e}"

        if not winner_url:
            return "筛选过程没有产生最终结果，请调整描述或关键词后重试。"

        final_bytes = await download_image(winner_url, self.http)
        if not final_bytes:
            return f"无法下载最终选定的图片：{winner_url}"

        try:
            await event.send(event.chain_result([Image.fromBytes(final_bytes)]))
        except Exception as e:  # noqa: BLE001
            logger.error(f"[serpapi_imgsearch] 发送图片失败: {e}", exc_info=True)
            return f"error: 发送图片失败：{e}"

        return "已成功为用户找到并发送了最匹配的图片。"

    # ------------------------------------------------------------------ #
    # LLM 工具 2：以图搜图
    # ------------------------------------------------------------------ #
    @filter.llm_tool(name="reverse_image_search")
    async def reverse_image_search(
        self, event: AstrMessageEvent, image_url: str = ""
    ) -> str:
        """反向检索图片的出处、来源、作者或所属作品。

        Args:
            image_url(string): 可选。待检索图片的公网 http/https 直链；留空则自动取当前或被引用消息中的图片。
        """
        if not self.client.has_keys():
            return (
                "error: 未配置 SerpApi API Key。请在 AstrBot 管理面板的本插件配置 "
                "api_keys.serpapi_keys 中填写后重试。"
            )

        image_url = (image_url or "").strip()
        if image_url.startswith(("http://", "https://")):
            http_url = image_url
        else:
            image_comp = extract_image_from_event(event)
            if image_comp is None:
                return (
                    "error: 未在消息中找到图片。请提示用户直接发送一张图片，"
                    "或引用一条包含图片的消息后再试。"
                )
            http_url = await self.http.get_public_url_for_image(image_comp)
            if not http_url:
                return (
                    "error: 无法获取图片的公网链接（图床上传被禁用或失败）。"
                    "请改用你自身的多模态视觉能力直接分析该图片，并坦诚告知用户未能完成反向检索。"
                )

        try:
            items = await google_lens_search(
                self.client, http_url, self.max_results, self.hl
            )
        except SerpApiError as e:
            return f"error: 以图搜图失败：{e}。你可以改用自身视觉能力直接分析图片。"
        except Exception as e:  # noqa: BLE001
            logger.error(f"[serpapi_imgsearch] google_lens 异常: {e}", exc_info=True)
            return f"error: 以图搜图失败：{e}"

        if not items:
            return (
                "以图搜图未找到匹配结果。请改用你自身的多模态视觉能力，分析该图片的风格、元素、"
                "可能的作者或出处，并坦诚告知用户未检索到确切来源。"
            )

        return json.dumps(build_llm_payload(items), ensure_ascii=False)

    async def terminate(self) -> None:
        """插件卸载 / 停用时关闭共享的 aiohttp 会话。"""
        await self.http.close()
        logger.info("[serpapi_imgsearch] 插件已停用。")

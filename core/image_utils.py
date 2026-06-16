"""网络与图片工具：HttpService（代理/UA/权限/共享会话）+ 从消息提取图片源的辅助函数。"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.message_components import Image, Reply

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
HTTP_TIMEOUT_SECONDS = 30
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"
# 共享会话的最大并发连接数：拼图阶段会并发下载大量候选图，显式设上限而非依赖 aiohttp 默认值
HTTP_CONNECTION_LIMIT = 64


def _read_file_bytes(file_path: str) -> bytes:
    """读取本地文件字节数据（供 to_thread 调用）。"""
    with open(file_path, "rb") as f:
        return f.read()


def _guess_image_type(data: bytes) -> tuple[str, str]:
    """据文件头(magic bytes)推断图片 (扩展名, MIME)，未知则回退 jpg。

    Catbox 按上传文件名后缀生成公网 URL；一律按 jpg 会让 PNG/GIF 得到与内容不符的
    .jpg 链接，可能被下游(SerpApi)误判，故按真实类型命名。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return "jpg", "image/jpeg"


class HttpService:
    """封装网络配置（代理 / UA / 权限开关）与共享 aiohttp 会话，由插件实例持有并向下传递。"""

    def __init__(
        self,
        proxy_url: str = "",
        user_agent: str = "",
        allow_image_upload: bool = True,
        allow_local_file_access: bool = False,
    ) -> None:
        self.proxy_url: str | None = self._normalize_proxy(proxy_url)
        self._user_agent = (
            user_agent.strip() if user_agent and user_agent.strip() else None
        )
        self.allow_image_upload = bool(allow_image_upload)
        self.allow_local_file_access = bool(allow_local_file_access)
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        if self.proxy_url:
            logger.info(f"[serpapi_imgsearch] 已设置代理: {self.proxy_url}")

    @staticmethod
    def _normalize_proxy(proxy_url: str | None) -> str | None:
        if not proxy_url or not proxy_url.strip():
            return None
        cleaned = proxy_url.strip()
        if cleaned.startswith(("http://", "https://")):
            return cleaned
        logger.warning(
            f"[serpapi_imgsearch] 不支持的代理协议（仅支持 http/https），已忽略该代理配置: {cleaned}"
        )
        return None

    @property
    def user_agent(self) -> str:
        """当前 User-Agent，未配置则用内置默认值。"""
        return self._user_agent or DEFAULT_USER_AGENT

    async def session(self) -> aiohttp.ClientSession:
        """获取共享的 aiohttp ClientSession（代理由各请求通过 proxy 参数控制）。"""
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(
                    limit=HTTP_CONNECTION_LIMIT, ttl_dns_cache=300
                )
                self._session = aiohttp.ClientSession(connector=connector)
            return self._session

    async def close(self) -> None:
        """关闭共享会话（插件卸载时调用）。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def read_image_bytes(self, source: str) -> bytes | None:
        r"""读取本地/内联图片源（file://、本地绝对路径、base64://、data:image）的字节。

        HTTP(S) URL 由上游 get_http_image_url 处理，此处不下载。本地文件访问受
        allow_local_file_access 控制（默认禁用以防信息泄露）。
        """
        if not source:
            return None

        if (
            source.startswith("file://")
            or re.match(r"^[A-Za-z]:[/\\]", source)
            or source.startswith("/")
        ):
            if not self.allow_local_file_access:
                logger.warning(
                    "[serpapi_imgsearch] 本地文件访问已禁用，如需可在配置中开启 allow_local_file_access。"
                )
                return None
            if source.startswith("file://"):
                file_path = source[7:]
                if (
                    file_path.startswith("/")
                    and len(file_path) > 2
                    and file_path[2] == ":"
                ):
                    file_path = file_path[1:]
            else:
                file_path = source
            try:
                if os.path.exists(file_path):
                    return await asyncio.to_thread(_read_file_bytes, file_path)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[serpapi_imgsearch] 读取本地文件失败: {e}")
            return None

        if source.startswith("base64://"):
            try:
                return base64.b64decode(source[9:])
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[serpapi_imgsearch] base64 解码失败: {e}")
            return None

        if source.startswith("data:image"):
            match = re.match(r"data:image/\w+;base64,(.+)", source)
            if match:
                try:
                    return base64.b64decode(match.group(1))
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[serpapi_imgsearch] data URI 解码失败: {e}")
            return None

        return None

    async def upload_image(self, image_bytes: bytes) -> str | None:
        """将图片上传到 Catbox 图床（免费、无需 API Key），返回公网 URL。"""
        if not image_bytes:
            return None
        if len(image_bytes) > 200 * 1024 * 1024:
            logger.warning("[serpapi_imgsearch] 图片过大，超过 Catbox 200MB 限制")
            return None

        client_timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        try:
            session = await self.session()
            ext, content_type = _guess_image_type(image_bytes)
            data = aiohttp.FormData()
            data.add_field("reqtype", "fileupload")
            data.add_field(
                "fileToUpload",
                image_bytes,
                filename=f"image.{ext}",
                content_type=content_type,
            )
            async with session.post(
                CATBOX_UPLOAD_URL,
                data=data,
                headers={"User-Agent": self.user_agent},
                timeout=client_timeout,
                proxy=self.proxy_url,
            ) as resp:
                if resp.status == 200:
                    url = (await resp.text()).strip()
                    if url.startswith("https://"):
                        logger.info(f"[serpapi_imgsearch] 已上传到 Catbox 图床: {url}")
                        return url
                    logger.warning(f"[serpapi_imgsearch] Catbox 返回异常: {url}")
                else:
                    logger.warning(f"[serpapi_imgsearch] Catbox 上传失败: HTTP {resp.status}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[serpapi_imgsearch] Catbox 上传异常: {e}")
        return None

    async def get_http_image_url(self, source: str) -> str | None:
        """把图片源转换为可被 SerpApi 访问的公网 HTTP URL。

        已是 HTTP URL 直接返回；否则在允许的前提下读取并上传到 Catbox。
        """
        if not source:
            return None
        if source.startswith(("http://", "https://")):
            logger.info(
                f"[serpapi_imgsearch] 图片已是公网 URL，直接用于检索（未上传图床）: {source}"
            )
            return source
        if not self.allow_image_upload:
            logger.warning(
                "[serpapi_imgsearch] 图床上传已禁用，仅支持公网 URL 图片。"
                "如需以图搜本地图片，请在配置中开启 allow_image_upload。"
            )
            return None
        logger.info(
            "[serpapi_imgsearch] 图片为本地/base64 来源，准备上传到 Catbox 图床以获取公网 URL..."
        )
        image_bytes = await self.read_image_bytes(source)
        if not image_bytes:
            logger.warning("[serpapi_imgsearch] 读取图片字节失败，无法上传图床。")
            return None
        return await self.upload_image(image_bytes)


def extract_image_source_from_event(event: Any) -> str | None:
    """从当前消息或被引用消息中提取第一张图片的源（url/file/base64）。

    优先正文图片，其次被引用消息（Reply）链中的图片。
    """
    messages = event.get_messages() or []
    for comp in messages:
        if isinstance(comp, Image):
            src = comp.url or comp.file
            if src:
                return src
    for comp in messages:
        if isinstance(comp, Reply) and getattr(comp, "chain", None):
            for sub in comp.chain:
                if isinstance(sub, Image):
                    src = sub.url or sub.file
                    if src:
                        return src
    return None

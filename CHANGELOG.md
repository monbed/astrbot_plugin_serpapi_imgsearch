# 更新日志

本文件记录本插件的重要变更，版本号遵循语义化版本。

## [1.1.0] - 2026-06-27

### 新增
- 图床可选：新增 `network.image_host`（`litterbox` / `uguu` / `catbox`）与 `network.litterbox_time`（`1h`/`12h`/`24h`/`72h`）。litterbox、uguu 为临时图床，更适合以图搜图，且对代理/机房 IP 更友好（Catbox 常按 IP 封禁上传，返回 `412 Invalid uploader`）。

### 修复
- 适配 AstrBot 主框架 **v4.26.0**（PR #8764「统一媒体引用处理」）：该版本起，预处理阶段会把入站图片落地为本地临时文件并改写 `Image.url`/`Image.file`，导致**以图搜图取不到图片直链**。现改为通过框架的 `Image.convert_to_base64()` 还原图片字节后上传图床，恢复以图搜图。向下兼容旧版框架——图片本身是公网直链时仍直接复用，不下载、不上传。

### 变更
- 界面语言 `region.hl` 默认值由 `en` 改为 `zh-cn`。
- 默认图床由 Catbox 改为 **Litterbox**（临时、默认 1h；对代理/机房 IP 更友好，以图搜图只需链接短暂存活即可）。

### 移除
- 移除 `network.allow_local_file_access` 配置项：改用框架解析器取图后该开关已对主路径失效。本地/临时图片的读取与上传现统一由 `network.allow_image_upload` 把关——关闭即不读取、不上传任何非公网图片。

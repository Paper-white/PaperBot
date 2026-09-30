"""文本 / Markdown → 图片 渲染核心。

链路：
    Markdown 文本
      --(python-markdown + Pygments)--> HTML 片段
      --(Jinja2 模板 markdown.html + markdown.css)--> 完整 HTML
      --(Playwright 无头浏览器，元素级截图)--> PNG 字节流

设计要点：
- **浏览器单例**：首次使用时懒加载，进程内复用；bot 关闭时调用 :func:`shutdown_renderer` 释放。
- **来源可配**：本地复用系统 Edge（``RENDER_BROWSER_CHANNEL=msedge``），搬服务器时改成
  ``chromium`` 或指定 ``RENDER_BROWSER_PATH`` 即可，代码无需改动。
- **尺寸规则**：宽度固定（``RENDER_WIDTH``），高度自适应（只截 ``.markdown-body`` 元素）。

用法::

    from src.core.render import render_markdown

    png: bytes = await render_markdown("# 标题\\n正文 **加粗**")
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

import markdown as md_lib
from jinja2 import Environment, FileSystemLoader
from loguru import logger

from src.config import BASE_DIR, settings

TEMPLATE_DIR: Path = BASE_DIR / "src" / "core" / "templates"
TEMPLATE_NAME = "markdown.html"
CSS_NAME = "markdown.css"
BODY_SELECTOR = ".markdown-body"

# 页脚两行内容（可被参数覆盖）
FOOTER_LINE1 = "广告位招租"
FOOTER_LINE2 = "把复杂留给自己，把可爱留给世界"

# Markdown 扩展：覆盖 表格 / 代码高亮 / 任务列表 / 引用 / 图片
MARKDOWN_EXTENSIONS: list[str] = [
    "tables",
    "footnotes",
    "attr_list",
    "def_list",
    "abbr",
    "md_in_html",
    "sane_lists",
    "nl2br",
    "pymdownx.superfences",
    "pymdownx.highlight",
    "pymdownx.tasklist",
]

MARKDOWN_EXTENSION_CONFIGS: dict[str, dict[str, Any]] = {
    "pymdownx.highlight": {
        "css_class": "codehilite",  # 与 markdown.css 里的 .codehilite 对齐
        "guess_lang": False,
        "linenums": False,
    },
    "pymdownx.tasklist": {
        "custom_checkbox": False,     # 用原生 <input type=checkbox>，由 CSS 美化
        "clickable_checkbox": False,
    },
}


class RenderError(RuntimeError):
    """渲染过程中的错误（浏览器启动失败 / 元素找不到等）。"""


# --------------------------------------------------------------------------- #
# Markdown → HTML
# --------------------------------------------------------------------------- #
def markdown_to_html(
    text: str,
    *,
    extensions: list[str] | None = None,
    extension_configs: dict[str, dict[str, Any]] | None = None,
) -> str:
    """把 Markdown 文本转成 HTML 片段（本地图片会自动内联为 data URI）。"""
    return md_lib.markdown(
        inline_local_images(text or ""),
        extensions=extensions or MARKDOWN_EXTENSIONS,
        extension_configs=extension_configs or MARKDOWN_EXTENSION_CONFIGS,
        output_format="html5",
    )


# --------------------------------------------------------------------------- #
# HTML 组装（模板 + 样式 + OC 叠图）
# --------------------------------------------------------------------------- #
_env: Environment | None = None


def _get_env() -> Environment:
    """懒加载 Jinja2 环境（关闭自动转义：content 已是 HTML）。"""
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _env


def default_footer(
    line1: str = FOOTER_LINE1,
    line2: str = FOOTER_LINE2,
) -> str:
    """构造页脚两行内容（外层 ``.pb-footer`` 由模板负责）。"""
    return f'<span class="line1">{line1}</span><span class="line2">{line2}</span>'


def _resolve_image(path_or_url: str) -> str:
    """把本地图片路径转成 data URI（确保 ``set_content`` 下也能加载）。"""
    src = path_or_url.strip()
    if src.startswith(("http://", "https://", "data:")):
        return src
    p = Path(src)
    if not p.is_absolute():
        p = BASE_DIR / p
    if not p.is_file():
        logger.warning(f"图片不存在，已忽略：{p}")
        return ""
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# Markdown 图片语法：![alt](src "title")
_IMG_RE = re.compile(r'!\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+["\'][^"\']*["\'])?\s*\)')


def inline_local_images(text: str) -> str:
    """把 Markdown 里的**本地图片路径**内联成 data URI，网络图片原样保留。"""
    def _repl(m: "re.Match[str]") -> str:
        alt, src = m.group(1), m.group(2)
        if src.startswith("<") and src.endswith(">"):
            src = src[1:-1]
        if src.startswith(("http://", "https://", "data:", "//")):
            return m.group(0)
        uri = _resolve_image(src)
        return f"![{alt}]({uri})" if uri else m.group(0)

    return _IMG_RE.sub(_repl, text or "")


def _bg_image_style() -> str:
    """按配置生成 OC 叠图的 CSS 变量覆盖块。"""
    src = settings.render_bg_image.strip()
    if not src:
        return ""
    uri = _resolve_image(src)
    if not uri:
        return ""
    return (
        "<style>:root{"
        f'--pb-bg-image:url("{uri}");'
        f"--pb-bg-image-opacity:{settings.render_bg_image_opacity};"
        f"--pb-bg-image-size:{settings.render_bg_image_size};"
        f"--pb-bg-image-position:{settings.render_bg_image_position};"
        "}</style>"
    )


def build_html(
    content_html: str,
    *,
    header: str = "",
    footer: str = "",
) -> str:
    """把 HTML 正文套进模板，返回完整 HTML 文档字符串。"""
    css = (TEMPLATE_DIR / CSS_NAME).read_text(encoding="utf-8")
    html = _get_env().get_template(TEMPLATE_NAME).render(
        css=css,
        content=content_html,
        header=header,
        footer=footer,
    )
    bg = _bg_image_style()
    if bg:
        html = html.replace("</head>", bg + "</head>")
    return html


# --------------------------------------------------------------------------- #
# 浏览器单例
# --------------------------------------------------------------------------- #
class _BrowserManager:
    """Playwright 浏览器单例（懒加载 + 串行启动 + 关闭释放）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None

    async def get(self) -> Any:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            await self._close_locked()
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover
                raise RenderError(
                    "未安装 playwright，请执行：pixi add --pypi playwright"
                ) from exc

            self._playwright = await async_playwright().start()
            kwargs: dict[str, Any] = {
                "headless": True,
                "args": [
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--force-color-profile=srgb",
                ],
            }
            if settings.render_browser_path:
                kwargs["executable_path"] = settings.render_browser_path
                src_desc = f"executable_path={settings.render_browser_path}"
            elif settings.render_browser_channel:
                kwargs["channel"] = settings.render_browser_channel
                src_desc = f"channel={settings.render_browser_channel}"
            else:
                src_desc = "内置 chromium"

            try:
                self._browser = await self._playwright.chromium.launch(**kwargs)
            except Exception as exc:
                await self._close_locked()
                raise RenderError(
                    f"启动浏览器失败（{src_desc}）：{exc}。"
                    "请检查 RENDER_BROWSER_CHANNEL / RENDER_BROWSER_PATH 配置。"
                ) from exc

            logger.info(f"🎨 渲染浏览器已启动（{src_desc}）")
            return self._browser

    async def _close_locked(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # pragma: no cover
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # pragma: no cover
                pass
            self._playwright = None

    async def close(self) -> None:
        async with self._lock:
            if self._browser is None and self._playwright is None:
                return
            await self._close_locked()
            logger.info("🎨 渲染浏览器已关闭")


_manager = _BrowserManager()


async def shutdown_renderer() -> None:
    """释放浏览器资源（挂到 bot 的关闭钩子上）。"""
    await _manager.close()


# --------------------------------------------------------------------------- #
# 截图
# --------------------------------------------------------------------------- #
async def render_html_to_png(
    html: str,
    *,
    selector: str = BODY_SELECTOR,
    width: int | None = None,
    scale: int | None = None,
    timeout: int | None = None,
) -> bytes:
    """把完整 HTML 渲染成 PNG 字节流（只截 ``selector`` 指定的元素）。"""
    width = width or settings.render_width
    scale = scale or settings.render_scale
    timeout = timeout or settings.render_timeout_ms

    browser = await _manager.get()
    page = await browser.new_page(
        viewport={"width": width, "height": 600},
        device_scale_factor=scale,
    )
    try:
        await page.set_content(html, wait_until="load", timeout=timeout)
        target = page.locator(selector).first
        await target.wait_for(state="visible", timeout=timeout)
        png: bytes = await target.screenshot(type="png")
        return png
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"渲染截图失败：{exc}") from exc
    finally:
        await page.close()


# --------------------------------------------------------------------------- #
# 对外便捷接口
# --------------------------------------------------------------------------- #
async def render_markdown(
    text: str,
    *,
    header: str = "",
    footer: str | None = None,
    width: int | None = None,
    scale: int | None = None,
) -> bytes:
    """Markdown 文本 → PNG 字节流。

    - ``header``：卡片顶部标题栏，留空则不渲染。
    - ``footer``：卡片底部页脚，**常显**；默认固定两行文案（广告位招租 + 座右铭），
      一般无需传参（传了会覆盖文案）。
    """
    body = markdown_to_html(text)
    html = build_html(body, header=header, footer=footer or default_footer())
    return await render_html_to_png(html, width=width, scale=scale)


async def render_text(
    text: str,
    *,
    header: str = "",
    footer: str | None = None,
    width: int | None = None,
    scale: int | None = None,
) -> bytes:
    """纯文本 → PNG 字节流（按行换行渲染，不走 Markdown 语法）。"""
    from html import escape

    paragraphs = "".join(
        f"<p>{escape(line)}</p>" if line.strip() else "<p><br></p>"
        for line in (text or "").splitlines()
    )
    html = build_html(paragraphs, header=header, footer=footer or default_footer())
    return await render_html_to_png(html, width=width, scale=scale)


async def warmup() -> None:
    """预热：提前把浏览器拉起来，避免首次渲染卡顿。"""
    try:
        await _manager.get()
    except RenderError as exc:
        logger.warning(f"渲染预热失败（不影响启动）：{exc}")


__all__ = [
    "RenderError",
    "FOOTER_LINE1",
    "FOOTER_LINE2",
    "default_footer",
    "markdown_to_html",
    "inline_local_images",
    "build_html",
    "render_html_to_png",
    "render_markdown",
    "render_text",
    "warmup",
    "shutdown_renderer",
]


if __name__ == "__main__":  # pragma: no cover
    import sys

    _demo_md = """# 渲染自测

这是通过 `src/core/render.py` 真实链路生成的图片。

## 表格

| 项目 | 说明 |
| --- | --- |
| 引擎 | Playwright + 系统 Edge |
| 样式 | 二次元风 CSS |
| 宽度 | 720 固定 |

## 代码

```python
async def hello(name: str) -> str:
    return f"喵~ {name}"
```

## 任务列表

- [x] Markdown → HTML
- [x] HTML → 图片
- [ ] 接入人设对话

> 引用也会被渲染成浅蓝色块。
"""

    _out = Path(sys.argv[1] if len(sys.argv) > 1 else "render-demo.png")
    _text = (
        Path(sys.argv[2]).read_text(encoding="utf-8")
        if len(sys.argv) > 2
        else _demo_md
    )

    async def _amain() -> None:
        try:
            png = await render_markdown(_text)
        finally:
            await shutdown_renderer()  # 退出前释放浏览器，避免事件循环告警
        _out.write_bytes(png)
        logger.success(f"已生成：{_out.resolve()}（{len(png)} 字节）")

    asyncio.run(_amain())

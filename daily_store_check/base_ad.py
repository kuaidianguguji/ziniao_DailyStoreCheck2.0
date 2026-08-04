"""三个平台广告爬虫共用的最小抽象。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class BaseAdCrawler:
    """平台爬虫基类，统一输出字段，方便写入飞书两种表。"""

    platform = "unknown"

    def __init__(self, config: dict[str, Any] | None = None):
        """保存当前平台配置，例如后续可扩展的页面选择器。"""
        self.config = config or {}

    def collect(self, store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
        """使用 DrissionPage 接管紫鸟当前页面并返回标准化记录。"""
        page = self._open_page(debugging_port)
        metrics = self.extract_metrics(page)
        collected_at = datetime.now(timezone.utc).isoformat()
        return [
            {
                "店铺名": store_name,
                "平台": self.platform,
                "采集时间": collected_at,
                "指标": metric,
                "数值": value,
                "原始数据": raw,
            }
            for metric, value, raw in metrics
        ]

    def extract_metrics(self, page: Any) -> list[tuple[str, Any, str]]:
        """子类覆盖页面选择器；没有页面时返回说明性记录。"""
        if page is None:
            return [("页面状态", "未连接", "请检查 DrissionPage/紫鸟调试端口")]
        title = str(getattr(page, "title", "") or "")
        return [("页面标题", title, "page.title")]

    @staticmethod
    def _open_page(debugging_port: int | str | None) -> Any:
        """强制使用 DrissionPage 连接紫鸟已打开的 Chromium 调试端口。"""
        if not debugging_port:
            raise RuntimeError("紫鸟没有返回 debuggingPort，无法使用 DrissionPage")
        try:
            from DrissionPage import ChromiumPage
        except ImportError as exc:
            raise RuntimeError("未安装 DrissionPage，请执行 pip install -r requirements-daily.txt") from exc
        try:
            return ChromiumPage(addr_or_opts=f"127.0.0.1:{debugging_port}")
        except Exception as exc:
            raise RuntimeError(f"DrissionPage 连接紫鸟调试端口失败: {debugging_port}") from exc

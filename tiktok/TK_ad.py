"""TikTok 广告数据最小采集器。

页面选择器会因站点版本变化，后续把选择器放入 config.yaml 即可调整，无需改调度器。
"""

from __future__ import annotations

from typing import Any

from daily_store_check.base_ad import BaseAdCrawler


class TiktokAdCrawler(BaseAdCrawler):
    platform = "tiktok"

    def extract_metrics(self, page: Any) -> list[tuple[str, Any, str]]:
        """读取 TikTok 广告数量和花费的示例元素。"""
        if page is None:
            return super().extract_metrics(page)
        results: list[tuple[str, Any, str]] = [("页面标题", str(getattr(page, "title", "") or ""), "page.title")]
        # 这是演示选择器：正式使用时按当前账号页面 DOM 调整。
        for metric, selector in (("广告数量", "[data-testid='ad-count']"), ("总花费", "[data-testid='total-spend']")):
            value = self._first_text(page, selector)
            if value:
                results.append((metric, value, selector))
        return results

    @staticmethod
    def _first_text(page: Any, selector: str) -> str:
        """使用 DrissionPage CSS 选择器返回第一个匹配元素文本。"""
        try:
            elements = page.eles(f"css:{selector}")
            if elements:
                element = elements[0]
                return str(getattr(element, "text", "") or getattr(element, "text_content", "") or "").strip()
        except Exception:
            return ""
        return ""


def collect_tiktok_ad(store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
    """以 DrissionPage 调试端口为入口执行 TikTok 采集。"""
    return TiktokAdCrawler().collect(store_name, download_path, debugging_port)

"""Shopee 广告数据最小采集器。"""

from __future__ import annotations

from typing import Any

from daily_store_check.base_ad import BaseAdCrawler


class ShopeeAdCrawler(BaseAdCrawler):
    platform = "shopee"

    def extract_metrics(self, page: Any) -> list[tuple[str, Any, str]]:
        """读取 Shopee 广告余额和广告订单的示例元素。"""
        if page is None:
            return super().extract_metrics(page)
        results: list[tuple[str, Any, str]] = [("页面标题", str(getattr(page, "title", "") or ""), "page.title")]
        for metric, selector in (("广告余额", "[data-testid='ad-balance']"), ("广告订单", "[data-testid='ad-orders']")):
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
                return str(getattr(elements[0], "text", "") or "").strip()
        except Exception:
            return ""
        return ""


def collect_shopee_ad(store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
    """以 DrissionPage 调试端口为入口执行 Shopee 采集。"""
    return ShopeeAdCrawler().collect(store_name, download_path, debugging_port)

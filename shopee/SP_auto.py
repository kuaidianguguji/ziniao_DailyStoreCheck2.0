"""Shopee 店铺广告自动化。

本文件独立维护 Shopee 的 DrissionPage 连接、页面操作、数据提取和结果组装。
程序只接管紫鸟已经打开的当前标签页，不会主动访问任何网址。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from DrissionPage import Chromium


class ShopeeAuto:
    """Shopee 广告后台自动化。"""

    def __init__(self, config: dict[str, Any] | None = None):
        """保存 Shopee 独立配置，后续可在 YAML 中增加选择器或业务参数。"""
        self.config = config or {}

    def collect(self, store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
        """接管紫鸟当前标签页，采集 Shopee 广告数据。"""
        if not debugging_port:
            raise RuntimeError("紫鸟没有返回 debuggingPort，无法接管 Shopee 店铺")

        # 连接紫鸟已经打开的 Chromium，不创建普通浏览器，也不主动访问网址。
        browser = Chromium(f"127.0.0.1:{debugging_port}")
        tab = browser.latest_tab
        collected_at = datetime.now(timezone.utc).isoformat()

        # 下面是简单的 DrissionPage 写法；按真实 Shopee 后台 DOM 修改选择器即可。
        rows: list[dict[str, Any]] = [
            {
                "店铺名": store_name,
                "平台": "shopee",
                "采集时间": collected_at,
                "指标": "页面标题",
                "数值": tab.title,
                "原始数据": "tab.title",
            }
        ]

        ad_balance = tab.ele("css:[data-testid='ad-balance']", timeout=3)
        if ad_balance:
            rows.append(
                {
                    "店铺名": store_name,
                    "平台": "shopee",
                    "采集时间": collected_at,
                    "指标": "广告余额",
                    "数值": ad_balance.text,
                    "原始数据": "[data-testid='ad-balance']",
                }
            )

        ad_orders = tab.ele("css:[data-testid='ad-orders']", timeout=3)
        if ad_orders:
            rows.append(
                {
                    "店铺名": store_name,
                    "平台": "shopee",
                    "采集时间": collected_at,
                    "指标": "广告订单",
                    "数值": ad_orders.text,
                    "原始数据": "[data-testid='ad-orders']",
                }
            )

        return rows


def collect_shopee_ad(store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
    """提供一个可直接调用的 Shopee 函数入口。"""
    return ShopeeAuto().collect(store_name, download_path, debugging_port)


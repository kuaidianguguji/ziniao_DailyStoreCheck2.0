"""Mercado Libre（美客多）店铺广告自动化。

本文件独立维护美客多的 DrissionPage 连接、页面操作、数据提取和结果组装。
程序只接管紫鸟已经打开的当前标签页，不会主动访问任何网址。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from DrissionPage import Chromium


class MercadoAuto:
    """美客多广告后台自动化。"""

    def __init__(self, config: dict[str, Any] | None = None):
        """保存美客多独立配置，后续可在 YAML 中增加选择器或业务参数。"""
        self.config = config or {}

    def collect(self, store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
        """接管紫鸟当前标签页，采集美客多广告数据。"""
        if not debugging_port:
            raise RuntimeError("紫鸟没有返回 debuggingPort，无法接管美客多店铺")

        # 连接紫鸟已经打开的 Chromium，不创建普通浏览器，也不主动访问网址。
        browser = Chromium(f"127.0.0.1:{debugging_port}")
        tab = browser.latest_tab
        collected_at = datetime.now(timezone.utc).isoformat()

        # 下面是简单的 DrissionPage 写法；按真实美客多后台 DOM 修改选择器即可。
        rows: list[dict[str, Any]] = [
            {
                "店铺名": store_name,
                "平台": "mercado",
                "采集时间": collected_at,
                "指标": "页面标题",
                "数值": tab.title,
                "原始数据": "tab.title",
            }
        ]

        ad_spend = tab.ele("css:[data-testid='ad-spend']", timeout=3)
        if ad_spend:
            rows.append(
                {
                    "店铺名": store_name,
                    "平台": "mercado",
                    "采集时间": collected_at,
                    "指标": "广告花费",
                    "数值": ad_spend.text,
                    "原始数据": "[data-testid='ad-spend']",
                }
            )

        ad_sales = tab.ele("css:[data-testid='ad-sales']", timeout=3)
        if ad_sales:
            rows.append(
                {
                    "店铺名": store_name,
                    "平台": "mercado",
                    "采集时间": collected_at,
                    "指标": "广告销售额",
                    "数值": ad_sales.text,
                    "原始数据": "[data-testid='ad-sales']",
                }
            )

        return rows


def collect_mercado_ad(store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
    """提供一个可直接调用的美客多函数入口。"""
    return MercadoAuto().collect(store_name, download_path, debugging_port)


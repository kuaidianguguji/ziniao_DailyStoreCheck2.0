"""TikTok 店铺广告和数据分析自动化。

本文件独立维护 TikTok 的 DrissionPage 连接、按钮点击、XPath、数值解析和飞书字段。
程序只接管紫鸟已经打开的当前标签页，不会主动访问任何网址。

所有按钮和指标 xpath 均故意留空。后续只需要在本文件填写实际 XPath；
XPath 为空、元素不存在或读取失败时默认返回空值，不会中断其他步骤和指标。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from DrissionPage import Chromium


# ---------------------------------------------------------------------------
# 一、店铺广告流程
# ---------------------------------------------------------------------------

# 进入店铺广告页面需要依次点击的按钮。
# 实际需要更多或更少步骤时，可以直接增加或删除列表项。
AD_COMMON_CLICK_STEPS: list[dict[str, Any]] = [
    {"name": "点击营销按钮", "xpath": '//div[@class="p-menu-inline"]//div[@class="p-menu-item-title-txt"][normalize-space()="营销"]', "wait_seconds": 1},
    {"name": "点击店铺广告", "xpath": '//div[@class="p-menu-inline"]//div[@class="p-menu-item-title-txt"][normalize-space()="店铺广告"]', "wait_seconds": 2},
]


# 店铺广告时间范围切换步骤。每个时间范围都预留“打开时间”和“选择范围”两次点击。
AD_PERIOD_CLICK_STEPS: dict[str, list[dict[str, Any]]] = {
    "昨天": [
        {"name": "广告-点击时间按钮", "xpath": '//span[@class="theme-arco-picker-suffix-icon"]', "wait_seconds": 1},
        {"name": "广告-点击昨天按钮", "xpath": '//button[contains(@class,"theme-arco-btn") and contains(@class,"theme-arco-btn-secondary") and contains(@class,"theme-arco-btn-size-mini") and contains(@class,"theme-arco-btn-shape-square")][contains(.,"昨") and contains(.,"天")]', "wait_seconds": 2},
    ],
    "7天": [
        {"name": "广告-再次点击时间按钮", "xpath": '//span[@class="theme-arco-picker-suffix-icon"]', "wait_seconds": 1},
        {"name": "广告-点击7天按钮", "xpath": '//button[contains(@class,"theme-arco-btn") and contains(@class,"theme-arco-btn-secondary") and contains(@class,"theme-arco-btn-size-mini") and contains(@class,"theme-arco-btn-shape-square")][contains(.,"近") and contains(.,"7") and contains(.,"天")]', "wait_seconds": 2},
    ],
}


# 广告金额使用美元 USD，不转换为巴西雷亚尔。
AD_METRIC_SPECS: list[dict[str, str]] = [
    {"period": "昨天", "field": "昨天成本", "xpath": '//div[normalize-space(.)="成本"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")][contains(.,"USD")]', "kind": "currency", "currency_code": "USD"},
    {"period": "昨天", "field": "昨天SKU订单数", "xpath": '//div[normalize-space(.)="SKU 订单数"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")]', "kind": "integer"},
    {"period": "昨天", "field": "昨天均单价", "xpath": '//div[normalize-space(.)="平均下单成本"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")][contains(.,"USD")]', "kind": "currency", "currency_code": "USD"},
    {"period": "昨天", "field": "昨天总收入", "xpath": '//div[normalize-space(.)="总收入"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")][contains(.,"USD")]', "kind": "currency", "currency_code": "USD"},
    {"period": "昨天", "field": "昨天ROI", "xpath": '//div[normalize-space(.)="ROI"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")]', "kind": "decimal"},
    {"period": "7天", "field": "7天成本", "xpath": '//div[normalize-space(.)="成本"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")][contains(.,"USD")]', "kind": "currency", "currency_code": "USD"},
    {"period": "7天", "field": "7天SKU订单数", "xpath": '//div[normalize-space(.)="SKU 订单数"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")]', "kind": "integer"},
    {"period": "7天", "field": "7天均单价", "xpath": '//div[normalize-space(.)="平均下单成本"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")][contains(.,"USD")]', "kind": "currency", "currency_code": "USD"},
    {"period": "7天", "field": "7天总收入", "xpath": '//div[normalize-space(.)="总收入"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")][contains(.,"USD")]', "kind": "currency", "currency_code": "USD"},
    {"period": "7天", "field": "7天ROI", "xpath": '//div[normalize-space(.)="ROI"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")]', "kind": "decimal"},
]


# ---------------------------------------------------------------------------
# 二、数据分析 -> 概览流程
# ---------------------------------------------------------------------------

# 从店铺广告页面切换到数据分析概览页面的点击步骤。
OVERVIEW_COMMON_CLICK_STEPS: list[dict[str, Any]] = [
    {"name": "点击数据分析", "xpath": '//div[@class="p-menu-inline"]//div[@class="p-menu-item-title-txt"][normalize-space(.)="数据分析"]', "wait_seconds": 1},
    {"name": "点击概览", "xpath": '//span[contains(@class,"text-base") and contains(@class,"font-medium") and contains(@class,"text-neutral-text1")][normalize-space(.)="概览"]', "wait_seconds": 2},
]


# 数据概览时间范围切换步骤。
OVERVIEW_PERIOD_CLICK_STEPS: dict[str, list[dict[str, Any]]] = {
    "昨天": [
        {"name": "概览-点击时间按钮", "xpath": '//div[contains(@class,"arco-picker-input")]//input[@placeholder="结束日期"]', "wait_seconds": 1},
        {"name": "概览-点击昨天按钮", "xpath": '//div[contains(@class,"arco-typography")][normalize-space(.)="昨天"]', "wait_seconds": 2},
    ],
    "7天": [
        {"name": "概览-再次点击时间按钮", "xpath": '//div[contains(@class,"arco-picker-input")]//input[@placeholder="结束日期"]', "wait_seconds": 1},
        {"name": "概览-点击7天按钮", "xpath": '//div[contains(@class,"arco-typography")][normalize-space(.)="最近 7 天"]', "wait_seconds": 2},
    ],
}


# 概览中的 GMV 使用巴西雷亚尔 BRL；数量为整数，比率为无小数百分数。
OVERVIEW_METRIC_SPECS: list[dict[str, str]] = [
    {"period": "昨天", "field": "昨天GMV", "xpath": '//div[@class="pcm-smc"][contains(.,"GMV")]//div[@class="pcm-smc-content"]', "kind": "currency", "currency_code": "BRL"},
    {"period": "昨天", "field": "昨天成交件数", "xpath": '//div[@class="pcm-smc"][contains(.,"商品成交件数")]//div[@class="pcm-smc-value-content"]', "kind": "integer"},
    {"period": "昨天", "field": "昨天SKU订单数", "xpath": '//div[@class="pcm-smc"][contains(.,"SKU 订单数")]//div[@class="pcm-smc-value-content"]', "kind": "integer"},
    {"period": "昨天", "field": "昨天订单数", "xpath": '//*[normalize-space(.)="订单数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "昨天", "field": "昨天客户数", "xpath": '//*[normalize-space(.)="客户数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "昨天", "field": "昨天商品访客数", "xpath": '//*[normalize-space(.)="商品访客数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "昨天", "field": "昨天曝光数", "xpath": '//*[normalize-space(.)="商品曝光次数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "昨天", "field": "昨天去重曝光数", "xpath": '//*[normalize-space(.)="去重商品曝光次数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "昨天", "field": "昨天GMV直播比", "xpath": '//*[normalize-space(.)="直播"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "percent"},
    {"period": "昨天", "field": "昨天GMV视频比", "xpath": '//*[normalize-space(.)="视频"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "percent"},
    {"period": "昨天", "field": "昨天GMV商品卡比", "xpath": '//*[normalize-space(.)="商品卡"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "percent"},
    {"period": "7天", "field": "7天GMV", "xpath": '//div[@class="pcm-smc"][contains(.,"GMV")]//div[@class="pcm-smc-content"]', "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天成交件数", "xpath": '//div[@class="pcm-smc"][contains(.,"商品成交件数")]//div[@class="pcm-smc-value-content"]', "kind": "integer"},
    {"period": "7天", "field": "7天SKU订单数", "xpath": '//div[@class="pcm-smc"][contains(.,"SKU 订单数")]//div[@class="pcm-smc-value-content"]', "kind": "integer"},
    {"period": "7天", "field": "7天订单数", "xpath": '//*[normalize-space(.)="订单数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "7天", "field": "7天客户数", "xpath": '//*[normalize-space(.)="客户数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "7天", "field": "7天商品访客数", "xpath": '//*[normalize-space(.)="商品访客数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "7天", "field": "7天曝光数", "xpath": '//*[normalize-space(.)="商品曝光次数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "7天", "field": "7天去重曝光数", "xpath": '//*[normalize-space(.)="去重商品曝光次数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "7天", "field": "7天GMV直播比", "xpath": '//*[normalize-space(.)="直播"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "percent"},
    {"period": "7天", "field": "7天GMV视频比", "xpath": '//*[normalize-space(.)="视频"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "percent"},
    {"period": "7天", "field": "7天GMV商品卡比", "xpath": '//*[normalize-space(.)="商品卡"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "percent"},
]


class TiktokAuto:
    """TikTok 店铺广告和数据概览自动化。"""

    def __init__(self, config: dict[str, Any] | None = None):
        """保存 TikTok 独立配置；按钮和指标 XPath 直接维护在本文件顶部。"""
        self.config = config or {}

    def collect(self, store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
        """接管紫鸟当前标签页，依次采集广告数据和数据分析概览。"""
        if not debugging_port:
            raise RuntimeError("紫鸟没有返回 debuggingPort，无法接管 TikTok 店铺")

        # 连接紫鸟已经打开的 Chromium，不创建普通浏览器，也不调用 tab.get()。
        browser = Chromium(f"127.0.0.1:{debugging_port}")
        tab = browser.latest_tab
        collected_at = datetime.now(timezone.utc).isoformat()

        # 1. 点击“营销 -> 店铺广告”，分别采集昨天和 7 天广告数据。
        ad_fields: dict[str, Any] = {}
        ad_raw_values: dict[str, str] = {}
        self._run_click_steps(tab, AD_COMMON_CLICK_STEPS)
        for period in ("昨天", "7天"):
            self._run_click_steps(tab, AD_PERIOD_CLICK_STEPS.get(period, []))
            self._collect_period_metrics(tab, period, AD_METRIC_SPECS, ad_fields, ad_raw_values)

        ad_row = {
            "店铺名": store_name,
            "平台": "tiktok",
            "采集时间": collected_at,
            "指标": "TikTok店铺广告",
            "数值": "",
            "原始数据": json.dumps(ad_raw_values, ensure_ascii=False),
            "飞书字段": ad_fields,
        }

        # 2. 点击“数据分析 -> 概览”，分别采集昨天和 7 天概览数据。
        overview_fields: dict[str, Any] = {}
        overview_raw_values: dict[str, str] = {}
        self._run_click_steps(tab, OVERVIEW_COMMON_CLICK_STEPS)
        for period in ("昨天", "7天"):
            self._run_click_steps(tab, OVERVIEW_PERIOD_CLICK_STEPS.get(period, []))
            self._collect_period_metrics(tab, period, OVERVIEW_METRIC_SPECS, overview_fields, overview_raw_values)

        overview_row = {
            "店铺名": store_name,
            "平台": "tiktok",
            "采集时间": collected_at,
            "指标": "TikTok数据分析概览",
            "数值": "",
            "原始数据": json.dumps(overview_raw_values, ensure_ascii=False),
            "飞书字段": overview_fields,
        }

        # 两个模块分别返回一条记录，避免同名字段（如 SKU订单数）相互覆盖。
        return [ad_row, overview_row]

    def _collect_period_metrics(
        self,
        tab: Any,
        period: str,
        specs: list[dict[str, str]],
        field_values: dict[str, Any],
        raw_values: dict[str, str],
    ) -> None:
        """读取某个模块、某个时间范围下的全部指标。"""
        for spec in specs:
            if spec["period"] != period:
                continue
            field_name = spec["field"]
            raw_text = self._read_xpath(tab, spec["xpath"])
            raw_values[field_name] = raw_text
            field_values[field_name] = self._format_value(raw_text, spec["kind"], spec.get("currency_code", ""))

    @staticmethod
    def _run_click_steps(tab: Any, steps: list[dict[str, Any]]) -> None:
        """按顺序执行点击步骤；空 XPath 或单步失败都不会中断后续采集。"""
        for step in steps:
            xpath = str(step.get("xpath") or "").strip()
            if not xpath:
                continue
            try:
                element = tab.ele(f"xpath:{xpath}", timeout=3)
                if element:
                    element.click()
                    wait_seconds = float(step.get("wait_seconds", 1) or 0)
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)
            except Exception:
                continue

    @staticmethod
    def _read_xpath(tab: Any, xpath: str) -> str:
        """读取一个 XPath 文本；XPath 为空、元素不存在或异常时返回空字符串。"""
        if not xpath:
            return ""
        try:
            element = tab.ele(f"xpath:{xpath}", timeout=3)
            if not element:
                return ""
            return str(element.text or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _format_value(raw_text: str, kind: str, currency_code: str = "") -> Any:
        """按类型格式化：货币/ROI 两位小数、数量为整数、比率为无小数百分数。"""
        if not raw_text:
            return ""
        if kind == "integer":
            integer_text = re.sub(r"[^0-9-]", "", str(raw_text))
            if not integer_text or integer_text == "-":
                return ""
            try:
                return int(integer_text)
            except ValueError:
                return ""

        number = TiktokAuto._parse_number(raw_text, currency_code)
        if number is None:
            return ""
        if kind in {"currency", "decimal"}:
            return round(number, 2)
        if kind == "percent":
            return f"{round(number):.0f}%"
        return raw_text

    @staticmethod
    def _parse_number(raw_text: str, currency_code: str = "") -> float | None:
        """分别解析 USD ``$1,234.56``、BRL ``R$ 1.234,56`` 和普通百分数。"""
        text = str(raw_text).strip().replace("%", "")
        text = re.sub(r"[^0-9,.-]", "", text)
        if not text or text in {"-", ".", ","}:
            return None
        try:
            if currency_code == "USD":
                # 美元格式使用逗号作为千位分隔、点号作为小数点。
                text = text.replace(",", "")
            elif currency_code == "BRL":
                # 巴西雷亚尔使用点号作为千位分隔、逗号作为小数点。
                text = text.replace(".", "").replace(",", ".")
            elif "." in text and "," in text:
                # 无币种指标按最后出现的符号判断小数点。
                if text.rfind(",") > text.rfind("."):
                    text = text.replace(".", "").replace(",", ".")
                else:
                    text = text.replace(",", "")
            elif "," in text:
                text = text.replace(",", ".")
            return float(text)
        except (TypeError, ValueError):
            return None


def collect_tiktok_ad(store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
    """提供一个可直接调用的 TikTok 函数入口。"""
    return TiktokAuto().collect(store_name, download_path, debugging_port)


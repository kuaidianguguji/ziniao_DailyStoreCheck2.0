"""Mercado Libre（美客多）店铺数据自动化和爬虫。

本文件独立维护美客多的 DrissionPage 连接、XPath、数值解析和飞书字段组装。
程序只接管紫鸟已经打开的当前标签页，不会主动访问网址。

后续抓取页面时，只需要把下面各指标的 xpath 从空字符串改成实际 XPath。
如果 xpath 为空、元素不存在或数值解析失败，该指标默认写入空值，其他指标继续执行。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from DrissionPage import Chromium


# 进入美客多数据页面前需要执行的点击步骤。
#
# 目前不知道你的账号进入数据页需要点哪些菜单，所以这里先准备多个可编辑步骤。
# 后续只需要把对应 xpath 填入，程序会严格按照列表顺序点击；xpath 为空时自动跳过。
# 如果实际只需要点击两次，就保留两项；需要更多步骤时继续向列表添加字典即可。
COMMON_CLICK_STEPS: list[dict[str, Any]] = [
    {"name": "进入经营分析菜单", "xpath": "", "wait_seconds": 1},
    {"name": "进入广告/销售数据菜单", "xpath": "", "wait_seconds": 1},
    {"name": "进入数据概览页面", "xpath": "", "wait_seconds": 2},
]


# 统计页面中的时间范围切换步骤。
# 采集 7 天指标前执行 PERIOD_CLICK_STEPS["7天"]；
# 采集完 7 天指标后执行 PERIOD_CLICK_STEPS["30天"]，再读取 30 天指标。
# 如果页面默认就是 7 天，可以把 7 天按钮 xpath 留空；30 天按钮必须填写实际 XPath。
PERIOD_CLICK_STEPS: dict[str, list[dict[str, Any]]] = {
    "7天": [
        {"name": "切换到7天数据", "xpath": "", "wait_seconds": 2},
    ],
    "30天": [
        {"name": "切换到30天数据", "xpath": "", "wait_seconds": 2},
    ],
}


# 每个指标单独配置 XPath 和数据类型，故意全部留空，等待用户根据实际页面填写。
# kind 可选：currency=货币、integer=整数、percent=百分数。
# currency_code 按用户要求固定为巴西雷亚尔 BRL。
METRIC_SPECS: list[dict[str, str]] = [
    {"period": "7天", "field": "7天总销售额", "xpath": "", "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天已售单位", "xpath": "", "kind": "integer"},
    {"period": "7天", "field": "7天平均单价", "xpath": "", "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天访问", "xpath": "", "kind": "integer"},
    {"period": "7天", "field": "7天销售量", "xpath": "", "kind": "integer"},
    {"period": "7天", "field": "7天转换率", "xpath": "", "kind": "percent"},
    {"period": "7天", "field": "7天取消的销售数量", "xpath": "", "kind": "integer"},
    {"period": "7天", "field": "7天取消的销售价值", "xpath": "", "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天退货数量", "xpath": "", "kind": "integer"},
    {"period": "7天", "field": "7天退货价值", "xpath": "", "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天独特的参观", "xpath": "", "kind": "integer"},
    {"period": "7天", "field": "7天购买意向", "xpath": "", "kind": "integer"},
    {"period": "7天", "field": "7天独立意向转换率", "xpath": "", "kind": "percent"},
    {"period": "7天", "field": "7天意向购买转换率", "xpath": "", "kind": "percent"},
    {"period": "30天", "field": "30天总销售额", "xpath": "", "kind": "currency", "currency_code": "BRL"},
    {"period": "30天", "field": "30天已售单位", "xpath": "", "kind": "integer"},
    {"period": "30天", "field": "30天平均单价", "xpath": "", "kind": "currency", "currency_code": "BRL"},
    {"period": "30天", "field": "30天访问", "xpath": "", "kind": "integer"},
    {"period": "30天", "field": "30天销售量", "xpath": "", "kind": "integer"},
    {"period": "30天", "field": "30天转换率", "xpath": "", "kind": "percent"},
    {"period": "30天", "field": "30天取消的销售数量", "xpath": "", "kind": "integer"},
    {"period": "30天", "field": "30天取消的销售价值", "xpath": "", "kind": "currency", "currency_code": "BRL"},
    {"period": "30天", "field": "30天退货数量", "xpath": "", "kind": "integer"},
    {"period": "30天", "field": "30天退货价值", "xpath": "", "kind": "currency", "currency_code": "BRL"},
    {"period": "30天", "field": "30天独特的参观", "xpath": "", "kind": "integer"},
    {"period": "30天", "field": "30天购买意向", "xpath": "", "kind": "integer"},
    {"period": "30天", "field": "30天独立意向转换率", "xpath": "", "kind": "percent"},
    {"period": "30天", "field": "30天意向购买转换率", "xpath": "", "kind": "percent"},
]


class MercadoAuto:
    """美客多 7 天/30 天经营指标自动化。"""

    def __init__(self, config: dict[str, Any] | None = None):
        """保存美客多独立配置；指标 XPath 直接维护在本文件的 METRIC_SPECS。"""
        self.config = config or {}

    def collect(self, store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
        """接管紫鸟当前标签页，读取 28 个指标并返回一条多字段记录。"""
        if not debugging_port:
            raise RuntimeError("紫鸟没有返回 debuggingPort，无法接管美客多店铺")

        # 连接紫鸟已经打开的 Chromium，不创建普通浏览器，也不调用 tab.get()。
        browser = Chromium(f"127.0.0.1:{debugging_port}")
        tab = browser.latest_tab
        collected_at = datetime.now(timezone.utc).isoformat()
        feishu_fields: dict[str, Any] = {}
        raw_values: dict[str, str] = {}

        # 先点击进入经营数据页面，再分别读取 7 天和 30 天指标。
        self._run_click_steps(tab, COMMON_CLICK_STEPS)
        for period in ("7天", "30天"):
            self._run_click_steps(tab, PERIOD_CLICK_STEPS.get(period, []))
            for spec in METRIC_SPECS:
                if spec["period"] != period:
                    continue
                field_name = spec["field"]
                xpath = spec["xpath"]
                raw_text = self._read_xpath(tab, xpath)
                raw_values[field_name] = raw_text
                feishu_fields[field_name] = self._format_value(raw_text, spec["kind"])

        # “飞书字段”由 orchestrator 合并进已建立的同名多维表字段。
        # 标准字段仍保留，便于历史电子表和旧版数据表兼容。
        row = {
            "店铺名": store_name,
            "平台": "mercado",
            "采集时间": collected_at,
            "指标": "美客多7天/30天经营指标",
            "数值": "",
            "原始数据": json.dumps(raw_values, ensure_ascii=False),
            "飞书字段": feishu_fields,
        }
        return [row]

    @staticmethod
    def _run_click_steps(tab: Any, steps: list[dict[str, Any]]) -> None:
        """按顺序执行一组点击步骤；空 XPath 或单步失败都不会中断后续采集。"""
        for step in steps:
            xpath = str(step.get("xpath") or "").strip()
            if not xpath:
                # 尚未知道实际 XPath 时，保留步骤但安全跳过。
                continue
            try:
                element = tab.ele(f"xpath:{xpath}", timeout=3)
                if element:
                    element.click()
                    wait_seconds = float(step.get("wait_seconds", 1) or 0)
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)
            except Exception:
                # 某个按钮暂时不存在时，继续尝试后面的步骤和指标。
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
    def _format_value(raw_text: str, kind: str) -> Any:
        """按字段类型转换数据：货币两位小数、整数无小数、百分数无小数。"""
        if not raw_text:
            return ""
        if kind == "integer":
            # 数量类指标不应有小数；点号和逗号只当千位分隔符处理。
            integer_text = re.sub(r"[^0-9-]", "", str(raw_text))
            if not integer_text or integer_text == "-":
                return ""
            try:
                return int(integer_text)
            except ValueError:
                return ""
        number = MercadoAuto._parse_number(raw_text)
        if number is None:
            return ""
        if kind == "currency":
            return round(number, 2)
        if kind == "percent":
            # 页面通常带 %，飞书字段直接保存为“15%”这种无小数百分数文本。
            return f"{round(number):.0f}%"
        return raw_text

    @staticmethod
    def _parse_number(raw_text: str) -> float | None:
        """解析巴西常见格式，例如 ``R$ 1.234,56`` 或 ``12,5%``。"""
        text = str(raw_text).strip().replace("%", "")
        text = re.sub(r"[^0-9,.-]", "", text)
        if not text or text in {"-", ".", ","}:
            return None
        try:
            # 同时有点和逗号时，按巴西格式把点当千位分隔、逗号当小数点。
            if "." in text and "," in text:
                text = text.replace(".", "").replace(",", ".")
            elif "," in text:
                text = text.replace(",", ".")
            return float(text)
        except (TypeError, ValueError):
            return None


def collect_mercado_ad(store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
    """提供一个可直接调用的美客多函数入口。"""
    return MercadoAuto().collect(store_name, download_path, debugging_port)

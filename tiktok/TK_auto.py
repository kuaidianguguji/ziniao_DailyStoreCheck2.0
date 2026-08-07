"""TikTok 店铺广告和数据分析自动化。

本文件独立维护 TikTok 的 DrissionPage 连接、按钮点击、XPath、数值解析和飞书字段。
程序只接管紫鸟已经打开的当前标签页，不会主动访问任何网址。

所有按钮和指标 XPath 都集中维护在本文件顶部，方便单独调整 TikTok 流程；
以后新增 XPath 时，如果 XPath 为空、元素不存在或读取失败，仍默认返回空值，不会中断其他步骤和指标。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

from DrissionPage import Chromium


LOGGER = logging.getLogger(__name__)


# 首页弹窗和滑块验证码的关闭按钮。
# 验证码可能在任意阶段出现，因此点击、等待和读取指标前都会检查一次。
HOME_DIALOG_CLOSE_XPATH = '//div[@role="dialog"]//span[contains(@class,"core-modal-close-icon")]'
VERIFY_BAR_CLOSE_XPATH = '//a[@id="verify-bar-close"]'

# 页面加载和按钮操作参数。重试次数 3 表示“首次点击失败后再重试 3 次”。
PAGE_READY_TIMEOUT_SECONDS = 60
AFTER_PAGE_READY_WAIT_SECONDS = 10
CLICK_RETRY_TIMES = 3
CLICK_RETRY_INTERVAL_SECONDS = 2
NEXT_ELEMENT_TIMEOUT_SECONDS = 30

# 广告日期切换后，最长等待 30 秒确认五个广告指标均已加载且数值保持稳定。
# 连续两次读取结果一致，才认为本轮异步数据渲染已经结束。
AD_DATA_LOAD_TIMEOUT_SECONDS = 30
AD_DATA_STABLE_CHECKS = 2
AD_DATA_CHECK_INTERVAL_SECONDS = 2


# ---------------------------------------------------------------------------
# 一、店铺广告流程
# ---------------------------------------------------------------------------

# 广告流程按钮 XPath。单独定义是为了让“点击按钮”和“验证点击结果”使用完全相同的定位规则。
AD_MARKETING_BUTTON_XPATH = '//div[@class="p-menu-inline"]//div[@class="p-menu-item-title-txt"][normalize-space()="营销"]'
AD_STORE_BUTTON_XPATH = '//div[@class="p-menu-inline"]//div[@class="p-menu-item-title-txt"][normalize-space()="店铺广告"]'
AD_TIME_BUTTON_XPATH = '//span[@class="theme-arco-picker-suffix-icon"]'
AD_YESTERDAY_BUTTON_XPATH = '//button[contains(@class,"theme-arco-btn") and contains(@class,"theme-arco-btn-secondary") and contains(@class,"theme-arco-btn-size-mini") and contains(@class,"theme-arco-btn-shape-square")][contains(.,"昨") and contains(.,"天")]'
AD_7_DAYS_BUTTON_XPATH = '//button[contains(@class,"theme-arco-btn") and contains(@class,"theme-arco-btn-secondary") and contains(@class,"theme-arco-btn-size-mini") and contains(@class,"theme-arco-btn-shape-square")][contains(.,"近") and contains(.,"7") and contains(.,"天")]'


# 如果“店铺广告”已经可见，营销菜单已经展开，不再点击营销按钮，避免把菜单重新收起。
# 点击“店铺广告”后必须看见时间按钮，才确认已经真正进入广告页面。
AD_COMMON_CLICK_STEPS: list[dict[str, Any]] = [
    {
        "name": "点击营销按钮",
        "xpath": AD_MARKETING_BUTTON_XPATH,
        "wait_seconds": 1,
        "success_xpath": AD_STORE_BUTTON_XPATH,
        "success_state": "visible",
        "success_name": "店铺广告按钮已经可见",
    },
    {
        "name": "点击店铺广告",
        "xpath": AD_STORE_BUTTON_XPATH,
        "wait_seconds": 2,
        "success_xpath": AD_TIME_BUTTON_XPATH,
        "success_state": "visible",
        "success_name": "广告时间按钮已经出现",
    },
]


# 店铺广告时间范围切换步骤。每个时间范围都预留“打开时间”和“选择范围”两次点击。
AD_PERIOD_CLICK_STEPS: dict[str, list[dict[str, Any]]] = {
    "昨天": [
        {
            "name": "广告-点击时间按钮",
            "xpath": AD_TIME_BUTTON_XPATH,
            "wait_seconds": 1,
            "success_xpath": AD_YESTERDAY_BUTTON_XPATH,
            "success_state": "visible",
            "success_name": "昨天按钮已经出现",
        },
        {
            "name": "广告-点击昨天按钮",
            "xpath": AD_YESTERDAY_BUTTON_XPATH,
            "wait_seconds": 2,
            "success_xpath": AD_YESTERDAY_BUTTON_XPATH,
            "success_state": "hidden",
            "success_name": "昨天按钮已经消失",
        },
    ],
    "7天": [
        {
            "name": "广告-再次点击时间按钮",
            "xpath": AD_TIME_BUTTON_XPATH,
            "wait_seconds": 1,
            "success_xpath": AD_7_DAYS_BUTTON_XPATH,
            "success_state": "visible",
            "success_name": "最近7天按钮已经出现",
        },
        {
            "name": "广告-点击7天按钮",
            "xpath": AD_7_DAYS_BUTTON_XPATH,
            "wait_seconds": 2,
            "success_xpath": AD_7_DAYS_BUTTON_XPATH,
            "success_state": "hidden",
            "success_name": "最近7天按钮已经消失",
        },
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


# 概览中的 GMV 使用巴西雷亚尔 BRL；数量为整数。
# “直播/视频/商品卡”三个 XPath 实际抓到的是渠道 GMV 金额，不是页面占比。
# 昨天的渠道金额直接使用飞书已有字段名；7 天金额仅作为计算占比的临时值，计算后会从飞书字段中移除。
OVERVIEW_METRIC_SPECS: list[dict[str, str]] = [
    {"period": "昨天", "field": "昨天GMV", "xpath": '//div[@class="pcm-smc"][contains(.,"GMV")]//div[@class="pcm-smc-content"]', "kind": "currency", "currency_code": "BRL"},
    {"period": "昨天", "field": "昨天成交件数", "xpath": '//div[@class="pcm-smc"][contains(.,"商品成交件数")]//div[@class="pcm-smc-value-content"]', "kind": "integer"},
    {"period": "昨天", "field": "昨天SKU订单数", "xpath": '//div[@class="pcm-smc"][contains(.,"SKU 订单数")]//div[@class="pcm-smc-value-content"]', "kind": "integer"},
    {"period": "昨天", "field": "昨天订单数", "xpath": '//*[normalize-space(.)="订单数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "昨天", "field": "昨天客户数", "xpath": '//*[normalize-space(.)="客户数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "昨天", "field": "昨天商品访客数", "xpath": '//*[normalize-space(.)="商品访客数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "昨天", "field": "昨天曝光数", "xpath": '//*[normalize-space(.)="商品曝光次数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "昨天", "field": "昨天去重曝光数", "xpath": '//*[normalize-space(.)="去重商品曝光次数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "昨天", "field": "直播GMV", "xpath": '//*[normalize-space(.)="直播"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "currency", "currency_code": "BRL"},
    {"period": "昨天", "field": "短视频GMV", "xpath": '//*[normalize-space(.)="视频"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "currency", "currency_code": "BRL"},
    {"period": "昨天", "field": "商品卡GMV", "xpath": '//*[normalize-space(.)="商品卡"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天GMV", "xpath": '//div[@class="pcm-smc"][contains(.,"GMV")]//div[@class="pcm-smc-content"]', "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天成交件数", "xpath": '//div[@class="pcm-smc"][contains(.,"商品成交件数")]//div[@class="pcm-smc-value-content"]', "kind": "integer"},
    {"period": "7天", "field": "7天SKU订单数", "xpath": '//div[@class="pcm-smc"][contains(.,"SKU 订单数")]//div[@class="pcm-smc-value-content"]', "kind": "integer"},
    {"period": "7天", "field": "7天订单数", "xpath": '//*[normalize-space(.)="订单数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "7天", "field": "7天客户数", "xpath": '//*[normalize-space(.)="客户数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "7天", "field": "7天商品访客数", "xpath": '//*[normalize-space(.)="商品访客数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "7天", "field": "7天曝光数", "xpath": '//*[normalize-space(.)="商品曝光次数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "7天", "field": "7天去重曝光数", "xpath": '//*[normalize-space(.)="去重商品曝光次数"]/ancestor::div[contains(@class,"pcm-smc")][1]//div[contains(@class,"pcm-smc-value-content")]', "kind": "integer"},
    {"period": "7天", "field": "_7天直播GMV", "xpath": '//*[normalize-space(.)="直播"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "_7天短视频GMV", "xpath": '//*[normalize-space(.)="视频"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "_7天商品卡GMV", "xpath": '//*[normalize-space(.)="商品卡"]/ancestor::td[contains(@class,"core-table-td")][1]//div[contains(@class,"text-body-m-medium")]', "kind": "currency", "currency_code": "BRL"},
]


# 每个占比都由“同周期渠道 GMV / 同周期总 GMV * 100”计算，结果保留两位小数。
# remove_amount=True 表示该渠道金额只是内部计算变量，不对应当前飞书多维表字段。
OVERVIEW_GMV_RATIO_SPECS: dict[str, list[dict[str, Any]]] = {
    "昨天": [
        {"amount_field": "直播GMV", "ratio_field": "昨天GMV直播比", "remove_amount": False},
        {"amount_field": "短视频GMV", "ratio_field": "昨天GMV视频比", "remove_amount": False},
        {"amount_field": "商品卡GMV", "ratio_field": "昨天GMV商品卡比", "remove_amount": False},
    ],
    "7天": [
        {"amount_field": "_7天直播GMV", "ratio_field": "7天GMV直播比", "remove_amount": True},
        {"amount_field": "_7天短视频GMV", "ratio_field": "7天GMV视频比", "remove_amount": True},
        {"amount_field": "_7天商品卡GMV", "ratio_field": "7天GMV商品卡比", "remove_amount": True},
    ],
}


class TiktokAuto:
    """TikTok 店铺广告和数据概览自动化。"""

    def __init__(self, config: dict[str, Any] | None = None):
        """保存 TikTok 独立配置；按钮和指标 XPath 直接维护在本文件顶部。"""
        self.config = config or {}

    def collect(self, store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
        """接管紫鸟当前标签页，依次采集广告数据和数据分析概览。"""
        if not debugging_port:
            raise RuntimeError("紫鸟没有返回 debuggingPort，无法接管 TikTok 店铺")

        LOGGER.info("[TikTok][开始] 店铺=%s，准备接管紫鸟浏览器，debugging_port=%s", store_name, debugging_port)

        # 连接紫鸟已经打开的 Chromium，不创建普通浏览器，也不调用 tab.get()。
        browser = Chromium(f"127.0.0.1:{debugging_port}")
        tab = browser.latest_tab
        collected_at = datetime.now(timezone.utc).isoformat()
        LOGGER.info("[TikTok][浏览器] 店铺=%s，已取得紫鸟当前标签页", store_name)

        # 等待紫鸟当前页面完全加载，再额外等待 10 秒；等待期间穿插鼠标移动和弹窗检查。
        self._wait_for_page_ready(tab, PAGE_READY_TIMEOUT_SECONDS)
        LOGGER.info(
            "[TikTok][页面] 主文档等待结束，开始额外等待 %s 秒；期间执行鼠标移动并检查弹窗",
            AFTER_PAGE_READY_WAIT_SECONDS,
        )
        self._human_wait(tab, AFTER_PAGE_READY_WAIT_SECONDS, check_interruptions=True)
        self._close_interruptions(tab)
        LOGGER.info("[TikTok][页面] 额外等待结束，开始执行采集流程")

        # 1. 点击“营销 -> 店铺广告”，分别采集昨天和 7 天广告数据。
        LOGGER.info("[TikTok][广告] 开始进入 营销 -> 店铺广告")
        ad_fields: dict[str, Any] = {}
        ad_raw_values: dict[str, str] = {}
        ad_yesterday_steps = AD_PERIOD_CLICK_STEPS.get("昨天", [])
        ad_navigation_ok = self._run_click_steps(tab, AD_COMMON_CLICK_STEPS, self._first_step_xpath(ad_yesterday_steps))
        # 即使前面某一步曾经超时，只要最终已经看见时间按钮，仍可确认当前位于店铺广告页面。
        ad_page_ready = ad_navigation_ok or self._element_state_matches(tab, AD_TIME_BUTTON_XPATH, "visible")
        if not ad_page_ready:
            LOGGER.error("[TikTok][广告页面失败] 未确认广告时间按钮出现，本店铺两个广告周期均按空值处理")

        for period in ("昨天", "7天"):
            LOGGER.info("[TikTok][广告] 开始切换并采集时间范围=%s", period)
            if not ad_page_ready:
                self._record_empty_period_metrics(period, AD_METRIC_SPECS, ad_fields, ad_raw_values, "未进入店铺广告页面")
                continue

            # 点击日期前记录当前页面数值。日期按钮关闭后，新数据必须完整并稳定，才能开始正式读取。
            previous_snapshot = self._capture_metric_snapshot(tab, period, AD_METRIC_SPECS)
            LOGGER.info(
                "[TikTok][广告数据切换前] 时间范围=%s，当前页面指标快照=%s",
                period,
                json.dumps(previous_snapshot, ensure_ascii=False),
            )
            period_steps = AD_PERIOD_CLICK_STEPS.get(period, [])
            period_clicked = self._run_click_steps(tab, period_steps)
            if not period_clicked:
                self._record_empty_period_metrics(period, AD_METRIC_SPECS, ad_fields, ad_raw_values, "日期按钮点击或状态验证失败")
                continue

            data_loaded = self._wait_for_metric_data_loaded(
                tab,
                period,
                AD_METRIC_SPECS,
                previous_snapshot,
                AD_DATA_LOAD_TIMEOUT_SECONDS,
            )
            if not data_loaded:
                self._record_empty_period_metrics(period, AD_METRIC_SPECS, ad_fields, ad_raw_values, "广告数据未确认加载完成")
                continue
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
        LOGGER.info("[TikTok][概览] 开始进入 数据分析 -> 概览")
        overview_fields: dict[str, Any] = {}
        overview_raw_values: dict[str, str] = {}
        overview_yesterday_steps = OVERVIEW_PERIOD_CLICK_STEPS.get("昨天", [])
        self._run_click_steps(tab, OVERVIEW_COMMON_CLICK_STEPS, self._first_step_xpath(overview_yesterday_steps))
        for period in ("昨天", "7天"):
            LOGGER.info("[TikTok][概览] 开始切换并采集时间范围=%s", period)
            period_steps = OVERVIEW_PERIOD_CLICK_STEPS.get(period, [])
            first_metric_xpath = self._first_metric_xpath(OVERVIEW_METRIC_SPECS, period)
            self._run_click_steps(tab, period_steps, first_metric_xpath)
            self._collect_period_metrics(tab, period, OVERVIEW_METRIC_SPECS, overview_fields, overview_raw_values)
            self._calculate_gmv_ratios(period, overview_fields, overview_raw_values)

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
        ad_valid_count = sum(value != "" for value in ad_fields.values())
        overview_valid_count = sum(value != "" for value in overview_fields.values())
        LOGGER.info(
            "[TikTok][完成] 店铺=%s，广告有效指标=%s/%s，概览有效指标=%s/%s",
            store_name,
            ad_valid_count,
            len(ad_fields),
            overview_valid_count,
            len(overview_fields),
        )
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
            self._close_interruptions(tab)
            field_name = spec["field"]
            xpath = spec["xpath"]
            value_kind = spec["kind"]
            currency_code = spec.get("currency_code", "")
            LOGGER.info(
                "[TikTok][指标] 准备读取：时间范围=%s，字段=%s，配置类型=%s，币种=%s，xpath=%s",
                period,
                field_name,
                value_kind,
                currency_code or "无",
                xpath or "<空 XPath>",
            )
            raw_text = self._read_xpath(tab, xpath, field_name)
            converted_value = self._format_value(raw_text, value_kind, currency_code)
            raw_values[field_name] = raw_text
            field_values[field_name] = converted_value
            LOGGER.info(
                "[TikTok][指标结果] 字段=%s，原始值=%r，原始类型=%s，转换值=%r，转换后类型=%s，配置类型=%s，币种=%s",
                field_name,
                raw_text,
                type(raw_text).__name__,
                converted_value,
                type(converted_value).__name__,
                value_kind,
                currency_code or "无",
            )
            if raw_text == "":
                LOGGER.warning("[TikTok][指标失败] 字段=%s，XPath 未抓到有效文本，最终按空值处理", field_name)
            elif converted_value == "":
                LOGGER.warning(
                    "[TikTok][转换失败] 字段=%s，已抓到原始值=%r，但无法按配置类型=%s 转换，最终按空值处理",
                    field_name,
                    raw_text,
                    value_kind,
                )

    def _capture_metric_snapshot(
        self,
        tab: Any,
        period: str,
        specs: list[dict[str, str]],
    ) -> dict[str, str]:
        """快速读取某周期的指标文本，用于判断日期切换后的异步数据是否已经刷新。"""
        snapshot: dict[str, str] = {}
        for spec in specs:
            if spec["period"] != period:
                continue
            field_name = spec["field"]
            xpath = str(spec.get("xpath") or "").strip()
            raw_text = ""
            try:
                element = self._find_visible_element(tab, xpath, timeout=1)
                if element:
                    raw_text = str(element.text or "").strip()
            except Exception:
                raw_text = ""
            snapshot[field_name] = raw_text
        return snapshot

    def _wait_for_metric_data_loaded(
        self,
        tab: Any,
        period: str,
        specs: list[dict[str, str]],
        previous_snapshot: dict[str, str],
        timeout_seconds: float,
    ) -> bool:
        """等待日期切换后的指标全部非空、发生刷新并连续保持稳定。"""
        expected_count = sum(1 for spec in specs if spec["period"] == period)
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        last_snapshot: dict[str, str] = {}
        stable_checks = 0
        loading_state_observed = False

        LOGGER.info(
            "[TikTok][广告数据等待] 时间范围=%s，等待 %s 个指标加载并连续稳定 %s 次，最长 %.1f 秒",
            period,
            expected_count,
            AD_DATA_STABLE_CHECKS,
            timeout_seconds,
        )
        while time.monotonic() < deadline:
            self._close_interruptions(tab)
            current_snapshot = self._capture_metric_snapshot(tab, period, specs)
            ready_count = sum(bool(value) for value in current_snapshot.values())
            all_ready = expected_count > 0 and ready_count == expected_count

            # 指标曾经消失或变空，说明页面确实进入过加载状态；即使新旧数值相同，也可据此确认刷新发生过。
            if not all_ready:
                loading_state_observed = True
                stable_checks = 0
            else:
                data_changed = current_snapshot != previous_snapshot
                refresh_confirmed = data_changed or loading_state_observed or not any(previous_snapshot.values())
                if refresh_confirmed:
                    stable_checks = stable_checks + 1 if current_snapshot == last_snapshot else 1
                else:
                    stable_checks = 0

                if stable_checks >= AD_DATA_STABLE_CHECKS:
                    LOGGER.info(
                        "[TikTok][广告数据加载完成] 时间范围=%s，指标=%s/%s，连续稳定=%s次，耗时=%.2f秒，最终快照=%s",
                        period,
                        ready_count,
                        expected_count,
                        stable_checks,
                        time.monotonic() - started_at,
                        json.dumps(current_snapshot, ensure_ascii=False),
                    )
                    return True

            if current_snapshot != last_snapshot:
                LOGGER.info(
                    "[TikTok][广告数据状态] 时间范围=%s，已加载=%s/%s，是否较切换前变化=%s，稳定次数=%s，快照=%s",
                    period,
                    ready_count,
                    expected_count,
                    current_snapshot != previous_snapshot,
                    stable_checks,
                    json.dumps(current_snapshot, ensure_ascii=False),
                )
            last_snapshot = current_snapshot
            self._human_wait(tab, AD_DATA_CHECK_INTERVAL_SECONDS, check_interruptions=True)

        # 所有值始终与切换前完全相同时，无法从数值变化证明刷新；等待满 30 秒后接受完整且稳定的页面。
        final_ready_count = sum(bool(value) for value in last_snapshot.values())
        if expected_count > 0 and final_ready_count == expected_count and last_snapshot == previous_snapshot:
            LOGGER.warning(
                "[TikTok][广告数据无变化] 时间范围=%s，等待 %.1f 秒后全部指标仍与切换前一致；按完整稳定数据继续读取，最终快照=%s",
                period,
                timeout_seconds,
                json.dumps(last_snapshot, ensure_ascii=False),
            )
            return True

        LOGGER.error(
            "[TikTok][广告数据加载超时] 时间范围=%s，等待 %.1f 秒后仅加载=%s/%s，不读取本周期数据，最终快照=%s",
            period,
            timeout_seconds,
            final_ready_count,
            expected_count,
            json.dumps(last_snapshot, ensure_ascii=False),
        )
        return False

    @staticmethod
    def _record_empty_period_metrics(
        period: str,
        specs: list[dict[str, str]],
        field_values: dict[str, Any],
        raw_values: dict[str, str],
        reason: str,
    ) -> None:
        """流程未通过加载验证时写入空值，防止误用上一个日期范围的旧数据。"""
        empty_fields: list[str] = []
        for spec in specs:
            if spec["period"] != period:
                continue
            field_name = spec["field"]
            field_values[field_name] = ""
            raw_values[field_name] = ""
            empty_fields.append(field_name)
        LOGGER.error("[TikTok][周期数据置空] 时间范围=%s，原因=%s，字段=%s", period, reason, empty_fields)

    @staticmethod
    def _calculate_gmv_ratios(
        period: str,
        field_values: dict[str, Any],
        raw_values: dict[str, str],
    ) -> None:
        """用渠道 GMV 金额除以同周期总 GMV，生成直播、视频和商品卡占比。"""
        total_field = f"{period}GMV"
        total_value = field_values.get(total_field, "")
        total_is_number = isinstance(total_value, (int, float)) and not isinstance(total_value, bool)

        for ratio_spec in OVERVIEW_GMV_RATIO_SPECS.get(period, []):
            amount_field = str(ratio_spec["amount_field"])
            ratio_field = str(ratio_spec["ratio_field"])
            amount_value = field_values.get(amount_field, "")
            amount_is_number = isinstance(amount_value, (int, float)) and not isinstance(amount_value, bool)

            # 原始数据中保留明确的计算表达式，排查时能看到分子、分母分别来自哪个 XPath 结果。
            amount_raw = raw_values.get(amount_field, "")
            total_raw = raw_values.get(total_field, "")
            raw_values[ratio_field] = f"渠道GMV={amount_raw!r}; 总GMV={total_raw!r}"

            if amount_is_number and total_is_number and total_value > 0:
                ratio_number = amount_value / total_value * 100
                ratio_value = f"{ratio_number:.2f}%"
                field_values[ratio_field] = ratio_value
                LOGGER.info(
                    "[TikTok][GMV占比计算成功] 时间范围=%s，字段=%s，渠道金额=%r(%s)，总GMV=%r(%s)，计算结果=%r(%s)",
                    period,
                    ratio_field,
                    amount_value,
                    type(amount_value).__name__,
                    total_value,
                    type(total_value).__name__,
                    ratio_value,
                    type(ratio_value).__name__,
                )
            else:
                field_values[ratio_field] = ""
                LOGGER.warning(
                    "[TikTok][GMV占比计算失败] 时间范围=%s，字段=%s，渠道金额=%r，总GMV=%r；金额缺失或总GMV不大于0，返回空值",
                    period,
                    ratio_field,
                    amount_value,
                    total_value,
                )

            # 当前飞书表没有 7 天渠道 GMV 金额字段，只保留算出的占比，避免提交不存在的临时字段。
            if bool(ratio_spec.get("remove_amount")):
                field_values.pop(amount_field, None)

    def _run_click_steps(self, tab: Any, steps: list[dict[str, Any]], final_next_xpath: str = "") -> bool:
        """按顺序点击按钮；点击后的目标状态也必须满足，才把该步骤判定为成功。"""
        all_steps_succeeded = True
        for index, step in enumerate(steps):
            step_name = str(step.get("name") or f"第 {index + 1} 个未命名按钮")
            xpath = str(step.get("xpath") or "").strip()
            if not xpath:
                LOGGER.warning("[TikTok][按钮跳过] 步骤=%s，原因=XPath 为空", step_name)
                all_steps_succeeded = False
                continue

            LOGGER.info("[TikTok][按钮] 准备点击：步骤=%s，xpath=%s", step_name, xpath)
            clicked = self._click_with_retry(
                tab,
                xpath,
                step_name,
                success_xpath=str(step.get("success_xpath") or "").strip(),
                success_state=str(step.get("success_state") or "").strip().lower(),
                success_name=str(step.get("success_name") or "点击后的页面状态"),
            )
            if not clicked:
                all_steps_succeeded = False
                LOGGER.error("[TikTok][按钮失败] 步骤=%s，首次及 3 次重试的点击或状态验证均未成功", step_name)
                continue

            wait_seconds = float(step.get("wait_seconds", 1) or 0)
            if wait_seconds > 0:
                LOGGER.info("[TikTok][按钮] 步骤=%s 点击成功，先等待 %.1f 秒", step_name, wait_seconds)
                self._human_wait(tab, wait_seconds, check_interruptions=True)

            next_xpath = self._next_step_xpath(steps, index + 1)
            next_name = "后续按钮"
            if not next_xpath:
                next_xpath = final_next_xpath
                next_name = "当前模块的首个数据指标"
            if next_xpath and not self._wait_for_xpath(
                tab,
                next_xpath,
                NEXT_ELEMENT_TIMEOUT_SECONDS,
                f"{step_name} 后的{next_name}",
            ):
                all_steps_succeeded = False
        return all_steps_succeeded

    def _click_with_retry(
        self,
        tab: Any,
        xpath: str,
        step_name: str = "未命名按钮",
        success_xpath: str = "",
        success_state: str = "",
        success_name: str = "点击后的页面状态",
    ) -> bool:
        """按钮最多点击 4 次；DOM 点击成功且预期页面状态满足，才返回成功。"""
        max_attempts = CLICK_RETRY_TIMES + 1
        for attempt in range(max_attempts):
            # 对“展开菜单、打开面板”类步骤，目标元素已经可见就说明当前状态正确，不应再次点击把它关闭。
            if success_xpath and success_state == "visible" and self._element_state_matches(tab, success_xpath, "visible"):
                LOGGER.info("[TikTok][按钮验证成功] 步骤=%s，%s，无需再次点击", step_name, success_name)
                return True
            # 对“选择日期后面板消失”类步骤，仅从第二次尝试开始预检，防止首次点击前把原本隐藏误判为成功。
            if attempt > 0 and success_xpath and success_state == "hidden" and self._element_state_matches(tab, success_xpath, "hidden"):
                LOGGER.info("[TikTok][按钮验证成功] 步骤=%s，%s，上一次点击已经延迟生效", step_name, success_name)
                return True

            if attempt > 0:
                # 在固定 2 秒基础上增加少量随机等待，避免点击节奏完全一致。
                interval = CLICK_RETRY_INTERVAL_SECONDS + random.uniform(0, 1)
                LOGGER.warning(
                    "[TikTok][按钮重试] 步骤=%s，第 %s/%s 次尝试前等待 %.2f 秒，xpath=%s",
                    step_name,
                    attempt + 1,
                    max_attempts,
                    interval,
                    xpath,
                )
                self._human_wait(tab, interval, check_interruptions=True)

            self._close_interruptions(tab)
            try:
                LOGGER.info(
                    "[TikTok][按钮查找] 步骤=%s，第 %s/%s 次尝试，xpath=%s",
                    step_name,
                    attempt + 1,
                    max_attempts,
                    xpath,
                )
                element = self._find_visible_element(tab, xpath, timeout=5)
                if not element:
                    LOGGER.warning(
                        "[TikTok][按钮未找到] 步骤=%s，第 %s/%s 次尝试未找到可见元素",
                        step_name,
                        attempt + 1,
                        max_attempts,
                    )
                    continue
                self._human_mouse_move(tab)
                element.click()
                self._close_interruptions(tab)

                if success_xpath and success_state:
                    LOGGER.info("[TikTok][按钮已点击] 步骤=%s，开始验证=%s", step_name, success_name)
                    verified = self._wait_for_element_state(
                        tab,
                        success_xpath,
                        success_state,
                        NEXT_ELEMENT_TIMEOUT_SECONDS,
                        success_name,
                    )
                    if not verified:
                        LOGGER.warning(
                            "[TikTok][按钮验证失败] 步骤=%s，第 %s/%s 次点击后未满足=%s，准备重试",
                            step_name,
                            attempt + 1,
                            max_attempts,
                            success_name,
                        )
                        continue
                LOGGER.info(
                    "[TikTok][按钮成功] 步骤=%s，第 %s/%s 次点击并验证成功，xpath=%s",
                    step_name,
                    attempt + 1,
                    max_attempts,
                    xpath,
                )
                return True
            except Exception as exc:
                LOGGER.warning(
                    "[TikTok][按钮异常] 步骤=%s，第 %s/%s 次尝试失败，异常=%s，xpath=%s",
                    step_name,
                    attempt + 1,
                    max_attempts,
                    exc,
                    xpath,
                )
                continue
        LOGGER.error("[TikTok][按钮终止] 步骤=%s，全部 %s 次点击均失败，xpath=%s", step_name, max_attempts, xpath)
        return False

    def _wait_for_element_state(
        self,
        tab: Any,
        xpath: str,
        expected_state: str,
        timeout_seconds: float,
        target_name: str,
    ) -> bool:
        """等待按钮或面板出现/消失；消失连续确认两次，避免瞬时查询失败造成误判。"""
        if expected_state not in {"visible", "hidden"}:
            LOGGER.error("[TikTok][状态配置错误] 目标=%s，不支持 expected_state=%s", target_name, expected_state)
            return False

        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        hidden_checks = 0
        LOGGER.info(
            "[TikTok][状态等待] 目标=%s，期望=%s，最长 %.1f 秒，xpath=%s",
            target_name,
            expected_state,
            timeout_seconds,
            xpath,
        )
        while time.monotonic() < deadline:
            self._close_interruptions(tab)
            visible = bool(self._find_visible_element(tab, xpath, timeout=1))
            if expected_state == "visible" and visible:
                LOGGER.info("[TikTok][状态满足] 目标=%s 已出现，耗时 %.2f 秒", target_name, time.monotonic() - started_at)
                return True
            if expected_state == "hidden":
                if visible:
                    hidden_checks = 0
                else:
                    hidden_checks += 1
                    if hidden_checks >= 2:
                        LOGGER.info("[TikTok][状态满足] 目标=%s 连续两次不可见，确认已经消失", target_name)
                        return True
            self._human_wait(tab, 0.5, check_interruptions=True)

        LOGGER.error(
            "[TikTok][状态等待超时] 目标=%s，等待 %.1f 秒仍未达到=%s，xpath=%s",
            target_name,
            timeout_seconds,
            expected_state,
            xpath,
        )
        return False

    def _element_state_matches(self, tab: Any, xpath: str, expected_state: str) -> bool:
        """立即检查元素当前是否符合 visible 或 hidden 状态。"""
        visible = bool(self._find_visible_element(tab, xpath, timeout=0.5))
        if expected_state == "visible":
            return visible
        if expected_state == "hidden":
            return not visible
        return False

    @staticmethod
    def _find_visible_element(tab: Any, xpath: str, timeout: float = 1) -> Any:
        """查找真正可见的元素，并兼容 DrissionPage 不同版本的可见性属性。"""
        if not xpath:
            return None
        try:
            element = tab.ele(f"xpath:{xpath}", timeout=timeout)
            if not element:
                return None

            states = getattr(element, "states", None)
            displayed = getattr(states, "is_displayed", None) if states is not None else None
            if callable(displayed):
                displayed = displayed()
            if displayed is not None:
                return element if bool(displayed) else None

            legacy_displayed = getattr(element, "is_displayed", None)
            if callable(legacy_displayed):
                legacy_displayed = legacy_displayed()
            if legacy_displayed is not None:
                return element if bool(legacy_displayed) else None
            return element
        except Exception:
            return None

    def _wait_for_page_ready(self, tab: Any, timeout_seconds: float) -> bool:
        """轮询 document.readyState，等待网站主文档加载完成。"""
        started_at = time.monotonic()
        deadline = time.monotonic() + timeout_seconds
        last_ready_state = ""
        LOGGER.info("[TikTok][页面等待] 开始等待 document.readyState=complete，最长 %.1f 秒", timeout_seconds)
        while time.monotonic() < deadline:
            self._close_interruptions(tab)
            try:
                ready_state = tab.run_js("return document.readyState;")
                ready_state_text = str(ready_state).lower()
                if ready_state_text != last_ready_state:
                    LOGGER.info("[TikTok][页面状态] document.readyState=%s", ready_state_text)
                    last_ready_state = ready_state_text
                if ready_state_text == "complete":
                    LOGGER.info("[TikTok][页面成功] 主文档加载完成，耗时 %.2f 秒", time.monotonic() - started_at)
                    return True
            except Exception as exc:
                LOGGER.warning("[TikTok][页面异常] 读取 document.readyState 失败：%s", exc)
            self._human_wait(tab, 1, check_interruptions=True)
        LOGGER.error("[TikTok][页面超时] 等待 %.1f 秒后仍未检测到 document.readyState=complete，继续执行", timeout_seconds)
        return False

    def _wait_for_xpath(self, tab: Any, xpath: str, timeout_seconds: float, target_name: str = "下一元素") -> bool:
        """成功点击后轮询下一元素，最长等待 30 秒。"""
        if not xpath:
            LOGGER.warning("[TikTok][元素等待跳过] 目标=%s，原因=XPath 为空", target_name)
            return True
        started_at = time.monotonic()
        deadline = time.monotonic() + timeout_seconds
        check_count = 0
        LOGGER.info("[TikTok][元素等待] 目标=%s，最长等待 %.1f 秒，xpath=%s", target_name, timeout_seconds, xpath)
        while time.monotonic() < deadline:
            check_count += 1
            self._close_interruptions(tab)
            try:
                if self._find_visible_element(tab, xpath, timeout=1):
                    LOGGER.info(
                        "[TikTok][元素出现] 目标=%s，第 %s 次检查找到元素，耗时 %.2f 秒，xpath=%s",
                        target_name,
                        check_count,
                        time.monotonic() - started_at,
                        xpath,
                    )
                    return True
            except Exception as exc:
                LOGGER.warning("[TikTok][元素检查异常] 目标=%s，第 %s 次检查异常=%s", target_name, check_count, exc)
            self._human_wait(tab, 1, check_interruptions=True)
        LOGGER.error(
            "[TikTok][元素等待超时] 目标=%s，等待 %.1f 秒仍未出现，xpath=%s",
            target_name,
            timeout_seconds,
            xpath,
        )
        return False

    def _human_wait(self, tab: Any, seconds: float, check_interruptions: bool = False) -> None:
        """分段等待并穿插鼠标移动，避免长时间完全静止。"""
        deadline = time.monotonic() + max(0, seconds)
        while time.monotonic() < deadline:
            if check_interruptions:
                self._close_interruptions(tab)
            self._human_mouse_move(tab)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, random.uniform(0.8, 1.5)))

    @staticmethod
    def _human_mouse_move(tab: Any) -> None:
        """优先用 DrissionPage Actions 移动鼠标，失败时退回页面 mousemove 事件。"""
        x = random.randint(80, 700)
        y = random.randint(80, 500)
        try:
            from DrissionPage import Actions

            Actions(tab).move_to((x, y), duration=random.uniform(0.2, 0.6))
            return
        except Exception:
            pass
        try:
            tab.run_js(
                "document.dispatchEvent(new MouseEvent('mousemove', "
                f"{{clientX:{x}, clientY:{y}, bubbles:true}}));"
            )
        except Exception:
            pass

    def _close_interruptions(self, tab: Any) -> None:
        """关闭首页弹窗和验证码；关闭按钮失败时同样最多重试 3 次。"""
        interruption_buttons = (
            ("验证码提示", VERIFY_BAR_CLOSE_XPATH),
            ("首页弹窗", HOME_DIALOG_CLOSE_XPATH),
        )
        for interruption_name, xpath in interruption_buttons:
            detected = False
            closed = False
            for attempt in range(CLICK_RETRY_TIMES + 1):
                try:
                    element = tab.ele(f"xpath:{xpath}", timeout=0.3)
                    if not element:
                        if detected:
                            LOGGER.info("[TikTok][干扰消失] %s关闭按钮已不再出现", interruption_name)
                            closed = True
                        break
                    if not detected:
                        LOGGER.warning("[TikTok][发现干扰] 检测到%s，xpath=%s", interruption_name, xpath)
                        detected = True
                    self._human_mouse_move(tab)
                    element.click()
                    time.sleep(0.5)
                    LOGGER.info(
                        "[TikTok][干扰关闭成功] %s，第 %s/%s 次点击成功",
                        interruption_name,
                        attempt + 1,
                        CLICK_RETRY_TIMES + 1,
                    )
                    closed = True
                    break
                except Exception as exc:
                    LOGGER.warning(
                        "[TikTok][干扰关闭失败] %s，第 %s/%s 次失败，异常=%s",
                        interruption_name,
                        attempt + 1,
                        CLICK_RETRY_TIMES + 1,
                        exc,
                    )
                    if attempt < CLICK_RETRY_TIMES:
                        time.sleep(CLICK_RETRY_INTERVAL_SECONDS + random.uniform(0, 1))
            if detected and not closed:
                LOGGER.error("[TikTok][干扰未关闭] %s连续 %s 次关闭失败，程序继续执行", interruption_name, CLICK_RETRY_TIMES + 1)

    @staticmethod
    def _next_step_xpath(steps: list[dict[str, Any]], start_index: int) -> str:
        """从后续步骤中找到第一个非空 XPath。"""
        for step in steps[start_index:]:
            xpath = str(step.get("xpath") or "").strip()
            if xpath:
                return xpath
        return ""

    @staticmethod
    def _first_step_xpath(steps: list[dict[str, Any]]) -> str:
        """返回一组点击步骤中的第一个非空 XPath。"""
        return TiktokAuto._next_step_xpath(steps, 0)

    @staticmethod
    def _first_metric_xpath(specs: list[dict[str, str]], period: str) -> str:
        """返回指定时间范围的第一个指标 XPath，用于点击后等待页面数据出现。"""
        for spec in specs:
            if spec["period"] == period and spec.get("xpath"):
                return spec["xpath"]
        return ""

    def _read_xpath(self, tab: Any, xpath: str, field_name: str = "未命名指标") -> str:
        """读取一个 XPath 文本；XPath 为空、元素不存在或异常时返回空字符串。"""
        if not xpath:
            LOGGER.warning("[TikTok][指标跳过] 字段=%s，原因=XPath 为空", field_name)
            return ""
        # 指标读取也检查一次验证码；首次失败后再等 2 秒重读一次。
        for attempt in range(2):
            if attempt > 0:
                LOGGER.warning(
                    "[TikTok][指标重试] 字段=%s，第 2/2 次读取前等待 %s 秒，xpath=%s",
                    field_name,
                    CLICK_RETRY_INTERVAL_SECONDS,
                    xpath,
                )
                self._human_wait(tab, CLICK_RETRY_INTERVAL_SECONDS, check_interruptions=True)
            self._close_interruptions(tab)
            try:
                LOGGER.info("[TikTok][指标查找] 字段=%s，第 %s/2 次读取，xpath=%s", field_name, attempt + 1, xpath)
                element = tab.ele(f"xpath:{xpath}", timeout=3)
                if element:
                    raw_text = str(element.text or "").strip()
                    if raw_text:
                        LOGGER.info("[TikTok][指标抓取成功] 字段=%s，原始文本=%r", field_name, raw_text)
                        return raw_text
                    LOGGER.warning("[TikTok][指标文本为空] 字段=%s，已找到元素但 text 为空", field_name)
                else:
                    LOGGER.warning("[TikTok][指标未找到] 字段=%s，第 %s/2 次未找到元素", field_name, attempt + 1)
            except Exception as exc:
                LOGGER.warning(
                    "[TikTok][指标异常] 字段=%s，第 %s/2 次读取失败，异常=%s，xpath=%s",
                    field_name,
                    attempt + 1,
                    exc,
                    xpath,
                )
                continue
        LOGGER.error("[TikTok][指标最终失败] 字段=%s，两次读取均未得到有效文本，返回空字符串", field_name)
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
        """解析第一个数值，避免把同一元素后面的趋势百分比拼接到金额中。"""
        text = str(raw_text).strip()
        # 页面可能把整数、小数点和小数部分拆成多行，先清理分隔符两侧的换行和空格。
        text = re.sub(r"\s*([.,])\s*", r"\1", text)

        if currency_code == "USD":
            # 美元示例：$1,234.56。只提取第一个金额，忽略其后的环比/同比百分数。
            match = re.search(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text)
            if not match:
                return None
            text = match.group(0).replace(",", "")
        elif currency_code == "BRL":
            # 巴西币示例：R$ 1.234,56。只提取第一个金额，忽略其后的趋势信息。
            match = re.search(r"-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?", text)
            if not match:
                return None
            text = match.group(0).replace(".", "").replace(",", ".")
        else:
            text = text.replace("%", "")
            text = re.sub(r"[^0-9,.-]", "", text)

        if not text or text in {"-", ".", ","}:
            return None
        try:
            if not currency_code and "." in text and "," in text:
                # 无币种指标按最后出现的符号判断小数点。
                if text.rfind(",") > text.rfind("."):
                    text = text.replace(".", "").replace(",", ".")
                else:
                    text = text.replace(",", "")
            elif not currency_code and "," in text:
                text = text.replace(",", ".")
            return float(text)
        except (TypeError, ValueError):
            return None


def collect_tiktok_ad(store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
    """提供一个可直接调用的 TikTok 函数入口。"""
    return TiktokAuto().collect(store_name, download_path, debugging_port)

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
        self._run_click_steps(tab, AD_COMMON_CLICK_STEPS, self._first_step_xpath(ad_yesterday_steps))
        for period in ("昨天", "7天"):
            LOGGER.info("[TikTok][广告] 开始切换并采集时间范围=%s", period)
            period_steps = AD_PERIOD_CLICK_STEPS.get(period, [])
            first_metric_xpath = self._first_metric_xpath(AD_METRIC_SPECS, period)
            self._run_click_steps(tab, period_steps, first_metric_xpath)
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

    def _run_click_steps(self, tab: Any, steps: list[dict[str, Any]], final_next_xpath: str = "") -> None:
        """按顺序点击按钮，并在成功后最多等待 30 秒让下一元素出现。"""
        for index, step in enumerate(steps):
            step_name = str(step.get("name") or f"第 {index + 1} 个未命名按钮")
            xpath = str(step.get("xpath") or "").strip()
            if not xpath:
                LOGGER.warning("[TikTok][按钮跳过] 步骤=%s，原因=XPath 为空", step_name)
                continue

            LOGGER.info("[TikTok][按钮] 准备点击：步骤=%s，xpath=%s", step_name, xpath)
            clicked = self._click_with_retry(tab, xpath, step_name)
            if not clicked:
                # 当前按钮最终失败时继续后续步骤，最终指标按空值处理。
                LOGGER.error("[TikTok][按钮失败] 步骤=%s，首次及 3 次重试均未成功，继续后续流程", step_name)
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
            self._wait_for_xpath(tab, next_xpath, NEXT_ELEMENT_TIMEOUT_SECONDS, f"{step_name} 后的{next_name}")

    def _click_with_retry(self, tab: Any, xpath: str, step_name: str = "未命名按钮") -> bool:
        """按钮最多点击 4 次，相邻尝试至少间隔 2 秒，并持续处理验证码。"""
        max_attempts = CLICK_RETRY_TIMES + 1
        for attempt in range(max_attempts):
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
                element = tab.ele(f"xpath:{xpath}", timeout=5)
                if not element:
                    LOGGER.warning(
                        "[TikTok][按钮未找到] 步骤=%s，第 %s/%s 次尝试未找到元素",
                        step_name,
                        attempt + 1,
                        max_attempts,
                    )
                    continue
                self._human_mouse_move(tab)
                element.click()
                self._close_interruptions(tab)
                LOGGER.info(
                    "[TikTok][按钮成功] 步骤=%s，第 %s/%s 次尝试点击成功，xpath=%s",
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
                if tab.ele(f"xpath:{xpath}", timeout=1):
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

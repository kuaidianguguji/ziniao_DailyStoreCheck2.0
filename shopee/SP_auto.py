"""Shopee 店铺广告自动化和数据采集。

本文件独立维护 Shopee 的 DrissionPage 连接、菜单操作、日期切换、指标读取、
数值转换、日志和结果组装。程序只接管紫鸟已经打开的当前标签页，不会主动访问网址。

尚未提供的 XPath 统一留在本文件顶部。XPath 为空、元素不存在或转换失败时，
对应指标返回空值并记录日志，不会影响其他指标继续执行。
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


# ---------------------------------------------------------------------------
# 一、Shopee 广告页面按钮 XPath
# ---------------------------------------------------------------------------

# 如果“Shopee广告”不可见，需要先点击“营销中心”展开菜单。
MARKETING_CENTER_BUTTON_XPATH = '(//ul[@class="sidebar-menu"]/li)[3]//span[@class="sidebar-menu-item-text"]'

# “Shopee广告”菜单按钮。
SHOPEE_AD_BUTTON_XPATH = '//a[contains(@href,"/portal/marketing/pas/index")]'

# 广告页面的时间切换按钮。
TIME_SWITCH_BUTTON_XPATH = '//div[@class="eds-popover__ref"]//div[@class="eds-date-picker__input"]'

# 时间面板中的“昨天”和“最近7天”选项。
YESTERDAY_OPTION_XPATH = '//ul[@class="eds-date-shortcut-list"]/li[2]'
LAST_7_DAYS_OPTION_XPATH = '//ul[@class="eds-date-shortcut-list"]/li[3]'


# 页面主文档加载完成的最长等待时间，单位为秒。
# 超过 60 秒仍未达到 document.readyState=complete 时会记录错误日志，然后继续执行。
PAGE_READY_TIMEOUT_SECONDS = 60

# 页面主文档加载完成后额外等待的时间，单位为秒。
# 这 10 秒用于等待 Shopee 的菜单、弹窗和异步页面内容继续渲染。
AFTER_PAGE_READY_WAIT_SECONDS = 10

# 按钮首次点击失败后允许再次重试的次数。
# 当前值为 3，表示“首次点击 1 次 + 失败后重试 3 次”，所以一个按钮最多点击 4 次。
CLICK_RETRY_TIMES = 3

# 同一个按钮前后两次点击尝试之间的最短间隔，单位为秒。
# 实际重试时会在 2 秒基础上增加 0～1 秒随机等待，避免连续机械点击。
CLICK_RETRY_INTERVAL_SECONDS = 2

# 点击按钮后，等待下一个按钮、日期选项或页面数据出现的最长时间，单位为秒。
# 如果没有可用于判断加载完成的 XPath，也会使用这个值固定等待 30 秒。
NEXT_ELEMENT_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# 二、昨天和最近 7 天的日期切换步骤
# ---------------------------------------------------------------------------

# 日期按钮只有在日期选项出现后才算点击成功；日期选项只有在点击后消失才算成功。
# 用户补充上面的四个 XPath 后，本列表会自动使用相同的 XPath。
PERIOD_CLICK_STEPS: dict[str, list[dict[str, Any]]] = {
    "昨天": [
        {
            "name": "广告-打开时间选择面板（昨天）",
            "xpath": TIME_SWITCH_BUTTON_XPATH,
            "scroll_to_center": True,
            "wait_seconds": 1,
            "success_xpath": YESTERDAY_OPTION_XPATH,
            "success_state": "visible",
            "success_name": "昨天选项出现",
        },
        {
            "name": "广告-选择昨天",
            "xpath": YESTERDAY_OPTION_XPATH,
            "wait_seconds": 2,
            "success_xpath": YESTERDAY_OPTION_XPATH,
            "success_state": "hidden",
            "success_name": "昨天选项消失",
        },
    ],
    "7天": [
        {
            "name": "广告-打开时间选择面板（7天）",
            "xpath": TIME_SWITCH_BUTTON_XPATH,
            "scroll_to_center": True,
            "wait_seconds": 1,
            "success_xpath": LAST_7_DAYS_OPTION_XPATH,
            "success_state": "visible",
            "success_name": "最近7天选项出现",
        },
        {
            "name": "广告-选择最近7天",
            "xpath": LAST_7_DAYS_OPTION_XPATH,
            "wait_seconds": 2,
            "success_xpath": LAST_7_DAYS_OPTION_XPATH,
            "success_state": "hidden",
            "success_name": "最近7天选项消失",
        },
    ],
}


# ---------------------------------------------------------------------------
# 三、Shopee 广告 ALL 行指标
# ---------------------------------------------------------------------------

# kind 可选值：
# integer=整数；percent=百分比文本；currency=巴西雷亚尔两位小数；decimal=普通两位小数。
# 同一指标在昨天和 7 天页面通常使用相同 XPath，但仍分别保留，方便页面差异化维护。
# 用户补充 XPath 时，金额使用 kind=currency，数量使用 kind=integer，百分比使用 kind=percent。
METRIC_SPECS: list[dict[str, str]] = [
    {"period": "昨天", "field": "昨天ALL展示次数", "xpath": '//div[@class="line-metrics"]/div[1]//div[@class="content"]//span', "kind": "integer"},
    {"period": "昨天", "field": "昨天ALL点击数", "xpath": '//div[@class="line-metrics"]/div[2]//div[@class="content"]//span', "kind": "integer"},
    {"period": "昨天", "field": "昨天ALL点击率", "xpath": '//div[@class="line-metrics"]/div[3]//div[@class="content"]//span', "kind": "percent"},
    {"period": "昨天", "field": "昨天ALL订单量", "xpath": '//div[@class="line-metrics"]/div[4]//div[@class="content"]//span', "kind": "integer"},
    {"period": "昨天", "field": "昨天ALL商品已出售", "xpath": '//div[@class="line-metrics"]/div[5]//div[@class="content"]//span', "kind": "integer"},
    {"period": "昨天", "field": "昨天ALL销售额", "xpath": '//div[@class="line-metrics"]/div[6]//div[@class="content"]//span', "kind": "currency", "currency_code": "BRL"},
    {"period": "昨天", "field": "昨天ALL优惠价金额", "xpath": '//div[@class="line-metrics"]/div[9]//div[@class="content"]//span', "kind": "currency", "currency_code": "BRL"},
    {"period": "昨天", "field": "昨天ALL优惠劵带来销售额", "xpath": '//div[@class="line-metrics"]/div[10]//div[@class="content"]//span', "kind": "currency", "currency_code": "BRL"},
    {"period": "昨天", "field": "昨天ALL加购次数", "xpath": '//div[@class="line-metrics"]/div[11]//div[@class="content"]//span', "kind": "integer"},
    {"period": "昨天", "field": "昨天ALL加购率", "xpath": '//div[@class="line-metrics"]/div[12]//div[@class="content"]//span', "kind": "percent"},
    {"period": "昨天", "field": "昨天ALL花费", "xpath": '//div[@class="line-metrics"]/div[7]//div[@class="content"]//span', "kind": "currency", "currency_code": "BRL"},
    {"period": "昨天", "field": "昨天ALL广告支出回报率", "xpath": '//div[@class="line-metrics"]/div[8]//div[@class="content"]//span', "kind": "decimal"},
    {"period": "7天", "field": "7天ALL展示次数", "xpath": '//div[@class="line-metrics"]/div[1]//div[@class="content"]//span', "kind": "integer"},
    {"period": "7天", "field": "7天ALL点击数", "xpath": '//div[@class="line-metrics"]/div[2]//div[@class="content"]//span', "kind": "integer"},
    {"period": "7天", "field": "7天ALL点击率", "xpath": '//div[@class="line-metrics"]/div[3]//div[@class="content"]//span', "kind": "percent"},
    {"period": "7天", "field": "7天ALL订单量", "xpath": '//div[@class="line-metrics"]/div[4]//div[@class="content"]//span', "kind": "integer"},
    {"period": "7天", "field": "7天ALL商品已出售", "xpath": '//div[@class="line-metrics"]/div[5]//div[@class="content"]//span', "kind": "integer"},
    {"period": "7天", "field": "7天ALL销售额", "xpath": '//div[@class="line-metrics"]/div[6]//div[@class="content"]//span', "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天ALL优惠价金额", "xpath": '//div[@class="line-metrics"]/div[9]//div[@class="content"]//span', "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天ALL优惠劵带来销售额", "xpath": '//div[@class="line-metrics"]/div[10]//div[@class="content"]//span', "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天ALL加购次数", "xpath": '//div[@class="line-metrics"]/div[11]//div[@class="content"]//span', "kind": "integer"},
    {"period": "7天", "field": "7天ALL加购率", "xpath": '//div[@class="line-metrics"]/div[12]//div[@class="content"]//span', "kind": "percent"},
    {"period": "7天", "field": "7天ALL花费", "xpath": '//div[@class="line-metrics"]/div[7]//div[@class="content"]//span', "kind": "currency", "currency_code": "BRL"},
    {"period": "7天", "field": "7天ALL广告支出回报率", "xpath": '//div[@class="line-metrics"]/div[8]//div[@class="content"]//span', "kind": "decimal"}
]


class ShopeeAuto:
    """Shopee 广告后台昨天和最近 7 天数据自动化。"""

    def __init__(self, config: dict[str, Any] | None = None):
        """保存 Shopee 独立配置；所有 XPath 集中维护在本文件顶部。"""
        self.config = config or {}

    def collect(
        self,
        store_name: str,
        download_path: str = "",
        debugging_port: int | str | None = None,
    ) -> list[dict[str, Any]]:
        """接管紫鸟当前标签页，采集昨天和最近 7 天共 24 个广告指标。"""
        if not debugging_port:
            raise RuntimeError("紫鸟没有返回 debuggingPort，无法接管 Shopee 店铺")

        LOGGER.info("[Shopee][开始] 店铺=%s，准备接管紫鸟浏览器，debugging_port=%s", store_name, debugging_port)

        # 只连接紫鸟已经打开的 Chromium，不创建浏览器，也不调用 tab.get()。
        browser = Chromium(f"127.0.0.1:{debugging_port}")
        tab = browser.latest_tab
        collected_at = datetime.now(timezone.utc).isoformat()

        self._wait_for_page_ready(tab, PAGE_READY_TIMEOUT_SECONDS)
        LOGGER.info("[Shopee][页面] 主文档等待结束，额外等待 %s 秒让菜单完成渲染", AFTER_PAGE_READY_WAIT_SECONDS)
        time.sleep(AFTER_PAGE_READY_WAIT_SECONDS)

        # 广告按钮可见时直接点击；不可见时先展开营销中心。
        first_period_xpath = self._first_step_xpath(PERIOD_CLICK_STEPS.get("昨天", []))
        self._enter_shopee_ads(tab, first_period_xpath)

        rows: list[dict[str, Any]] = []
        for period in ("昨天", "7天"):
            LOGGER.info("[Shopee][广告] 开始切换并采集时间范围=%s", period)
            # 选择日期后，时间选项消失才算点击成功；随后固定等待 30 秒让广告表刷新。
            self._run_click_steps(tab, PERIOD_CLICK_STEPS.get(period, []), final_next_xpath="")
            for spec in METRIC_SPECS:
                if spec["period"] != period:
                    continue
                field_name = spec["field"]
                xpath = spec["xpath"]
                value_kind = spec["kind"]
                currency_code = spec.get("currency_code", "")
                LOGGER.info(
                    "[Shopee][指标] 准备读取：时间范围=%s，字段=%s，配置类型=%s，币种=%s，xpath=%s",
                    period,
                    field_name,
                    value_kind,
                    currency_code or "无",
                    xpath or "<空 XPath>",
                )
                raw_text = self._read_xpath(tab, xpath, field_name)
                converted_value = self._format_value(raw_text, value_kind)
                LOGGER.info(
                    "[Shopee][指标结果] 字段=%s，原始值=%r，原始类型=%s，转换值=%r，转换后类型=%s，配置类型=%s，币种=%s",
                    field_name,
                    raw_text,
                    type(raw_text).__name__,
                    converted_value,
                    type(converted_value).__name__,
                    value_kind,
                    currency_code or "无",
                )
                if raw_text == "":
                    LOGGER.warning("[Shopee][指标失败] 字段=%s，XPath 未抓到有效文本，最终按空值处理", field_name)
                elif converted_value == "":
                    LOGGER.warning(
                        "[Shopee][转换失败] 字段=%s，原始值=%r 无法按类型=%s 转换，最终按空值处理",
                        field_name,
                        raw_text,
                        value_kind,
                    )

                # SP 多维表字段结构尚未给出，暂时使用通用的一项指标一条记录。
                row = {
                    "店铺名": store_name,
                    "平台": "shopee",
                    "采集时间": collected_at,
                    "指标": field_name,
                    "数值": converted_value,
                    "原始数据": json.dumps(
                        {
                            "时间范围": period,
                            "数据行": "ALL",
                            "字段": field_name,
                            "原始值": raw_text,
                            "XPath": xpath,
                        },
                        ensure_ascii=False,
                    ),
                }
                rows.append(row)

        valid_count = sum(row["数值"] != "" for row in rows)
        LOGGER.info("[Shopee][结果打包] rows=%s", json.dumps(rows, ensure_ascii=False, default=str))
        LOGGER.info("[Shopee][完成] 店铺=%s，有效指标=%s/%s", store_name, valid_count, len(rows))
        return rows

    def _enter_shopee_ads(self, tab: Any, next_xpath: str = "") -> bool:
        """判断 Shopee 广告按钮是否可见，必要时先展开营销中心。"""
        if not SHOPEE_AD_BUTTON_XPATH:
            LOGGER.error("[Shopee][配置缺失] SHOPEE_AD_BUTTON_XPATH 为空，无法进入 Shopee 广告页面")
            return False

        ad_element = self._find_visible_element(tab, SHOPEE_AD_BUTTON_XPATH, timeout=2)
        if ad_element:
            LOGGER.info("[Shopee][菜单判断] Shopee广告按钮当前可见，直接点击")
        else:
            LOGGER.info("[Shopee][菜单判断] Shopee广告按钮不可见，需要展开营销中心")
            if not MARKETING_CENTER_BUTTON_XPATH:
                LOGGER.error("[Shopee][配置缺失] MARKETING_CENTER_BUTTON_XPATH 为空，无法展开营销中心")
                return False
            expanded = self._click_with_retry(
                tab,
                MARKETING_CENTER_BUTTON_XPATH,
                "点击营销中心展开按钮",
                success_xpath=SHOPEE_AD_BUTTON_XPATH,
                success_state="visible",
                success_name="Shopee广告按钮出现",
            )
            if not expanded:
                LOGGER.error("[Shopee][菜单失败] 营销中心展开后仍未看到 Shopee广告按钮")
                return False

        # 已配置时间按钮时，用它的出现确认确实进入了广告页面；否则只判断 click() 是否成功。
        entered = self._click_with_retry(
            tab,
            SHOPEE_AD_BUTTON_XPATH,
            "点击Shopee广告按钮",
            success_xpath=next_xpath,
            success_state="visible" if next_xpath else "",
            success_name="Shopee广告页面的时间切换按钮出现",
        )
        if not entered:
            LOGGER.error("[Shopee][菜单失败] Shopee广告按钮连续 4 次点击失败")
            return False
        # XPath 尚未填写时仍固定等待 30 秒，给广告页面留下完整加载时间。
        if not next_xpath:
            self._wait_for_xpath(tab, "", NEXT_ELEMENT_TIMEOUT_SECONDS, "Shopee广告页面的时间切换按钮")
        return True

    def _run_click_steps(self, tab: Any, steps: list[dict[str, Any]], final_next_xpath: str = "") -> None:
        """顺序执行按钮步骤，并把点击后的状态验证纳入重试判定。"""
        for index, step in enumerate(steps):
            step_name = str(step.get("name") or f"第 {index + 1} 个未命名按钮")
            xpath = str(step.get("xpath") or "").strip()
            if not xpath:
                LOGGER.warning("[Shopee][按钮跳过] 步骤=%s，原因=XPath 为空", step_name)
                continue

            clicked = self._click_with_retry(
                tab,
                xpath,
                step_name,
                success_xpath=str(step.get("success_xpath") or "").strip(),
                success_state=str(step.get("success_state") or "").strip().lower(),
                success_name=str(step.get("success_name") or "点击后的页面状态"),
                scroll_to_center=bool(step.get("scroll_to_center", False)),
            )
            if not clicked:
                LOGGER.error("[Shopee][按钮失败] 步骤=%s，全部 4 次点击和状态验证均失败", step_name)
                continue

            wait_seconds = float(step.get("wait_seconds", 1) or 0)
            if wait_seconds > 0:
                LOGGER.info("[Shopee][按钮] 步骤=%s 点击成功，先等待 %.1f 秒", step_name, wait_seconds)
                time.sleep(wait_seconds)

            next_xpath = self._next_step_xpath(steps, index + 1) or final_next_xpath
            self._wait_for_xpath(tab, next_xpath, NEXT_ELEMENT_TIMEOUT_SECONDS, f"{step_name} 后的下一按钮或数据")

    def _click_with_retry(
        self,
        tab: Any,
        xpath: str,
        step_name: str,
        success_xpath: str = "",
        success_state: str = "",
        success_name: str = "点击后的页面状态",
        scroll_to_center: bool = False,
    ) -> bool:
        """按钮首次点击失败后重试 3 次，状态验证失败也视为本次点击失败。"""
        max_attempts = CLICK_RETRY_TIMES + 1
        for attempt in range(max_attempts):
            if scroll_to_center:
                self._scroll_element_to_center(tab, xpath, step_name)
            if success_xpath and success_state == "visible" and self._element_state_matches(tab, success_xpath, "visible"):
                LOGGER.info("[Shopee][按钮验证成功] 步骤=%s，%s，无需再次点击", step_name, success_name)
                return True
            if attempt > 0 and success_xpath and success_state == "hidden" and self._element_state_matches(tab, success_xpath, "hidden"):
                LOGGER.info("[Shopee][按钮验证成功] 步骤=%s，%s，上一次点击已延迟生效", step_name, success_name)
                return True

            if attempt > 0:
                interval = CLICK_RETRY_INTERVAL_SECONDS + random.uniform(0, 1)
                LOGGER.warning(
                    "[Shopee][按钮重试] 步骤=%s，第 %s/%s 次尝试前等待 %.2f 秒，xpath=%s",
                    step_name,
                    attempt + 1,
                    max_attempts,
                    interval,
                    xpath,
                )
                time.sleep(interval)

            try:
                LOGGER.info(
                    "[Shopee][按钮查找] 步骤=%s，第 %s/%s 次尝试，xpath=%s",
                    step_name,
                    attempt + 1,
                    max_attempts,
                    xpath,
                )
                element = self._find_visible_element(tab, xpath, timeout=5)
                if not element:
                    LOGGER.warning("[Shopee][按钮未找到] 步骤=%s，第 %s/%s 次未找到可见元素", step_name, attempt + 1, max_attempts)
                    continue
                element.click()
                if success_xpath and success_state:
                    LOGGER.info("[Shopee][按钮已点击] 步骤=%s，开始验证=%s", step_name, success_name)
                    if not self._wait_for_element_state(
                        tab,
                        success_xpath,
                        success_state,
                        NEXT_ELEMENT_TIMEOUT_SECONDS,
                        success_name,
                    ):
                        LOGGER.warning("[Shopee][按钮验证失败] 步骤=%s，第 %s/%s 次未满足=%s", step_name, attempt + 1, max_attempts, success_name)
                        continue
                else:
                    LOGGER.info("[Shopee][按钮已点击] 步骤=%s，无额外状态验证", step_name)
                LOGGER.info("[Shopee][按钮成功] 步骤=%s，第 %s/%s 次点击并验证成功", step_name, attempt + 1, max_attempts)
                return True
            except Exception as exc:
                LOGGER.warning(
                    "[Shopee][按钮异常] 步骤=%s，第 %s/%s 次失败，异常=%s，xpath=%s",
                    step_name,
                    attempt + 1,
                    max_attempts,
                    exc,
                    xpath,
                )

        LOGGER.error("[Shopee][按钮终止] 步骤=%s，全部 %s 次点击均失败，xpath=%s", step_name, max_attempts, xpath)
        return False

    def _scroll_element_to_center(self, tab: Any, xpath: str, target_name: str) -> bool:
        """把时间切换按钮滚动到屏幕中心，避免按钮在视口外导致点击失败。"""
        if not xpath:
            LOGGER.warning("[Shopee][滚动跳过] 目标=%s，XPath 为空", target_name)
            return False

        element = self._find_visible_element(tab, xpath, timeout=3)
        if not element:
            LOGGER.warning("[Shopee][滚动失败] 目标=%s，未找到可见元素，xpath=%s", target_name, xpath)
            return False

        # 优先使用页面 JavaScript 的 block:center，能够把元素放在视口垂直和水平中心。
        try:
            xpath_literal = json.dumps(xpath, ensure_ascii=False)
            result = tab.run_js(
                f"""
                const targetXPath = {xpath_literal};
                const target = document.evaluate(
                    targetXPath,
                    document,
                    null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE,
                    null
                ).singleNodeValue;
                if (!target) return false;
                target.scrollIntoView({{behavior: 'instant', block: 'center', inline: 'center'}});
                return true;
                """
            )
            if result is not False:
                LOGGER.info("[Shopee][滚动成功] 目标=%s，已滚动到屏幕中心，xpath=%s", target_name, xpath)
                return True
        except Exception as exc:
            LOGGER.warning("[Shopee][滚动JS异常] 目标=%s，准备使用 DrissionPage 滚动接口，异常=%s", target_name, exc)

        # 不同 DrissionPage 版本的滚动接口名称可能不同，因此提供兼容回退。
        try:
            scroll_api = getattr(element, "scroll", None)
            to_center = getattr(scroll_api, "to_center", None) if scroll_api is not None else None
            if callable(to_center):
                to_center()
                LOGGER.info("[Shopee][滚动成功] 目标=%s，已通过 DrissionPage to_center() 定位，xpath=%s", target_name, xpath)
                return True

            to_see = getattr(scroll_api, "to_see", None) if scroll_api is not None else None
            if callable(to_see):
                try:
                    to_see(center=True)
                except TypeError:
                    to_see()
                LOGGER.info("[Shopee][滚动成功] 目标=%s，已通过 DrissionPage to_see() 定位，xpath=%s", target_name, xpath)
                return True
        except Exception as exc:
            LOGGER.warning("[Shopee][滚动失败] 目标=%s，DrissionPage 滚动接口也失败：%s，xpath=%s", target_name, exc, xpath)

        return False

    def _wait_for_element_state(
        self,
        tab: Any,
        xpath: str,
        expected_state: str,
        timeout_seconds: float,
        target_name: str,
    ) -> bool:
        """等待日期选项出现或消失；消失需要连续确认两次。"""
        if expected_state not in {"visible", "hidden"}:
            LOGGER.error("[Shopee][状态配置错误] 目标=%s，不支持状态=%s", target_name, expected_state)
            return False
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        hidden_checks = 0
        LOGGER.info("[Shopee][状态等待] 目标=%s，期望=%s，最长 %.1f 秒，xpath=%s", target_name, expected_state, timeout_seconds, xpath)
        while time.monotonic() < deadline:
            visible = bool(self._find_visible_element(tab, xpath, timeout=1))
            if expected_state == "visible" and visible:
                LOGGER.info("[Shopee][状态满足] 目标=%s 已出现，耗时 %.2f 秒", target_name, time.monotonic() - started_at)
                return True
            if expected_state == "hidden":
                if visible:
                    hidden_checks = 0
                else:
                    hidden_checks += 1
                    if hidden_checks >= 2:
                        LOGGER.info("[Shopee][状态满足] 目标=%s 连续两次不可见，确认消失", target_name)
                        return True
            time.sleep(0.5)
        LOGGER.error("[Shopee][状态超时] 目标=%s，等待 %.1f 秒未达到=%s，xpath=%s", target_name, timeout_seconds, expected_state, xpath)
        return False

    def _wait_for_page_ready(self, tab: Any, timeout_seconds: float) -> bool:
        """等待 Shopee 主文档加载完成。"""
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        last_state = ""
        LOGGER.info("[Shopee][页面等待] 等待 document.readyState=complete，最长 %.1f 秒", timeout_seconds)
        while time.monotonic() < deadline:
            try:
                state = str(tab.run_js("return document.readyState;") or "").lower()
                if state != last_state:
                    LOGGER.info("[Shopee][页面状态] document.readyState=%s", state)
                    last_state = state
                if state == "complete":
                    LOGGER.info("[Shopee][页面成功] 页面加载完成，耗时 %.2f 秒", time.monotonic() - started_at)
                    return True
            except Exception as exc:
                LOGGER.warning("[Shopee][页面异常] 读取 document.readyState 失败：%s", exc)
            time.sleep(1)
        LOGGER.error("[Shopee][页面超时] 等待 %.1f 秒仍未加载完成，继续执行", timeout_seconds)
        return False

    def _wait_for_xpath(self, tab: Any, xpath: str, timeout_seconds: float, target_name: str) -> bool:
        """等待下一按钮或数据；没有目标 XPath 时固定等待 30 秒。"""
        if not xpath:
            LOGGER.warning("[Shopee][元素等待] 目标=%s 未配置 XPath，固定等待 %.1f 秒", target_name, timeout_seconds)
            time.sleep(timeout_seconds)
            return True
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        LOGGER.info("[Shopee][元素等待] 目标=%s，最长 %.1f 秒，xpath=%s", target_name, timeout_seconds, xpath)
        while time.monotonic() < deadline:
            if self._find_visible_element(tab, xpath, timeout=1):
                LOGGER.info("[Shopee][元素出现] 目标=%s，耗时 %.2f 秒", target_name, time.monotonic() - started_at)
                return True
            time.sleep(1)
        LOGGER.error("[Shopee][元素超时] 目标=%s，等待 %.1f 秒仍未出现，xpath=%s", target_name, timeout_seconds, xpath)
        return False

    def _read_xpath(self, tab: Any, xpath: str, field_name: str) -> str:
        """读取指标两次；首次失败后等待 2 秒再读，最终失败返回空字符串。"""
        if not xpath:
            LOGGER.warning("[Shopee][指标跳过] 字段=%s，原因=XPath 为空", field_name)
            return ""
        for attempt in range(2):
            if attempt > 0:
                LOGGER.warning("[Shopee][指标重试] 字段=%s，第 2/2 次读取前等待 %s 秒，xpath=%s", field_name, CLICK_RETRY_INTERVAL_SECONDS, xpath)
                time.sleep(CLICK_RETRY_INTERVAL_SECONDS)
            try:
                LOGGER.info("[Shopee][指标查找] 字段=%s，第 %s/2 次读取，xpath=%s", field_name, attempt + 1, xpath)
                element = tab.ele(f"xpath:{xpath}", timeout=3)
                if element:
                    raw_text = str(element.text or "").strip()
                    if raw_text:
                        LOGGER.info("[Shopee][指标抓取成功] 字段=%s，原始文本=%r", field_name, raw_text)
                        return raw_text
                    LOGGER.warning("[Shopee][指标文本为空] 字段=%s，已找到元素但 text 为空", field_name)
                else:
                    LOGGER.warning("[Shopee][指标未找到] 字段=%s，第 %s/2 次未找到元素", field_name, attempt + 1)
            except Exception as exc:
                LOGGER.warning("[Shopee][指标异常] 字段=%s，第 %s/2 次失败，异常=%s，xpath=%s", field_name, attempt + 1, exc, xpath)
        LOGGER.error("[Shopee][指标最终失败] 字段=%s，两次读取均未得到文本，返回空字符串", field_name)
        return ""

    @staticmethod
    def _find_visible_element(tab: Any, xpath: str, timeout: float = 1) -> Any:
        """查找 XPath 元素，并兼容 DrissionPage 不同版本的可见性属性。"""
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
                return element if bool(legacy_displayed()) else None
            if legacy_displayed is not None:
                return element if bool(legacy_displayed) else None
            return element
        except Exception:
            return None

    def _element_state_matches(self, tab: Any, xpath: str, expected_state: str) -> bool:
        """立即判断元素状态，用于重试前确认上次点击是否延迟生效。"""
        visible = bool(self._find_visible_element(tab, xpath, timeout=0.5))
        if expected_state == "visible":
            return visible
        if expected_state == "hidden":
            return not visible
        return False

    @staticmethod
    def _next_step_xpath(steps: list[dict[str, Any]], start_index: int) -> str:
        """从后续步骤中返回第一个非空 XPath。"""
        for step in steps[start_index:]:
            xpath = str(step.get("xpath") or "").strip()
            if xpath:
                return xpath
        return ""

    @staticmethod
    def _first_step_xpath(steps: list[dict[str, Any]]) -> str:
        """返回按钮步骤中的第一个非空 XPath。"""
        return ShopeeAuto._next_step_xpath(steps, 0)

    @staticmethod
    def _format_value(raw_text: str, kind: str) -> Any:
        """转换 Shopee 数值：BRL 两位小数、整数、百分比文本和普通小数。"""
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

        number = ShopeeAuto._parse_brazilian_number(raw_text)
        if number is None:
            return ""
        if kind == "currency":
            return round(number, 2)
        if kind == "decimal":
            return round(number, 2)
        if kind == "percent":
            return f"{number:.1f}%"
        return raw_text

    @staticmethod
    def _parse_brazilian_number(raw_text: str) -> float | None:
        """解析巴西格式，例如 R$18.558,26 -> 18558.26，7,61 -> 7.61。"""
        text = str(raw_text).strip().replace("%", "")
        text = re.sub(r"[^0-9,.-]", "", text)
        if not text or text in {"-", ".", ","}:
            return None
        try:
            if "." in text and "," in text:
                text = text.replace(".", "").replace(",", ".")
            elif "," in text:
                text = text.replace(",", ".")
            return float(text)
        except (TypeError, ValueError):
            return None


def collect_shopee_ad(
    store_name: str,
    download_path: str = "",
    debugging_port: int | str | None = None,
) -> list[dict[str, Any]]:
    """提供一个可直接调用的 Shopee 函数入口。"""
    return ShopeeAuto().collect(store_name, download_path, debugging_port)

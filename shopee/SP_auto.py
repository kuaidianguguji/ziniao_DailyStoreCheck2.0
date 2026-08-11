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

# 同时包含密码输入框和账号输入框的 form 是 Shopee 未登录页面的明确标志。
# 一旦检测到该表单，当前店铺不再执行登录、菜单点击或数据采集，而是抛出异常，
# 由外层 ZiniaoStoreSession 使用紫鸟官方 stopBrowser 关闭店铺后继续下一店铺。
LOGIN_FORM_XPATH = '//form[.//input[@name="password"] and .//input[@name="loginKey"]]'

# 刚接管紫鸟标签页时等待登录表单出现的最长时间，单位为秒。
# 首次检查结束后，页面完成加载时还会再次检查，避免异步渲染较慢导致漏判。
LOGIN_FORM_DETECTION_TIMEOUT_SECONDS = 5

# Shopee 未登录页面可能因语言或版本不同而使用不同的登录按钮。
# 程序严格按列表顺序检查：第一个 XPath 没找到可见元素时，才检查第二个 XPath。
# 以后遇到新的登录页面，可以继续在列表末尾追加 XPath，不需要修改登录处理函数。
LOGIN_BUTTON_XPATHS: list[str] = [
    "//form//button[contains(@class,'ZzzLTG')]",
    '//button[normalize-space()="Log In"]',
]

# Shopee 广告弹窗关闭按钮按顺序检查：先使用现有奖励弹窗定位，找不到时再检查广告升级通知弹窗。
# 后续如果出现更多类型，只需在列表末尾追加 XPath，不需要修改关闭函数。
AD_POPUP_CLOSE_XPATHS: list[str] = [
    (
        '//div[contains(@class,"eds-modal__box") and contains(@class,"rewards-homepage-prompt")]'
        '//i[contains(@class,"eds-modal__close")]'
    ),
    (
        '//div[contains(@class,"shop-ads-upgrade-pre-notice-modal")]'
        '/ancestor::div[contains(@class,"eds-modal__box")]'
        '//i[contains(@class,"eds-modal__close")]'
    ),
]

# 关闭一层广告弹窗后继续观察的时间，防止第二层弹窗稍晚渲染而被误判为全部关闭。
AD_POPUP_CHAIN_WAIT_SECONDS = 2

# 如果“Shopee广告”不可见，需要先点击“营销中心”展开菜单。
MARKETING_CENTER_BUTTON_XPATH = '(//ul[@class="sidebar-menu"]/li)[3]//span[@class="sidebar-menu-item-text"]'

# “Shopee广告”菜单按钮。
SHOPEE_AD_BUTTON_XPATH = '//a[contains(@href,"/portal/marketing/pas/index")]'

# 广告页面的时间切换按钮。
TIME_SWITCH_BUTTON_XPATH = '//div[@class="eds-popover__ref"]//div[@class="eds-date-picker__input"]/div'

# 时间面板中的“昨天”和“最近7天”选项。
YESTERDAY_OPTION_XPATH = '//ul[@class="eds-date-shortcut-list"]/li[2]//span'
LAST_7_DAYS_OPTION_XPATH = '//ul[@class="eds-date-shortcut-list"]/li[3]//span'


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
# integer=整数；percent=页面百分数去掉百分号后的两位小数；
# currency=巴西雷亚尔两位小数；decimal=普通两位小数。
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

        # 登录表单的优先级最高。检测到后通过异常退出 collect，外层紫鸟会话负责关闭店铺。
        self._raise_if_login_required(
            tab,
            store_name,
            check_position="刚接管紫鸟标签页",
            timeout_seconds=LOGIN_FORM_DETECTION_TIMEOUT_SECONDS,
        )

        self._wait_for_page_ready(tab, PAGE_READY_TIMEOUT_SECONDS)
        LOGGER.info("[Shopee][页面] 主文档等待结束，额外等待 %s 秒让菜单完成渲染", AFTER_PAGE_READY_WAIT_SECONDS)
        time.sleep(AFTER_PAGE_READY_WAIT_SECONDS)

        # 页面异步内容可能在首次检查之后才渲染，因此在任何弹窗或菜单操作前复查一次。
        self._raise_if_login_required(
            tab,
            store_name,
            check_position="页面加载完成后",
            timeout_seconds=1,
        )
        self._close_ad_popup(tab, "页面加载完成后")

        # 页面可能处于未登录状态；按配置顺序检查多个登录按钮 XPath。
        self._login_if_needed(tab)
        # 登录跳转完成后弹窗可能才开始渲染，因此再次检查一次。
        self._close_ad_popup(tab, "登录检查完成后")

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
                display_value = self._format_display_value(converted_value, value_kind)
                LOGGER.info(
                    "[Shopee][指标结果] 字段=%s，原始值=%r，原始类型=%s，飞书数值=%r，数值类型=%s，显示值=%r，配置类型=%s，币种=%s",
                    field_name,
                    raw_text,
                    type(raw_text).__name__,
                    converted_value,
                    type(converted_value).__name__,
                    display_value,
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

                # 爬虫按“一项指标一行”返回，编排器随后合并为一条 26 字段飞书记录。
                row = {
                    "店铺名": store_name,
                    "平台": "shopee",
                    "采集时间": collected_at,
                    "指标": field_name,
                    # 数值用于多维表和历史电子表，必须保持 int/float，不能带 R$ 或 %。
                    "数值": converted_value,
                    # 显示值用于机器人和 ALL_info，保留人能直接识别的货币/百分比单位。
                    "显示值": display_value,
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

    @staticmethod
    def _raise_if_login_required(
        tab: Any,
        store_name: str,
        check_position: str,
        timeout_seconds: float,
    ) -> None:
        """检查未登录 form；存在时立即终止当前店铺，让外层紫鸟会话执行关闭。"""
        LOGGER.info(
            "[Shopee][登录表单检查] 店铺=%s，检查位置=%s，最长等待=%.1f秒，xpath=%s",
            store_name,
            check_position,
            timeout_seconds,
            LOGIN_FORM_XPATH,
        )
        try:
            # 用户要求按元素是否存在判断，因此这里不额外要求元素可见或具备点击尺寸。
            login_form = tab.ele(f"xpath:{LOGIN_FORM_XPATH}", timeout=timeout_seconds)
        except Exception as exc:
            LOGGER.warning(
                "[Shopee][登录表单检查异常] 店铺=%s，检查位置=%s，异常=%s；继续按已登录流程处理",
                store_name,
                check_position,
                exc,
            )
            return

        if not login_form:
            LOGGER.info("[Shopee][登录表单未发现] 店铺=%s，检查位置=%s，继续后续操作", store_name, check_position)
            return

        error_message = (
            f"Shopee 店铺 {store_name} 检测到登录表单，当前账号需要登录；"
            "已停止本店铺采集，正在通过紫鸟关闭店铺，关闭成功后继续下一店铺。"
        )
        LOGGER.error(
            "[Shopee][需要登录-停止当前店铺] 店铺=%s，检查位置=%s，xpath=%s",
            store_name,
            check_position,
            LOGIN_FORM_XPATH,
        )
        raise RuntimeError(error_message)

    def _close_ad_popup(self, tab: Any, check_position: str = "当前步骤") -> bool:
        """发现奖励广告弹窗时关闭；首次失败后最多重试 3 次，并验证关闭按钮已经消失。"""
        configured_xpaths = [str(xpath or "").strip() for xpath in AD_POPUP_CLOSE_XPATHS if str(xpath or "").strip()]
        if not configured_xpaths:
            LOGGER.warning("[Shopee][广告弹窗跳过] 检查位置=%s，关闭按钮 XPath 列表为空", check_position)
            return False

        max_attempts = CLICK_RETRY_TIMES + 1
        detected = False
        for attempt in range(max_attempts):
            active_xpath, close_element = self._find_ad_popup_close_element(
                tab,
                configured_xpaths,
                timeout=0.5,
                require_action_element=True,
            )
            if not close_element:
                if detected:
                    LOGGER.info(
                        "[Shopee][广告弹窗关闭成功] 检查位置=%s，关闭按钮已经消失",
                        check_position,
                    )
                    return True
                return False

            if not detected:
                LOGGER.warning(
                    "[Shopee][发现广告弹窗] 检查位置=%s，准备关闭，xpath=%s",
                    check_position,
                    active_xpath,
                )
                detected = True

            if attempt > 0:
                interval = CLICK_RETRY_INTERVAL_SECONDS + random.uniform(0, 1)
                LOGGER.warning(
                    "[Shopee][广告弹窗重试] 检查位置=%s，第 %s/%s 次点击前等待 %.2f 秒",
                    check_position,
                    attempt + 1,
                    max_attempts,
                    interval,
                )
                time.sleep(interval)

            try:
                LOGGER.info(
                    "[Shopee][广告弹窗点击] 检查位置=%s，第 %s/%s 次尝试，xpath=%s",
                    check_position,
                    attempt + 1,
                    max_attempts,
                    active_xpath,
                )
                self._click_element_with_fallback(
                    tab,
                    close_element,
                    active_xpath,
                    "关闭Shopee广告弹窗",
                )
                time.sleep(0.5)
                visible_xpath, visible_close_element = self._find_ad_popup_close_element(
                    tab,
                    configured_xpaths,
                    timeout=0.5,
                    require_action_element=False,
                )
                if not visible_close_element:
                    LOGGER.info(
                        "[Shopee][广告弹窗单层已关闭] 检查位置=%s，第 %s/%s 次点击后关闭按钮消失，继续观察 %.1f 秒",
                        check_position,
                        attempt + 1,
                        max_attempts,
                        AD_POPUP_CHAIN_WAIT_SECONDS,
                    )
                    followup_deadline = time.monotonic() + AD_POPUP_CHAIN_WAIT_SECONDS
                    followup_found = False
                    followup_xpath = ""
                    while time.monotonic() < followup_deadline:
                        followup_xpath, followup_element = self._find_ad_popup_close_element(
                            tab,
                            configured_xpaths,
                            timeout=0.25,
                            require_action_element=False,
                        )
                        if followup_element:
                            followup_found = True
                            break
                        time.sleep(0.25)
                    if followup_found:
                        LOGGER.warning(
                            "[Shopee][发现连续广告弹窗] 检查位置=%s，第一层关闭后又发现关闭按钮，继续关闭下一层，xpath=%s",
                            check_position,
                            followup_xpath,
                        )
                        continue
                    LOGGER.info(
                        "[Shopee][广告弹窗关闭成功] 检查位置=%s，第 %s/%s 次点击后连续 %.1f 秒未出现下一层弹窗",
                        check_position,
                        attempt + 1,
                        max_attempts,
                        AD_POPUP_CHAIN_WAIT_SECONDS,
                    )
                    return True
                LOGGER.warning(
                    "[Shopee][广告弹窗验证失败] 检查位置=%s，第 %s/%s 次点击后仍发现弹窗关闭按钮，xpath=%s",
                    check_position,
                    attempt + 1,
                    max_attempts,
                    visible_xpath,
                )
            except Exception as exc:
                LOGGER.warning(
                    "[Shopee][广告弹窗关闭异常] 检查位置=%s，第 %s/%s 次失败，异常=%s",
                    check_position,
                    attempt + 1,
                    max_attempts,
                    exc,
                )

        LOGGER.error(
            "[Shopee][广告弹窗关闭失败] 检查位置=%s，连续 %s 次点击后弹窗仍未确认关闭，xpaths=%s",
            check_position,
            max_attempts,
            configured_xpaths,
        )
        return False

    def _find_ad_popup_close_element(
        self,
        tab: Any,
        xpaths: list[str],
        timeout: float,
        require_action_element: bool,
    ) -> tuple[str, Any]:
        """按配置顺序查找弹窗关闭按钮，并返回实际命中的 XPath 和元素。"""
        for xpath_index, xpath in enumerate(xpaths, start=1):
            if require_action_element:
                element = self._find_action_element(
                    tab,
                    xpath,
                    timeout=timeout,
                    target_name=f"第{xpath_index}个广告弹窗关闭按钮",
                )
            else:
                element = self._find_visible_element(tab, xpath, timeout=timeout)
            if element:
                return xpath, element
        return "", None

    def _login_if_needed(self, tab: Any) -> bool:
        """依次检查多个登录按钮；发现后点击并等待登录后的菜单出现。"""
        configured_xpaths = [str(xpath or "").strip() for xpath in LOGIN_BUTTON_XPATHS if str(xpath or "").strip()]
        if not configured_xpaths:
            LOGGER.warning("[Shopee][登录检查跳过] LOGIN_BUTTON_XPATHS 没有有效 XPath")
            return False

        for index, xpath in enumerate(configured_xpaths, start=1):
            LOGGER.info(
                "[Shopee][登录检查] 正在检查第 %s/%s 个登录按钮，xpath=%s",
                index,
                len(configured_xpaths),
                xpath,
            )
            login_element = self._find_visible_element(tab, xpath, timeout=3)
            if not login_element:
                LOGGER.info("[Shopee][登录按钮未发现] 第 %s 个 XPath 没有可见按钮，继续检查下一个", index)
                continue

            LOGGER.warning("[Shopee][未登录] 发现第 %s 个登录按钮，准备点击，xpath=%s", index, xpath)
            clicked = self._click_with_retry(
                tab,
                xpath,
                f"点击第{index}个Shopee登录按钮",
                success_xpath=xpath,
                success_state="hidden",
                success_name="登录按钮消失",
            )
            if not clicked:
                LOGGER.error("[Shopee][登录失败] 第 %s 个登录按钮经过首次及 3 次重试仍未成功，继续检查其他 XPath", index)
                continue

            # 登录按钮消失后，等待登录页面跳转并出现左侧菜单中的任意一个已登录标志。
            self._wait_for_any_xpath(
                tab,
                [SHOPEE_AD_BUTTON_XPATH, MARKETING_CENTER_BUTTON_XPATH],
                NEXT_ELEMENT_TIMEOUT_SECONDS,
                "登录后的Shopee广告或营销中心菜单",
            )
            LOGGER.info("[Shopee][登录处理完成] 已点击第 %s 个登录按钮，继续进入广告页面", index)
            return True

        LOGGER.info("[Shopee][登录检查完成] 所有登录按钮 XPath 均不可见，按当前已经登录继续")
        return False

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
            # 弹窗可能在任意按钮操作前延迟出现，先关闭再查找目标按钮。
            self._close_ad_popup(tab, f"{step_name}点击前")
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
                element = self._find_action_element(tab, xpath, timeout=5, target_name=step_name)
                if not element:
                    LOGGER.warning("[Shopee][按钮未找到] 步骤=%s，第 %s/%s 次未找到可见元素", step_name, attempt + 1, max_attempts)
                    continue
                self._click_element_with_fallback(tab, element, xpath, step_name)
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

        element = self._find_action_element(tab, xpath, timeout=3, target_name=target_name)
        if not element:
            LOGGER.warning("[Shopee][滚动失败] 目标=%s，未找到可见元素，xpath=%s", target_name, xpath)
            return False

        # 优先使用 DrissionPage 自带滚动接口，真实驱动浏览器滚动到元素中心。
        api_scrolled = False
        try:
            scroll_api = getattr(element, "scroll", None)
            to_center = getattr(scroll_api, "to_center", None) if scroll_api is not None else None
            if callable(to_center):
                to_center()
                api_scrolled = True
                LOGGER.info("[Shopee][滚动API成功] 目标=%s，已通过 to_center() 定位，xpath=%s", target_name, xpath)
            else:
                to_see = getattr(scroll_api, "to_see", None) if scroll_api is not None else None
                if callable(to_see):
                    try:
                        to_see(center=True)
                    except TypeError:
                        to_see()
                    api_scrolled = True
                    LOGGER.info("[Shopee][滚动API成功] 目标=%s，已通过 to_see() 定位，xpath=%s", target_name, xpath)
        except Exception as exc:
            LOGGER.warning("[Shopee][滚动API异常] 目标=%s，准备使用 JavaScript 滚动，异常=%s", target_name, exc)

        # 再使用 JavaScript 的 block:center，兼容没有 DrissionPage 滚动接口的版本。
        try:
            xpath_literal = json.dumps(xpath, ensure_ascii=False)
            result = tab.run_js(
                f"""
                const targetXPath = {xpath_literal};
                let target = document.evaluate(
                    targetXPath,
                    document,
                    null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE,
                    null
                ).singleNodeValue;
                if (!target) return false;
                const originalRect = target.getBoundingClientRect();
                // XPath 可能命中没有尺寸的内部 div/span，改用最近的父节点完成滚动。
                if (originalRect.width <= 0 || originalRect.height <= 0) {{
                    target = target.parentElement || target;
                }}
                target.scrollIntoView({{behavior: 'instant', block: 'center', inline: 'center'}});
                return true;
                """
            )
            if result is not False:
                LOGGER.info("[Shopee][滚动成功] 目标=%s，已滚动到屏幕中心，xpath=%s", target_name, xpath)
                return True
        except Exception as exc:
            LOGGER.warning("[Shopee][滚动JS异常] 目标=%s，JavaScript 滚动失败，异常=%s", target_name, exc)

        return api_scrolled

    def _click_element_with_fallback(self, tab: Any, element: Any, xpath: str, step_name: str) -> None:
        """点击元素；无尺寸时依次尝试可点击父节点和 JavaScript click。"""
        try:
            element.click()
            return
        except Exception as first_error:
            LOGGER.warning(
                "[Shopee][按钮原始点击失败] 步骤=%s，xpath=%s，异常=%s，准备尝试父节点/JS回退",
                step_name,
                xpath,
                first_error,
            )

        # 日期菜单通常把文字放在 span 中，真正有尺寸和点击事件的是外层 li。
        for tag_name in ("li", "button", "a", "div"):
            ancestor_xpath = f"({xpath})/ancestor::{tag_name}[1]"
            ancestor = self._find_action_element(tab, ancestor_xpath, timeout=1, target_name=f"{step_name}的{tag_name}父节点")
            if not ancestor:
                continue
            try:
                ancestor.click()
                LOGGER.info("[Shopee][按钮回退成功] 步骤=%s，已点击 %s 父节点，xpath=%s", step_name, tag_name, ancestor_xpath)
                return
            except Exception as ancestor_error:
                LOGGER.warning(
                    "[Shopee][按钮父节点回退失败] 步骤=%s，父节点=%s，异常=%s",
                    step_name,
                    ancestor_xpath,
                    ancestor_error,
                )

        # 最后一层使用 DOM click，不依赖元素的屏幕坐标，适合无尺寸但有事件绑定的 span。
        try:
            xpath_literal = json.dumps(xpath, ensure_ascii=False)
            result = tab.run_js(
                f"""
                const target = document.evaluate(
                    {xpath_literal},
                    document,
                    null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE,
                    null
                ).singleNodeValue;
                if (!target) return false;
                target.click();
                return true;
                """
            )
            if result is not False:
                LOGGER.info("[Shopee][按钮JS回退成功] 步骤=%s，xpath=%s", step_name, xpath)
                return
        except Exception as js_error:
            LOGGER.warning("[Shopee][按钮JS回退失败] 步骤=%s，异常=%s，xpath=%s", step_name, js_error, xpath)

        raise RuntimeError(f"原始点击和父节点/JS回退均失败：{first_error}")

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
            self._close_ad_popup(tab, f"等待{target_name}状态时")
            visible = bool(self._find_action_element(tab, xpath, timeout=1, target_name=target_name))
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
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                self._close_ad_popup(tab, f"固定等待{target_name}时")
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
            return True
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        LOGGER.info("[Shopee][元素等待] 目标=%s，最长 %.1f 秒，xpath=%s", target_name, timeout_seconds, xpath)
        while time.monotonic() < deadline:
            self._close_ad_popup(tab, f"等待{target_name}出现时")
            if self._find_action_element(tab, xpath, timeout=1, target_name=target_name):
                LOGGER.info("[Shopee][元素出现] 目标=%s，耗时 %.2f 秒", target_name, time.monotonic() - started_at)
                return True
            time.sleep(1)
        LOGGER.error("[Shopee][元素超时] 目标=%s，等待 %.1f 秒仍未出现，xpath=%s", target_name, timeout_seconds, xpath)
        return False

    def _wait_for_any_xpath(self, tab: Any, xpaths: list[str], timeout_seconds: float, target_name: str) -> bool:
        """等待多个 XPath 中任意一个出现，适用于登录后菜单可能有不同展开状态的页面。"""
        candidates = [str(xpath or "").strip() for xpath in xpaths if str(xpath or "").strip()]
        if not candidates:
            LOGGER.warning("[Shopee][多元素等待] 目标=%s 没有有效 XPath，固定等待 %.1f 秒", target_name, timeout_seconds)
            time.sleep(timeout_seconds)
            return True

        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        LOGGER.info("[Shopee][多元素等待] 目标=%s，候选数=%s，最长 %.1f 秒", target_name, len(candidates), timeout_seconds)
        while time.monotonic() < deadline:
            self._close_ad_popup(tab, f"等待{target_name}出现时")
            for index, xpath in enumerate(candidates, start=1):
                if self._find_action_element(tab, xpath, timeout=1, target_name=target_name):
                    LOGGER.info(
                        "[Shopee][多元素出现] 目标=%s，第 %s 个候选已出现，耗时 %.2f 秒，xpath=%s",
                        target_name,
                        index,
                        time.monotonic() - started_at,
                        xpath,
                    )
                    return True
            time.sleep(1)
        LOGGER.error("[Shopee][多元素超时] 目标=%s，等待 %.1f 秒仍没有候选元素出现", target_name, timeout_seconds)
        return False

    def _read_xpath(self, tab: Any, xpath: str, field_name: str) -> str:
        """读取指标两次；首次失败后等待 2 秒再读，最终失败返回空字符串。"""
        if not xpath:
            LOGGER.warning("[Shopee][指标跳过] 字段=%s，原因=XPath 为空", field_name)
            return ""
        for attempt in range(2):
            self._close_ad_popup(tab, f"读取指标{field_name}前")
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

    def _find_action_element(self, tab: Any, xpath: str, timeout: float = 1, target_name: str = "") -> Any:
        """查找真正有位置和尺寸的元素；无尺寸时回退到最近可点击父节点。"""
        element = self._find_visible_element(tab, xpath, timeout=timeout)
        if not element:
            return None
        if self._element_has_geometry(element) and self._element_is_really_visible(tab, xpath, element):
            return element

        # 原 XPath 可能命中仅用于包裹文字的 span/div，优先选择菜单常见的可点击父节点。
        for tag_name in ("li", "button", "a", "div"):
            ancestor_xpath = f"({xpath})/ancestor::{tag_name}[1]"
            ancestor = self._find_visible_element(tab, ancestor_xpath, timeout=timeout)
            if ancestor and self._element_has_geometry(ancestor) and self._element_is_really_visible(tab, ancestor_xpath, ancestor):
                LOGGER.info(
                    "[Shopee][元素父节点回退] 目标=%s，原 XPath 无尺寸，改用 %s 父节点：%s",
                    target_name or xpath,
                    tag_name,
                    ancestor_xpath,
                )
                return ancestor
        return None

    @staticmethod
    def _element_has_geometry(element: Any) -> bool:
        """判断 DrissionPage 元素是否有可用于鼠标操作的位置和尺寸。"""
        try:
            rect = getattr(element, "rect", None)
            size = getattr(rect, "size", None) if rect is not None else None
            if size is None:
                # 某些 DrissionPage 版本没有暴露 rect.size，交给 click() 自己判断。
                return True
            width, height = float(size[0]), float(size[1])
            return width > 0 and height > 0
        except Exception:
            return False

    @staticmethod
    def _element_is_really_visible(tab: Any, xpath: str, element: Any) -> bool:
        """检查元素没有被 CSS 隐藏，并且至少有一部分位于当前浏览器视口内。"""
        try:
            xpath_literal = json.dumps(xpath, ensure_ascii=False)
            result = tab.run_js(
                f"""
                const target = document.evaluate(
                    {xpath_literal},
                    document,
                    null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE,
                    null
                ).singleNodeValue;
                if (!target) return {{visible: false}};
                const rect = target.getBoundingClientRect();
                let current = target;
                while (current && current.nodeType === 1) {{
                    const style = window.getComputedStyle(current);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {{
                        return {{visible: false, reason: 'css-hidden'}};
                    }}
                    current = current.parentElement;
                }}
                const inViewport = rect.width > 0 && rect.height > 0 &&
                    rect.bottom > 0 && rect.right > 0 &&
                    rect.top < window.innerHeight && rect.left < window.innerWidth;
                return {{visible: inViewport, width: rect.width, height: rect.height}};
                """
            )
            if isinstance(result, dict) and "visible" in result:
                return bool(result["visible"])
        except Exception:
            # run_js 不同版本返回值不同，回退到 DrissionPage 的尺寸判断。
            pass
        return ShopeeAuto._element_has_geometry(element)

    def _element_state_matches(self, tab: Any, xpath: str, expected_state: str) -> bool:
        """立即判断元素状态，用于重试前确认上次点击是否延迟生效。"""
        visible = bool(self._find_action_element(tab, xpath, timeout=0.5, target_name="点击后的页面状态"))
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
        """转换 Shopee 数值：BRL、普通小数和百分数均保留两位，数量转为整数。"""
        if not raw_text:
            return ""
        if kind == "integer":
            return ShopeeAuto._parse_integer(raw_text)

        number = ShopeeAuto._parse_brazilian_number(raw_text)
        if number is None:
            return ""
        if kind == "currency":
            return round(number, 2)
        if kind == "decimal":
            return round(number, 2)
        if kind == "percent":
            # Shopee 飞书表中的点击率和加购率是“小数”字段，不是“进度/百分比”字段。
            # 页面 7,61% 因此写入数值 7.61，不能发送字符串 "7.61%"。
            return round(number, 2)
        return raw_text

    @staticmethod
    def _format_display_value(value: Any, kind: str) -> str:
        """为机器人消息格式化单位；飞书写入仍使用独立的纯数字 value。"""
        if value in ("", None):
            return ""
        try:
            if kind == "currency":
                # 巴西原始格式 R$17.490,26 规范显示为 R$17490.26。
                return f"R${float(value):.2f}"
            if kind == "percent":
                # 巴西原始格式 3,79% 规范显示为 3.79%。
                return f"{float(value):.2f}%"
            if kind == "decimal":
                return f"{float(value):.2f}"
            if kind == "integer":
                return str(int(value))
        except (TypeError, ValueError):
            return ""
        return str(value)

    @staticmethod
    def _parse_integer(raw_text: str) -> int | str:
        """解析整数和 Shopee 缩写数量，例如 10.7k -> 10700、1,2 mil -> 1200。"""
        text = str(raw_text or "").strip().lower()
        if not text or text == "-":
            return ""

        # k/m 是英文缩写，mil/mi 是巴西葡萄牙语页面可能使用的千/百万缩写。
        multiplier = 1
        suffix_match = re.search(r"\s*(k|mil|m|mi)\s*$", text, flags=re.IGNORECASE)
        if suffix_match:
            suffix = suffix_match.group(1).lower()
            multiplier = 1_000 if suffix in {"k", "mil"} else 1_000_000
            number_text = text[:suffix_match.start()].strip()
            number = ShopeeAuto._parse_brazilian_number(number_text)
            if number is None:
                return ""
            return int(round(number * multiplier))

        # 没有单位缩写时，点号和逗号按数量字段的千位分隔符处理。
        integer_text = re.sub(r"[^0-9-]", "", text)
        if not integer_text or integer_text == "-":
            return ""
        try:
            return int(integer_text)
        except ValueError:
            return ""

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

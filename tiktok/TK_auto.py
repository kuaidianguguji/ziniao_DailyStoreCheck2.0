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


# TikTok 登录入口和登录后的状态标记。
# 不等待页面完全就绪；登录入口出现时先处理登录，登录阶段不关闭其他弹窗或验证码。
LOGIN_EMAIL_PANEL_BUTTON_XPATH = '//span[@id="TikTok_Ads_SSO_Login_Email_Panel_Button"]'
LOGIN_EMAIL_SELECTED_XPATH = (
    '//span[@id="TikTok_Ads_SSO_Login_Email_Panel_Button" '
    'and contains(@class,"panel-item") and contains(@class,"selected")]'
)
LOGIN_BUTTON_XPATH = '//button[@id="TikTok_Ads_SSO_Login_Btn"]'
# 只有出现“手机号格式错误”提示时才切换到邮箱登录；没有该提示时直接点击登录。
LOGIN_PHONE_FORMAT_ERROR_XPATH = '//span[contains(@class,"error-msg") and normalize-space(.)="请检查输入的手机号格式"]'

# 首页弹窗和滑块验证码的关闭按钮。
# 登录流程结束后，验证码可能在任意阶段出现，因此点击、等待和读取指标前都会检查一次。
HOME_DIALOG_CLOSE_XPATH = '//div[@role="dialog"]//span[contains(@class,"core-modal-close-icon")]'
VERIFY_BAR_CLOSE_XPATH = '//a[@id="verify-bar-close"]'

# 页面入口和按钮操作参数。
# 不再等待 document.readyState=complete；这里只限制登录后等待营销/店铺广告按钮出现的最长秒数。
ENTRY_ELEMENT_TIMEOUT_SECONDS = 60
# 登录入口必须连续可见的秒数；达到该时间才判定未登录，短暂闪现不会触发登录流程。
LOGIN_PANEL_STABLE_SECONDS = 5
# 检测登录入口的最长总秒数；到期仍未连续可见满稳定时间时，按已经登录继续执行。
LOGIN_DETECTION_TIMEOUT_SECONDS = 10
# 单个按钮首次点击失败后的额外重试次数；值为 3 时最多执行 1 次首次点击加 3 次重试。
CLICK_RETRY_TIMES = 3
# 同一按钮相邻两次重试之间的基础等待秒数；实际还会随机增加 0 至 1 秒。
CLICK_RETRY_INTERVAL_SECONDS = 2
# 点击按钮后等待成功标志、下一按钮、面板或首个数据指标出现的最长秒数。
NEXT_ELEMENT_TIMEOUT_SECONDS = 30
# 数据加载完成标志首次未出现后允许的重查次数；值为 5 表示最多额外检查 5 次。
DATA_READY_RETRY_TIMES = 5
# 数据加载完成标志相邻两次检查的等待秒数；5 次重查最长等待约 10 秒。
DATA_READY_RETRY_INTERVAL_SECONDS = 2

# 任意两个真实按钮点击之间保持随机间隔，避免登录、菜单和日期按钮连续点击过快。
# 点击前还会把元素滚动到视口中间、移动鼠标到目标并短暂停留，尽量模拟人工操作。
# 两个真实按钮点击之间随机目标间隔的最小秒数；不足该间隔时程序会补足等待时间。
HUMAN_CLICK_INTERVAL_MIN_SECONDS = 2.5
# 两个真实按钮点击之间随机目标间隔的最大秒数；与上面的最小值共同生成随机间隔。
HUMAN_CLICK_INTERVAL_MAX_SECONDS = 4.5
# 本店铺首次点击前、以及按钮滚动到视口中心后的随机停留最小秒数。
HUMAN_PRE_CLICK_PAUSE_MIN_SECONDS = 0.5
# 本店铺首次点击前、以及按钮滚动到视口中心后的随机停留最大秒数。
HUMAN_PRE_CLICK_PAUSE_MAX_SECONDS = 1.2

# ---------------------------------------------------------------------------
# 一、店铺广告流程
# ---------------------------------------------------------------------------

# 广告流程按钮 XPath。单独定义是为了让“点击按钮”和“验证点击结果”使用完全相同的定位规则。
AD_MARKETING_BUTTON_XPATH = '//div[@class="p-menu-inline"]//div[@class="p-menu-item-title-txt"][normalize-space()="营销"]'
AD_STORE_BUTTON_XPATH = '//div[@class="p-menu-inline"]//div[@class="p-menu-item-title-txt"][normalize-space()="店铺广告"]'
AD_TIME_BUTTON_XPATH = '//span[@class="theme-arco-picker-suffix-icon"]'
AD_YESTERDAY_BUTTON_XPATH = '//button[contains(@class,"theme-arco-btn") and contains(@class,"theme-arco-btn-secondary") and contains(@class,"theme-arco-btn-size-mini") and contains(@class,"theme-arco-btn-shape-square")][contains(.,"昨") and contains(.,"天")]'
AD_7_DAYS_BUTTON_XPATH = '//button[contains(@class,"theme-arco-btn") and contains(@class,"theme-arco-btn-secondary") and contains(@class,"theme-arco-btn-size-mini") and contains(@class,"theme-arco-btn-shape-square")][contains(.,"近") and contains(.,"7") and contains(.,"天")]'
# 广告页面数据加载完成标志。该指标可见才表示广告页或切换后的日期数据已经加载完成。
# 后续页面结构变化时，只需要在这里替换成任意一个可靠的广告指标 XPath。
AD_DATA_READY_XPATH = '//div[normalize-space(.)="成本"]/ancestor::div[contains(@class,"overview-item")]//span[starts-with(@class,"overview-item-value-")][contains(.,"USD")]'


# 如果“店铺广告”已经可见，营销菜单已经展开，不再点击营销按钮，避免把菜单重新收起。
# 点击“店铺广告”后必须看见广告指标，才确认页面数据已经加载，可以继续操作日期。
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
        "success_xpath": AD_DATA_READY_XPATH,
        "success_state": "visible",
        "success_name": "广告页成本指标已经可见",
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

# 概览流程按钮 XPath。日期选项不可见时，会重新点击时间按钮并等待相应选项出现。
OVERVIEW_ANALYTICS_BUTTON_XPATH = '//div[@class="p-menu-inline"]//div[@class="p-menu-item-title-txt"][normalize-space(.)="数据分析"]'
OVERVIEW_PAGE_BUTTON_XPATH = '//span[contains(@class,"text-base") and contains(@class,"font-medium") and contains(@class,"text-neutral-text1")][normalize-space(.)="概览"]'
OVERVIEW_TIME_BUTTON_XPATH = '//div[contains(@class,"arco-picker-input")]//input[@placeholder="结束日期"]'
OVERVIEW_YESTERDAY_BUTTON_XPATH = '//div[contains(@class,"arco-typography")][normalize-space(.)="昨天"]'
OVERVIEW_7_DAYS_BUTTON_XPATH = '//div[contains(@class,"arco-typography")][normalize-space(.)="最近 7 天"]'
# 概览页面数据加载完成标志。该指标可见才表示概览页或切换后的日期数据已经加载完成。
# 后续页面结构变化时，只需要在这里替换成任意一个可靠的概览指标 XPath。
OVERVIEW_DATA_READY_XPATH = '//div[@class="pcm-smc"][contains(.,"GMV")]//div[@class="pcm-smc-content"]'


# 从店铺广告页面切换到数据分析概览页面；每一步都由下一个业务元素确认点击成功。
OVERVIEW_COMMON_CLICK_STEPS: list[dict[str, Any]] = [
    {
        "name": "点击数据分析",
        "xpath": OVERVIEW_ANALYTICS_BUTTON_XPATH,
        "wait_seconds": 1,
        "success_xpath": OVERVIEW_PAGE_BUTTON_XPATH,
        "success_state": "visible",
        "success_name": "概览按钮已经可见",
    },
    {
        "name": "点击概览",
        "xpath": OVERVIEW_PAGE_BUTTON_XPATH,
        "wait_seconds": 2,
        "success_xpath": OVERVIEW_DATA_READY_XPATH,
        "success_state": "visible",
        "success_name": "概览页GMV指标已经可见",
    },
]


# 数据概览时间范围切换步骤。
OVERVIEW_PERIOD_CLICK_STEPS: dict[str, list[dict[str, Any]]] = {
    "昨天": [
        {
            "name": "概览-点击时间按钮",
            "xpath": OVERVIEW_TIME_BUTTON_XPATH,
            "wait_seconds": 1,
            "success_xpath": OVERVIEW_YESTERDAY_BUTTON_XPATH,
            "success_state": "visible",
            "success_name": "概览昨天按钮已经出现",
        },
        {
            "name": "概览-点击昨天按钮",
            "xpath": OVERVIEW_YESTERDAY_BUTTON_XPATH,
            "wait_seconds": 2,
            "success_xpath": OVERVIEW_YESTERDAY_BUTTON_XPATH,
            "success_state": "hidden",
            "success_name": "概览昨天按钮已经消失",
        },
    ],
    "7天": [
        {
            "name": "概览-再次点击时间按钮",
            "xpath": OVERVIEW_TIME_BUTTON_XPATH,
            "wait_seconds": 1,
            "success_xpath": OVERVIEW_7_DAYS_BUTTON_XPATH,
            "success_state": "visible",
            "success_name": "概览最近7天按钮已经出现",
        },
        {
            "name": "概览-点击7天按钮",
            "xpath": OVERVIEW_7_DAYS_BUTTON_XPATH,
            "wait_seconds": 2,
            "success_xpath": OVERVIEW_7_DAYS_BUTTON_XPATH,
            "success_state": "hidden",
            "success_name": "概览最近7天按钮已经消失",
        },
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
        # 记录最近一次真实点击的时间；后续每次点击都会据此补足随机的人类操作间隔。
        self._last_button_click_at: float | None = None

    def collect(self, store_name: str, download_path: str = "", debugging_port: int | str | None = None) -> list[dict[str, Any]]:
        """接管紫鸟当前标签页，依次采集广告数据和数据分析概览。"""
        if not debugging_port:
            raise RuntimeError("紫鸟没有返回 debuggingPort，无法接管 TikTok 店铺")

        # 每间店铺重新计算点击节奏，避免上一间店铺的关闭时间影响当前店铺。
        self._last_button_click_at = None

        LOGGER.info("[TikTok][开始] 店铺=%s，准备接管紫鸟浏览器，debugging_port=%s", store_name, debugging_port)

        # 连接紫鸟已经打开的 Chromium，不创建普通浏览器，也不调用 tab.get()。
        browser = Chromium(f"127.0.0.1:{debugging_port}")
        tab = browser.latest_tab
        collected_at = datetime.now(timezone.utc).isoformat()
        LOGGER.info("[TikTok][浏览器] 店铺=%s，已取得紫鸟当前标签页", store_name)

        # 不等待 document.readyState。登录入口、营销按钮或店铺广告按钮谁先满足条件，就立即进入相应流程。
        login_required = self._is_login_required(tab)
        if login_required:
            self._perform_login(tab)

        if not self._wait_for_main_navigation(tab, ENTRY_ELEMENT_TIMEOUT_SECONDS):
            raise TimeoutError("TikTok 未在规定时间内出现营销按钮或店铺广告按钮，无法开始采集")
        self._close_interruptions(tab)
        LOGGER.info("[TikTok][页面入口] 已发现营销按钮或店铺广告按钮，不等待整页完成，立即开始采集")

        # 1. 点击“营销 -> 店铺广告”，分别采集昨天和 7 天广告数据。
        LOGGER.info("[TikTok][广告] 开始进入 营销 -> 店铺广告")
        ad_fields: dict[str, Any] = {}
        ad_raw_values: dict[str, str] = {}
        ad_yesterday_steps = AD_PERIOD_CLICK_STEPS.get("昨天", [])
        ad_navigation_ok = self._run_click_steps(tab, AD_COMMON_CLICK_STEPS, self._first_step_xpath(ad_yesterday_steps))
        # 即使步骤状态验证曾超时，只要广告指标最终可见，仍可确认页面数据已经加载完成。
        ad_page_ready = ad_navigation_ok or self._element_state_matches(tab, AD_DATA_READY_XPATH, "visible")
        if not ad_page_ready:
            LOGGER.error("[TikTok][广告页面失败] 未确认广告成本指标可见，本店铺两个广告周期均按空值处理")

        for period in ("昨天", "7天"):
            LOGGER.info("[TikTok][广告] 开始切换并采集时间范围=%s", period)
            if not ad_page_ready:
                self._record_empty_period_metrics(period, AD_METRIC_SPECS, ad_fields, ad_raw_values, "未进入店铺广告页面")
                continue

            period_steps = AD_PERIOD_CLICK_STEPS.get(period, [])
            period_clicked = self._run_period_click_steps(tab, period_steps)
            if not period_clicked:
                self._record_empty_period_metrics(period, AD_METRIC_SPECS, ad_fields, ad_raw_values, "日期按钮点击或状态验证失败")
                continue

            data_loaded = self._wait_for_data_ready_marker(
                tab,
                AD_DATA_READY_XPATH,
                f"广告-{period}成本指标",
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
        overview_navigation_ok = self._run_click_steps(
            tab,
            OVERVIEW_COMMON_CLICK_STEPS,
            self._first_step_xpath(overview_yesterday_steps),
        )
        overview_page_ready = overview_navigation_ok or self._element_state_matches(
            tab,
            OVERVIEW_DATA_READY_XPATH,
            "visible",
        )
        if not overview_page_ready:
            LOGGER.error("[TikTok][概览页面失败] 未确认概览GMV指标可见，本店铺两个概览周期均按空值处理")

        for period in ("昨天", "7天"):
            LOGGER.info("[TikTok][概览] 开始切换并采集时间范围=%s", period)
            if not overview_page_ready:
                self._record_empty_period_metrics(
                    period,
                    OVERVIEW_METRIC_SPECS,
                    overview_fields,
                    overview_raw_values,
                    "未进入数据分析概览页面",
                )
                continue

            period_steps = OVERVIEW_PERIOD_CLICK_STEPS.get(period, [])
            period_clicked = self._run_period_click_steps(tab, period_steps)
            if not period_clicked:
                self._record_empty_period_metrics(
                    period,
                    OVERVIEW_METRIC_SPECS,
                    overview_fields,
                    overview_raw_values,
                    "概览日期按钮点击或状态验证失败",
                )
                continue

            data_loaded = self._wait_for_data_ready_marker(
                tab,
                OVERVIEW_DATA_READY_XPATH,
                f"概览-{period}GMV指标",
            )
            if not data_loaded:
                self._record_empty_period_metrics(
                    period,
                    OVERVIEW_METRIC_SPECS,
                    overview_fields,
                    overview_raw_values,
                    "概览数据加载完成标志未出现",
                )
                continue

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
        """通过一次浏览器脚本读取某模块、某时间范围的全部指标，再逐字段转换和记录日志。"""
        period_specs = [spec for spec in specs if spec["period"] == period]
        batch_raw_values = self._read_metric_batch(tab, period, period_specs)
        for spec in period_specs:
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
            raw_text = batch_raw_values.get(field_name, "")
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

    def _read_metric_batch(
        self,
        tab: Any,
        period: str,
        specs: list[dict[str, str]],
    ) -> dict[str, str]:
        """在页面内一次执行全部 XPath；整批脚本异常时才回退为逐项读取。"""
        result = {str(spec.get("field") or ""): "" for spec in specs}
        configured_specs = [
            {"field": str(spec.get("field") or ""), "xpath": str(spec.get("xpath") or "").strip()}
            for spec in specs
            if str(spec.get("field") or "") and str(spec.get("xpath") or "").strip()
        ]
        empty_xpath_fields = [
            str(spec.get("field") or "")
            for spec in specs
            if str(spec.get("field") or "") and not str(spec.get("xpath") or "").strip()
        ]
        for field_name in empty_xpath_fields:
            LOGGER.warning("[TikTok][批量指标跳过] 时间范围=%s，字段=%s，原因=XPath 为空", period, field_name)

        if not configured_specs:
            LOGGER.warning("[TikTok][批量指标] 时间范围=%s，没有可执行的指标 XPath，全部返回空值", period)
            return result

        self._close_interruptions(tab)
        specs_json = json.dumps(configured_specs, ensure_ascii=False)
        script = f"""
            const metricSpecs = {specs_json};
            const batchResult = {{}};
            for (const metric of metricSpecs) {{
                try {{
                    const node = document.evaluate(
                        metric.xpath,
                        document,
                        null,
                        XPathResult.FIRST_ORDERED_NODE_TYPE,
                        null
                    ).singleNodeValue;
                    if (!node) {{
                        batchResult[metric.field] = "";
                        continue;
                    }}
                    const isInput = node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement;
                    const text = isInput ? node.value : (node.innerText ?? node.textContent ?? "");
                    batchResult[metric.field] = String(text).trim();
                }} catch (error) {{
                    batchResult[metric.field] = "";
                }}
            }}
            return JSON.stringify(batchResult);
        """
        try:
            started_at = time.monotonic()
            response = tab.run_js(script)
            parsed = json.loads(str(response or "{}"))
            if not isinstance(parsed, dict):
                raise TypeError(f"批量脚本返回类型不是字典: {type(parsed).__name__}")
            for field_name in result:
                result[field_name] = str(parsed.get(field_name) or "").strip()
            LOGGER.info(
                "[TikTok][批量指标读取完成] 时间范围=%s，一次读取=%s项，非空=%s项，耗时=%.2f秒，原始结果=%s",
                period,
                len(configured_specs),
                sum(bool(value) for value in result.values()),
                time.monotonic() - started_at,
                json.dumps(result, ensure_ascii=False),
            )
            return result
        except Exception as exc:
            LOGGER.exception(
                "[TikTok][批量指标异常] 时间范围=%s，一次读取失败，回退为逐项读取，异常=%s",
                period,
                exc,
            )
            for spec in specs:
                field_name = str(spec.get("field") or "")
                result[field_name] = self._read_xpath(tab, str(spec.get("xpath") or ""), field_name)
            return result

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

    def _is_login_required(self, tab: Any) -> bool:
        """不等待整页完成；业务菜单出现时立即按已登录处理，否则观察登录入口。"""
        started_at = time.monotonic()
        deadline = started_at + LOGIN_DETECTION_TIMEOUT_SECONDS
        continuously_visible_since: float | None = None
        LOGGER.info(
            "[TikTok][入口检测] 同时检查登录入口、营销按钮和店铺广告按钮；登录入口连续可见 %.1f 秒才判定未登录，最长观察 %.1f 秒",
            LOGIN_PANEL_STABLE_SECONDS,
            LOGIN_DETECTION_TIMEOUT_SECONDS,
        )

        while time.monotonic() < deadline:
            login_entry = self._find_visible_element(tab, LOGIN_EMAIL_PANEL_BUTTON_XPATH, timeout=0.5)
            now = time.monotonic()
            if login_entry:
                if continuously_visible_since is None:
                    continuously_visible_since = now
                    LOGGER.warning("[TikTok][登录检测] 发现邮箱登录入口，开始连续计时")
                visible_seconds = now - continuously_visible_since
                if visible_seconds >= LOGIN_PANEL_STABLE_SECONDS:
                    LOGGER.warning(
                        "[TikTok][登录检测] 邮箱登录入口已连续可见 %.2f 秒，确认当前店铺未登录",
                        visible_seconds,
                    )
                    return True
            else:
                navigation_name = self._main_navigation_name(tab)
                if navigation_name:
                    LOGGER.info(
                        "[TikTok][入口检测成功] 未发现登录入口且已发现%s，不等待 document.readyState，立即按已登录状态继续",
                        navigation_name,
                    )
                    return False
                if continuously_visible_since is not None:
                    LOGGER.info(
                        "[TikTok][登录检测] 登录入口连续出现 %.2f 秒后消失，未达到 %.1f 秒，重新计时",
                        now - continuously_visible_since,
                        LOGIN_PANEL_STABLE_SECONDS,
                    )
                continuously_visible_since = None

            # 登录判断完成前只移动鼠标，不允许关闭验证码或首页弹窗。
            self._human_wait(tab, 0.5, check_interruptions=False)

        LOGGER.info(
            "[TikTok][登录检测] 观察 %.2f 秒未发现连续可见满 %.1f 秒的登录入口，按已登录状态继续",
            time.monotonic() - started_at,
            LOGIN_PANEL_STABLE_SECONDS,
        )
        return False

    def _perform_login(self, tab: Any) -> None:
        """只有出现手机号格式错误时才切换邮箱；最终以业务菜单出现确认登录成功。"""
        LOGGER.warning("[TikTok][登录流程] 开始处理 TikTok 登录，登录完成前暂停其他弹窗检测")

        switched_to_email = self._element_state_matches(tab, LOGIN_PHONE_FORMAT_ERROR_XPATH, "visible")
        if switched_to_email:
            LOGGER.warning("[TikTok][登录判断] 已发现“请检查输入的手机号格式”，先切换邮箱再登录")
            self._switch_to_email_login(tab)
        else:
            LOGGER.info("[TikTok][登录判断] 当前没有手机号格式错误提示，不切换邮箱，直接点击登录")

        self._click_login_button(tab, "登录-点击登录按钮")

        # 某些页面只有第一次按手机号模式提交后才显示格式错误，因此再观察一次提交结果。
        if not switched_to_email:
            login_outcome = self._wait_for_direct_login_outcome(tab, LOGIN_DETECTION_TIMEOUT_SECONDS)
            if login_outcome == "phone_error":
                LOGGER.warning("[TikTok][登录判断] 直接登录后出现手机号格式错误，开始切换邮箱并重新登录")
                self._switch_to_email_login(tab)
                self._click_login_button(tab, "登录-邮箱模式重新点击登录按钮")

        if not self._wait_for_main_navigation(tab, ENTRY_ELEMENT_TIMEOUT_SECONDS):
            raise TimeoutError("TikTok 登录后未出现营销按钮或店铺广告按钮")
        LOGGER.info("[TikTok][登录成功] 已发现营销按钮或店铺广告按钮，不等待整页加载完成")

    def _switch_to_email_login(self, tab: Any) -> None:
        """点击邮箱切换按钮，并用 selected 标志确认切换完成。"""
        email_mode_ready = self._click_with_retry(
            tab,
            LOGIN_EMAIL_PANEL_BUTTON_XPATH,
            "登录-点击使用邮箱登录",
            success_xpath=LOGIN_EMAIL_SELECTED_XPATH,
            success_state="visible",
            success_name="邮箱登录入口已经切换为 selected 状态",
            check_interruptions=False,
        )
        if not email_mode_ready:
            raise RuntimeError("TikTok 已出现手机号格式错误，但无法切换到邮箱登录状态")

    def _click_login_button(self, tab: Any, step_name: str) -> None:
        """点击登录按钮；失败时直接终止当前店铺，避免错误状态继续执行。"""
        if not self._click_with_retry(
            tab,
            LOGIN_BUTTON_XPATH,
            step_name,
            check_interruptions=False,
        ):
            raise RuntimeError("TikTok 登录按钮点击失败")

    def _wait_for_direct_login_outcome(self, tab: Any, timeout_seconds: float) -> str:
        """直接登录后等待业务菜单或手机号格式错误出现，不检查 document.readyState。"""
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        LOGGER.info("[TikTok][登录结果等待] 最长 %.1f 秒等待业务菜单或手机号格式错误提示", timeout_seconds)
        while time.monotonic() < deadline:
            if self._element_state_matches(tab, LOGIN_PHONE_FORMAT_ERROR_XPATH, "visible"):
                LOGGER.warning("[TikTok][登录结果] 已出现“请检查输入的手机号格式”")
                return "phone_error"
            navigation_name = self._main_navigation_name(tab)
            if navigation_name:
                LOGGER.info("[TikTok][登录结果] 已发现%s，直接登录成功", navigation_name)
                return "navigation"
            self._human_wait(tab, 0.5, check_interruptions=False)
        LOGGER.info("[TikTok][登录结果等待] %.1f 秒内没有明确结果，继续等待业务菜单", time.monotonic() - started_at)
        return "pending"

    def _wait_for_main_navigation(self, tab: Any, timeout_seconds: float) -> bool:
        """轮询营销/店铺广告按钮；任意一个可见就立即继续，不等待整页加载完成。"""
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        LOGGER.info("[TikTok][业务入口等待] 最长 %.1f 秒等待营销按钮或店铺广告按钮", timeout_seconds)
        while time.monotonic() < deadline:
            navigation_name = self._main_navigation_name(tab)
            if navigation_name:
                LOGGER.info(
                    "[TikTok][业务入口出现] 已发现%s，耗时=%.2f秒",
                    navigation_name,
                    time.monotonic() - started_at,
                )
                return True
            self._human_wait(tab, 0.5, check_interruptions=False)
        LOGGER.error("[TikTok][业务入口超时] 未找到营销按钮或店铺广告按钮")
        return False

    def _main_navigation_name(self, tab: Any) -> str:
        """返回当前可见的业务入口名称；优先识别已经展开的店铺广告按钮。"""
        if self._find_visible_element(tab, AD_STORE_BUTTON_XPATH, timeout=0.2):
            return "店铺广告按钮"
        if self._find_visible_element(tab, AD_MARKETING_BUTTON_XPATH, timeout=0.2):
            return "营销按钮"
        return ""

    def _prepare_human_click(self, tab: Any, element: Any, step_name: str) -> None:
        """在真实点击前补足随机间隔，并执行居中滚动、鼠标移动和短暂停留。"""
        target_interval = random.uniform(HUMAN_CLICK_INTERVAL_MIN_SECONDS, HUMAN_CLICK_INTERVAL_MAX_SECONDS)
        if self._last_button_click_at is None:
            # 第一个按钮前也稍作停留，避免页面刚加载完就立即发生机械点击。
            first_pause = random.uniform(HUMAN_PRE_CLICK_PAUSE_MIN_SECONDS, HUMAN_PRE_CLICK_PAUSE_MAX_SECONDS)
            LOGGER.info(
                "[TikTok][仿人点击准备] 步骤=%s，当前是本店铺首次点击，先观察页面 %.2f 秒",
                step_name,
                first_pause,
            )
            self._human_wait(tab, first_pause, check_interruptions=False)
        else:
            elapsed = time.monotonic() - self._last_button_click_at
            remaining = max(0.0, target_interval - elapsed)
            if remaining > 0:
                LOGGER.info(
                    "[TikTok][仿人点击间隔] 步骤=%s，距上次点击 %.2f 秒，随机目标 %.2f 秒，继续等待 %.2f 秒",
                    step_name,
                    elapsed,
                    target_interval,
                    remaining,
                )
                self._human_wait(tab, remaining, check_interruptions=False)
            else:
                LOGGER.info(
                    "[TikTok][仿人点击间隔] 步骤=%s，距上次点击 %.2f 秒，已达到随机目标 %.2f 秒",
                    step_name,
                    elapsed,
                    target_interval,
                )

        scrolled = False
        try:
            scroll = getattr(element, "scroll", None)
            to_see = getattr(scroll, "to_see", None) if scroll is not None else None
            if callable(to_see):
                to_see(center=True)
                scrolled = True
        except Exception as exc:
            LOGGER.debug("[TikTok][仿人滚动] 步骤=%s，DrissionPage 居中滚动失败=%s", step_name, exc)
        if not scrolled:
            try:
                element.run_js("this.scrollIntoView({behavior:'smooth', block:'center', inline:'center'});")
                scrolled = True
            except Exception as exc:
                LOGGER.debug("[TikTok][仿人滚动] 步骤=%s，JavaScript 居中滚动失败=%s", step_name, exc)
        LOGGER.info("[TikTok][仿人滚动] 步骤=%s，滚动到按钮附近结果=%s", step_name, "成功" if scrolled else "跳过")

        scroll_pause = random.uniform(HUMAN_PRE_CLICK_PAUSE_MIN_SECONDS, HUMAN_PRE_CLICK_PAUSE_MAX_SECONDS)
        self._human_wait(tab, scroll_pause, check_interruptions=False)
        moved_to_target = self._human_mouse_move_to_element(tab, element)
        hover_pause = random.uniform(0.3, 0.8)
        LOGGER.info(
            "[TikTok][仿人鼠标] 步骤=%s，移动到目标结果=%s，点击前停留 %.2f 秒",
            step_name,
            "成功" if moved_to_target else "使用随机移动代替",
            hover_pause,
        )
        # 此处直接停留，不再调用会随机移动鼠标的 _human_wait，确保光标停在目标附近再点击。
        time.sleep(hover_pause)

    @staticmethod
    def _human_mouse_move_to_element(tab: Any, element: Any) -> bool:
        """把鼠标平滑移动到目标元素；失败时保留原有随机移动作为后备。"""
        try:
            from DrissionPage import Actions

            Actions(tab).move_to(element, duration=random.uniform(0.4, 0.9))
            return True
        except Exception:
            TiktokAuto._human_mouse_move(tab)
            return False

    def _record_button_click(self) -> None:
        """记录真实点击完成时间，供下一个按钮计算最小随机间隔。"""
        self._last_button_click_at = time.monotonic()

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

    def _run_period_click_steps(self, tab: Any, steps: list[dict[str, Any]]) -> bool:
        """执行“打开日期面板 -> 选择日期”；日期选项消失时自动重新打开面板。"""
        if len(steps) < 2:
            LOGGER.error("[TikTok][日期流程配置错误] 至少需要时间面板和日期选项两个步骤，当前步骤数=%s", len(steps))
            return False

        open_step = steps[0]
        option_step = steps[1]
        open_name = str(open_step.get("name") or "打开日期面板")
        open_xpath = str(open_step.get("xpath") or "").strip()
        option_name = str(option_step.get("name") or "选择日期")
        option_xpath = str(option_step.get("xpath") or "").strip()
        if not open_xpath or not option_xpath:
            LOGGER.error(
                "[TikTok][日期流程配置错误] 步骤=%s/%s，时间按钮或日期选项 XPath 为空",
                open_name,
                option_name,
            )
            return False

        LOGGER.info("[TikTok][日期流程] 先执行上一步=%s，确认日期选项出现后再执行=%s", open_name, option_name)
        panel_opened = self._click_with_retry(
            tab,
            open_xpath,
            open_name,
            success_xpath=option_xpath,
            success_state="visible",
            success_name=str(open_step.get("success_name") or "日期选项已经出现"),
        )
        if panel_opened:
            wait_seconds = float(open_step.get("wait_seconds", 1) or 0)
            if wait_seconds > 0:
                self._human_wait(tab, wait_seconds, check_interruptions=True)
        else:
            # 不在这里终止：下一步会再次检查日期选项；不可见时由 recovery_xpath 重做本步骤。
            LOGGER.warning("[TikTok][日期流程恢复] 上一步=%s 未确认成功，下一步将按页面实际可见性决定是否重开面板", open_name)

        option_clicked = self._click_with_retry(
            tab,
            option_xpath,
            option_name,
            success_xpath=str(option_step.get("success_xpath") or option_xpath).strip(),
            success_state=str(option_step.get("success_state") or "hidden").strip().lower(),
            success_name=str(option_step.get("success_name") or "日期选项已经消失"),
            recovery_xpath=open_xpath,
            recovery_step_name=f"{open_name}（日期选项不可见，重新执行上一步）",
            recovery_success_name=str(open_step.get("success_name") or "日期选项已经重新出现"),
        )
        if not option_clicked:
            LOGGER.error("[TikTok][日期流程失败] 日期选项=%s 点击及恢复均失败", option_name)
            return False

        wait_seconds = float(option_step.get("wait_seconds", 2) or 0)
        if wait_seconds > 0:
            LOGGER.info("[TikTok][日期流程] 日期选项=%s 点击成功，等待 %.1f 秒", option_name, wait_seconds)
            self._human_wait(tab, wait_seconds, check_interruptions=True)
        return True

    def _click_with_retry(
        self,
        tab: Any,
        xpath: str,
        step_name: str = "未命名按钮",
        success_xpath: str = "",
        success_state: str = "",
        success_name: str = "点击后的页面状态",
        check_interruptions: bool = True,
        recovery_xpath: str = "",
        recovery_step_name: str = "重新执行上一步",
        recovery_success_name: str = "目标按钮已经重新出现",
    ) -> bool:
        """按钮最多点击 4 次；目标不可见且配置 recovery_xpath 时，先重新执行上一步。"""
        max_attempts = CLICK_RETRY_TIMES + 1
        click_dispatched = False
        for attempt in range(max_attempts):
            # 对“展开菜单、打开面板”类步骤，目标元素已经可见就说明当前状态正确，不应再次点击把它关闭。
            if success_xpath and success_state == "visible" and self._element_state_matches(tab, success_xpath, "visible"):
                LOGGER.info("[TikTok][按钮验证成功] 步骤=%s，%s，无需再次点击", step_name, success_name)
                return True
            # 只有确实发出过点击后，才允许用“选项已经隐藏”确认延迟生效，防止首次检查时误判成功。
            if click_dispatched and success_xpath and success_state == "hidden" and self._element_state_matches(tab, success_xpath, "hidden"):
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
                self._human_wait(tab, interval, check_interruptions=check_interruptions)

            if check_interruptions:
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
                if not element and recovery_xpath:
                    LOGGER.warning(
                        "[TikTok][按钮恢复上一步] 步骤=%s，第 %s/%s 次尝试发现目标不可见；不直接重试目标，先执行=%s，xpath=%s",
                        step_name,
                        attempt + 1,
                        max_attempts,
                        recovery_step_name,
                        recovery_xpath,
                    )
                    recovered = self._click_with_retry(
                        tab,
                        recovery_xpath,
                        recovery_step_name,
                        success_xpath=xpath,
                        success_state="visible",
                        success_name=recovery_success_name,
                        check_interruptions=check_interruptions,
                    )
                    if recovered:
                        element = self._find_visible_element(tab, xpath, timeout=5)
                        LOGGER.info(
                            "[TikTok][按钮恢复结果] 步骤=%s，重新执行上一步后目标可见=%s",
                            step_name,
                            "是" if element else "否",
                        )
                if not element:
                    LOGGER.warning(
                        "[TikTok][按钮未找到] 步骤=%s，第 %s/%s 次尝试未找到可见元素",
                        step_name,
                        attempt + 1,
                        max_attempts,
                    )
                    continue
                self._prepare_human_click(tab, element, step_name)
                element.click()
                click_dispatched = True
                self._record_button_click()
                if check_interruptions:
                    self._close_interruptions(tab)

                if success_xpath and success_state:
                    LOGGER.info("[TikTok][按钮已点击] 步骤=%s，开始验证=%s", step_name, success_name)
                    verified = self._wait_for_element_state(
                        tab,
                        success_xpath,
                        success_state,
                        NEXT_ELEMENT_TIMEOUT_SECONDS,
                        success_name,
                        check_interruptions=check_interruptions,
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
        check_interruptions: bool = True,
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
            if check_interruptions:
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
            self._human_wait(tab, 0.5, check_interruptions=check_interruptions)

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

    def _wait_for_data_ready_marker(self, tab: Any, xpath: str, marker_name: str) -> bool:
        """检查数据加载完成标志；首次未找到后每 2 秒重查，最多重查 5 次。"""
        xpath = str(xpath or "").strip()
        if not xpath:
            LOGGER.warning("[TikTok][数据标志跳过] 目标=%s，XPath 尚未填写，暂不阻止后续批量读取", marker_name)
            return True

        total_checks = DATA_READY_RETRY_TIMES + 1
        started_at = time.monotonic()
        LOGGER.info(
            "[TikTok][数据标志等待] 目标=%s，首次检查失败后每 %.1f 秒重查，最多重查 %s 次，xpath=%s",
            marker_name,
            DATA_READY_RETRY_INTERVAL_SECONDS,
            DATA_READY_RETRY_TIMES,
            xpath,
        )
        for check_index in range(total_checks):
            self._close_interruptions(tab)
            try:
                marker = self._find_visible_element(tab, xpath, timeout=0.2)
            except Exception as exc:
                marker = None
                LOGGER.warning(
                    "[TikTok][数据标志异常] 目标=%s，第 %s/%s 次检查异常=%s",
                    marker_name,
                    check_index + 1,
                    total_checks,
                    exc,
                )

            if marker:
                LOGGER.info(
                    "[TikTok][数据标志出现] 目标=%s，第 %s/%s 次检查找到标志，耗时=%.2f秒",
                    marker_name,
                    check_index + 1,
                    total_checks,
                    time.monotonic() - started_at,
                )
                return True

            if check_index < DATA_READY_RETRY_TIMES:
                LOGGER.warning(
                    "[TikTok][数据标志未出现] 目标=%s，第 %s/%s 次检查未找到，等待 %.1f 秒后重查",
                    marker_name,
                    check_index + 1,
                    total_checks,
                    DATA_READY_RETRY_INTERVAL_SECONDS,
                )
                self._human_wait(tab, DATA_READY_RETRY_INTERVAL_SECONDS, check_interruptions=True)

        LOGGER.error(
            "[TikTok][数据标志超时] 目标=%s，首次检查及 %s 次重查均未找到，累计等待约 %.1f 秒，xpath=%s",
            marker_name,
            DATA_READY_RETRY_TIMES,
            DATA_READY_RETRY_TIMES * DATA_READY_RETRY_INTERVAL_SECONDS,
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
                    self._prepare_human_click(tab, element, f"关闭{interruption_name}")
                    element.click()
                    self._record_button_click()
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

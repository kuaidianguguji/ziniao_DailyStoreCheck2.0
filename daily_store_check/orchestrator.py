"""每日店铺任务编排器。"""

from __future__ import annotations

import importlib
import json
import logging
import unicodedata
from datetime import datetime, timezone
from typing import Any

from .config import StoreTask
from .feishu_client import FeishuClient
from .ziniao_client import ZiniaoClient, ZiniaoStoreCloseError, ZiniaoStoreSession

LOGGER = logging.getLogger(__name__)


# TikTok 多维表和历史电子表的固定字段顺序，与用户建立的 33 个字段完全一致。
# 电子表按此顺序追加，避免使用字典插入顺序导致不同采集模块的列发生错位。
TIKTOK_TABLE_FIELD_ORDER: tuple[str, ...] = (
    "店铺名",
    "直播GMV",
    "短视频GMV",
    "7天均单价",
    "昨天ROI",
    "昨天订单数",
    "昨天GMV视频比",
    "7天订单数",
    "昨天总收入",
    "昨天SKU订单数",
    "7天GMV",
    "昨天商品访客数",
    "昨天曝光数",
    "7天成交件数",
    "7天曝光数",
    "7天成本",
    "昨天GMV直播比",
    "采集时间",
    "昨天GMV商品卡比",
    "7天总收入",
    "昨天成本",
    "7天GMV直播比",
    "商品卡GMV",
    "昨天客户数",
    "昨天去重曝光数",
    "7天客户数",
    "7天ROI",
    "昨天GMV",
    "7天商品访客数",
    "7天去重曝光数",
    "昨天均单价",
    "昨天成交件数",
    "7天SKU订单数",
)

# 飞书多维表的公式字段由飞书自己计算，调用新增记录接口时不能赋值。
# 这些字段的爬虫原始结果仍会写入历史电子表和机器人消息，方便核对公式。
TIKTOK_FORMULA_FIELDS: frozenset[str] = frozenset(
    {
        "昨天ROI",
        "昨天GMV视频比",
        "昨天GMV直播比",
        "昨天GMV商品卡比",
        "7天GMV直播比",
        "7天ROI",
    }
)


# Shopee 多维表和历史电子表的固定 26 字段顺序，严格采用用户提供的表格顺序。
# 除店铺名和采集时间外，其余 24 个字段全部来自 SP_auto.py 的指标结果。
SHOPEE_TABLE_FIELD_ORDER: tuple[str, ...] = (
    "店铺名",
    "7天ALL销售额",
    "昨天ALL广告支出回报率",
    "7天ALL订单量",
    "7天ALL广告支出回报率",
    "7天ALL优惠劵带来销售额",
    "昨天ALL优惠价金额",
    "7天ALL优惠价金额",
    "7天ALL商品已出售",
    "昨天ALL点击数",
    "昨天ALL订单量",
    "7天ALL点击数",
    "昨天ALL加购率",
    "7天ALL点击率",
    "昨天ALL商品已出售",
    "7天ALL展示次数",
    "昨天ALL花费",
    "昨天ALL优惠劵带来销售额",
    "7天ALL加购次数",
    "昨天ALL展示次数",
    "昨天ALL加购次数",
    "7天ALL加购率",
    "采集时间",
    "昨天ALL销售额",
    "7天ALL花费",
    "昨天ALL点击率",
)


# 美客多多维表和历史电子表的固定 32 字段顺序。
# 多维表只接收这些已建立字段，避免通用“指标/数值/原始数据”导致 WrongRequestBody。
MERCADO_TABLE_FIELD_ORDER: tuple[str, ...] = (
    "7天取消的销售数量",
    "30天取消的销售价值",
    "7天转换率",
    "30天已售件数",
    "30天购买意向",
    "7天意向购买转换率",
    "30天退货价值",
    "7天已售件数",
    "7天退货价值",
    "7天平均单价",
    "店铺名",
    "7天总销售额",
    "7天独立意向转换率",
    "7天独特的参观",
    "7天取消的销售价值",
    "7天购买意向",
    "7天销售量",
    "30天意向购买转换率",
    "7天退货数量",
    "30天访问",
    "30天总销售额",
    "30天独特的参观",
    "7天访问",
    "30天取消的销售数量",
    "30天独立意向转换率",
    "采集时间",
    "30天转换率",
    "30天退货数量",
    "30天平均单价",
    "30天销售量",
    "7天总转换率",
    "30天总转换率",
)

# 机器人消息需要把内部数值比例重新显示成人能直接阅读的百分比。
MERCADO_PROGRESS_FIELDS: frozenset[str] = frozenset(
    {
        "7天转换率",
        "7天意向购买转换率",
        "7天独立意向转换率",
        "30天意向购买转换率",
        "30天独立意向转换率",
        "30天转换率",
        "7天总转换率",
        "30天总转换率",
    }
)


class DailyStoreCheck:
    """控制表 -> 紫鸟单店铺 -> 平台爬虫 -> 飞书写入/推送的主流程。"""

    def __init__(self, config: dict[str, Any]):
        """创建飞书和紫鸟客户端，并准备当前轮次的店铺缓存。"""
        self.config = config
        self.feishu = FeishuClient(config)
        self.ziniao = ZiniaoClient(config)
        self.browser_list: list[dict[str, Any]] = []

    def run_once(self) -> None:
        """执行一轮任务；店铺永远按控制表顺序串行处理。"""
        # ALL_info 保存本轮所有平台、所有店铺的采集结果和失败状态。
        # 它只在全部店铺结束后用于 summary_recipients 汇总推送，不影响每店铺即时推送。
        ALL_info: list[dict[str, Any]] = []
        tasks = self.feishu.list_control_tasks()
        if not tasks:
            LOGGER.info("没有可执行店铺，可能是控制表为空、开关暂停或飞书尚未配置")
            return

        try:
            self._prepare_ziniao()
            close_failed = False
            for task in tasks:
                store_info = self._run_store(task)
                ALL_info.append(store_info)
                if store_info.get("中止后续店铺"):
                    LOGGER.critical("店铺 %s 未确认关闭，本轮不再打开后续店铺", task.store_name)
                    close_failed = True
                    break
            if close_failed:
                LOGGER.warning("本轮因店铺未确认关闭而提前结束，跳过90天数据清理并立即进入最终收尾")
            else:
                self._cleanup_retention()
        finally:
            # 即使旧数据清理或某个店铺异常，也尽量发送已经收集到的最终汇总；
            # 无论汇总推送是否成功，最后都必须退出紫鸟客户端。
            try:
                self._send_all_info_summary(ALL_info)
            finally:
                self.ziniao.exit_client()

    def _prepare_ziniao(self) -> None:
        """启动紫鸟、更新内核并缓存全部店铺信息。"""
        self.ziniao.start_client()
        self.ziniao.update_core()
        self.browser_list = self.ziniao.list_browsers()

    def _run_store(self, task: StoreTask) -> dict[str, Any]:
        """处理一间店铺并返回 ALL_info 项；context manager 确保店铺串行关闭。"""
        store_info: dict[str, Any] = {
            "店铺名": task.store_name,
            "平台": task.platform,
            "状态": "未执行",
            "采集时间": "",
            "数据": {},
        }
        identifier = task.browser_oauth or task.browser_id or self._find_browser_identifier(task.store_name)
        if not identifier:
            LOGGER.error("找不到店铺 %s 对应的紫鸟 browserOauth/browserId，跳过", task.store_name)
            error_message = "没有找到紫鸟店铺标识，请检查店铺名是否与紫鸟一致。"
            store_info["状态"] = "失败"
            store_info["错误"] = error_message
            self._safe_notify(task.recipient, f"{task.store_name} 数据任务失败", error_message)
            return store_info

        try:
            with ZiniaoStoreSession(self.ziniao, identifier, task.store_name) as session:
                crawler = self._load_crawler(task.platform)
                rows = crawler.collect(task.store_name, session.download_path, session.opened.get("debuggingPort"))
                collected_at, metric_values = self._extract_all_info_values(rows)
                store_info["采集时间"] = collected_at
                store_info["数据"] = metric_values
                self._write_feishu(task, rows)
                self._safe_notify(task.recipient, f"{task.store_name} {task.platform} 广告数据", self._format_rows(rows))
                store_info["状态"] = "成功"
        except ZiniaoStoreCloseError as exc:
            LOGGER.exception("店铺 %s 未能关闭，必须中止后续店铺", task.store_name)
            store_info["状态"] = "失败"
            store_info["错误"] = str(exc)
            store_info["中止后续店铺"] = True
            self._safe_notify(task.recipient, f"{task.store_name} 关闭失败", f"{exc}\n为避免同时打开多个店铺，已中止本轮后续店铺。")
        except Exception as exc:
            LOGGER.exception("店铺 %s 处理失败", task.store_name)
            store_info["状态"] = "失败"
            store_info["错误"] = str(exc)
            self._safe_notify(task.recipient, f"{task.store_name} 数据任务失败", str(exc))
        return store_info

    @staticmethod
    def _extract_all_info_values(rows: list[dict[str, Any]]) -> tuple[Any, dict[str, Any]]:
        """从平台爬虫结果提取采集时间和全部指标，供 ALL_info 保存。"""
        collected_at: Any = ""
        metric_values: dict[str, Any] = {}
        for row in rows:
            if not collected_at and row.get("采集时间"):
                collected_at = row["采集时间"]

            # Shopee 当前使用“一项指标一行”的通用结果结构，空值也保留在汇总中便于排错。
            metric_name = str(row.get("指标") or "").strip()
            if metric_name:
                metric_values[metric_name] = row.get("显示值", row.get("数值", ""))

            # TikTok 和美客多使用“飞书字段”字典；后写入的同名字段覆盖前一模块。
            platform_fields = row.get("飞书字段", {})
            if isinstance(platform_fields, dict):
                metric_values.update(platform_fields)
        return collected_at, metric_values

    def _send_all_info_summary(self, ALL_info: list[dict[str, Any]]) -> None:
        """全部店铺完成后，按姓名 -> open_id 字典发送 ALL_info 最终汇总。"""
        recipients = self.feishu.get_summary_recipients()
        if not recipients:
            LOGGER.info("[飞书][ALL_info汇总] summary_recipients 为空，不发送最终全店铺汇总")
            return
        if not ALL_info:
            LOGGER.info("[飞书][ALL_info汇总] 本轮没有店铺结果，不发送最终全店铺汇总")
            return

        summary_text = json.dumps(ALL_info, ensure_ascii=False, indent=2, default=str)
        LOGGER.info(
            "[飞书][ALL_info打包] 店铺数=%s，接收人数=%s，ALL_info=%s",
            len(ALL_info),
            len(recipients),
            json.dumps(ALL_info, ensure_ascii=False, default=str),
        )
        for recipient_name, receive_id in recipients.items():
            LOGGER.info("[飞书][ALL_info发送] 接收人姓名=%s，准备发送全部店铺汇总", recipient_name)
            self._safe_notify(receive_id, "全部平台全部店铺数据汇总", summary_text)

    def _safe_notify(self, recipient: str, title: str, content: str) -> None:
        """推送失败只记录日志，不影响关闭店铺和后续店铺。"""
        try:
            LOGGER.info(
                "[飞书][机器人消息打包] recipient=%s，title=%r，content=%r",
                recipient or "<空接收人>",
                title,
                content,
            )
            self.feishu.send_robot_message(recipient, title, content)
        except Exception:
            LOGGER.exception("飞书消息推送失败 recipient=%s", recipient)

    def _find_browser_identifier(self, store_name: str) -> str:
        """按店铺名匹配紫鸟店铺，优先 browserOauth。"""
        wanted = str(store_name).strip().casefold()
        for browser in self.browser_list:
            name = str(browser.get("browserName") or browser.get("name") or "").strip().casefold()
            if name == wanted:
                return str(browser.get("browserOauth") or browser.get("browserId") or "")
        return ""

    def _load_crawler(self, platform: str) -> Any:
        """从配置中的 module:Class 动态加载平台爬虫。"""
        crawler_path = self.config.get("platforms", {}).get(platform, {}).get("crawler", "")
        if ":" not in crawler_path:
            raise ValueError(f"平台 {platform} 未配置 crawler")
        module_name, class_name = crawler_path.split(":", 1)
        crawler_class = getattr(importlib.import_module(module_name), class_name)
        platform_config = self.config.get("platforms", {}).get(platform, {})
        return crawler_class(platform_config)

    def _write_feishu(self, task: StoreTask, rows: list[dict[str, Any]]) -> None:
        """把标准行写入对应多维表，并追加到对应历史电子表。"""
        feishu_cfg = self.config.get("feishu", {})
        app_token, table_id = self.feishu.get_bitable_ref("data", task.platform)
        if task.platform == "tiktok":
            record_rows, spreadsheet_rows = self._build_tiktok_feishu_rows(task, rows)
        elif task.platform == "shopee":
            record_rows, spreadsheet_rows = self._build_shopee_feishu_rows(task, rows)
        elif task.platform == "mercado":
            record_rows, spreadsheet_rows = self._build_mercado_feishu_rows(task, rows)
        else:
            record_rows, spreadsheet_rows = self._build_default_feishu_rows(task, rows)

        LOGGER.info(
            "[飞书][传输准备] 店铺=%s，平台=%s，多维表记录数=%s，电子表行数=%s",
            task.store_name,
            task.platform,
            len(record_rows),
            len(spreadsheet_rows),
        )
        write_errors: list[str] = []

        # 多维表和历史电子表是两个独立目标；一个失败时仍然尝试另一个，避免采集数据全部丢失。
        try:
            bitable_rows = record_rows
            if task.platform == "shopee":
                bitable_rows = self._align_shopee_bitable_fields(record_rows, app_token, table_id)
            self.feishu.batch_create_records(table_id, bitable_rows, app_token=app_token)
        except Exception as exc:
            LOGGER.exception("[飞书][多维表写入失败] 店铺=%s，平台=%s", task.store_name, task.platform)
            write_errors.append(f"多维表写入失败: {exc}")

        try:
            spreadsheet_cfg = feishu_cfg.get("spreadsheets", {}).get(task.platform, "")
            if isinstance(spreadsheet_cfg, dict):
                token = str(spreadsheet_cfg.get("token") or spreadsheet_cfg.get("spreadsheet_token") or "")
                sheet_id = str(spreadsheet_cfg.get("sheet_id") or "")
                range_end_by_platform = {"tiktok": "AG", "shopee": "Z", "mercado": "AF"}
                range_end = range_end_by_platform.get(task.platform, "Z")
                range_name = str(spreadsheet_cfg.get("range") or (f"{sheet_id}!A:{range_end}" if sheet_id else f"Sheet1!A:{range_end}"))
                self.feishu.append_spreadsheet_rows(token, spreadsheet_rows, range_name)
            else:
                self.feishu.append_spreadsheet_rows(str(spreadsheet_cfg), spreadsheet_rows)
        except Exception as exc:
            LOGGER.exception("[飞书][电子表写入失败] 店铺=%s，平台=%s", task.store_name, task.platform)
            write_errors.append(f"电子表写入失败: {exc}")

        if write_errors:
            raise RuntimeError("；".join(write_errors))

    def _align_shopee_bitable_fields(
        self,
        records: list[dict[str, Any]],
        app_token: str,
        table_id: str,
    ) -> list[dict[str, Any]]:
        """按飞书真实字段名对齐 Shopee 记录，兼容“券/劵”和首尾空格差异。"""
        try:
            actual_fields = self.feishu.list_table_field_names(table_id, app_token=app_token)
        except Exception:
            LOGGER.exception("[飞书][SP字段预检失败] 无法读取真实字段，暂时沿用代码字段名")
            return records
        if not actual_fields:
            return records

        actual_by_normalized: dict[str, list[str]] = {}
        for actual_name in actual_fields:
            normalized = self._normalise_feishu_field_name(actual_name)
            actual_by_normalized.setdefault(normalized, []).append(actual_name)

        aligned_records: list[dict[str, Any]] = []
        for record_index, record in enumerate(records, start=1):
            aligned: dict[str, Any] = {}
            missing_required: list[str] = []
            for code_name, value in record.items():
                if code_name in actual_fields:
                    aligned[code_name] = value
                    continue

                candidates = actual_by_normalized.get(self._normalise_feishu_field_name(code_name), [])
                if len(candidates) == 1:
                    actual_name = candidates[0]
                    aligned[actual_name] = value
                    LOGGER.warning("[飞书][SP字段自动对齐] 代码字段=%r -> 真实字段=%r", code_name, actual_name)
                    continue

                if code_name in {"店铺名", "采集时间"}:
                    missing_required.append(code_name)
                elif len(candidates) > 1:
                    LOGGER.error("[飞书][SP字段歧义] 代码字段=%r 匹配到多个真实字段=%s，本次跳过", code_name, candidates)
                else:
                    LOGGER.error("[飞书][SP字段不存在] 代码字段=%r 不在真实多维表中，本次跳过该字段", code_name)

            if missing_required:
                raise RuntimeError(f"Shopee 多维表缺少必要字段: {missing_required}")
            aligned_records.append(aligned)
            LOGGER.info(
                "[飞书][SP字段预检完成] 第 %s 条记录，发送字段数=%s，字段=%s",
                record_index,
                len(aligned),
                list(aligned),
            )
        return aligned_records

    @staticmethod
    def _normalise_feishu_field_name(field_name: str) -> str:
        """仅用于字段匹配：统一全半角、首尾空格，并把易混淆的“劵”归一为“券”。"""
        return unicodedata.normalize("NFKC", str(field_name)).strip().replace("劵", "券")

    def _build_default_feishu_rows(
        self,
        task: StoreTask,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[list[Any]]]:
        """为以后新增但尚未提供专用飞书字段结构的平台打包通用字段。"""
        bitable = self.config.get("feishu", {}).get("bitable", {})
        fields = bitable.get("data_fields", {})
        now = datetime.now(timezone.utc).isoformat()
        record_rows: list[dict[str, Any]] = []
        spreadsheet_rows: list[list[Any]] = []
        for row in rows:
            collected_at = row.get("采集时间", now)
            record = {
                fields.get("store_name", "店铺名"): row.get("店铺名", task.store_name),
                fields.get("collected_at", "采集时间"): self._to_feishu_timestamp_ms(collected_at),
                fields.get("metric", "指标"): row.get("指标", ""),
                fields.get("value", "数值"): row.get("数值", ""),
                fields.get("raw_data", "原始数据"): row.get("原始数据", ""),
            }
            # 平台可以提供自己的字段字典。美客多使用独立的 32 字段打包逻辑，
            # 其他平台没有该字段时仍沿用上面的通用结构。
            platform_fields = row.get("飞书字段", {})
            if isinstance(platform_fields, dict):
                # 空字符串不能写入飞书数字、货币、百分比等字段，因此空指标不进入请求体。
                record.update({name: value for name, value in platform_fields.items() if value not in ("", None)})
            record_rows.append(record)
            spreadsheet_row = [
                record.get(fields.get("store_name", "店铺名")),
                collected_at,
                task.platform,
                record.get(fields.get("metric", "指标")),
                record.get(fields.get("value", "数值")),
                record.get(fields.get("raw_data", "原始数据")),
            ]
            if isinstance(platform_fields, dict):
                spreadsheet_row.extend(platform_fields.values())
            spreadsheet_rows.append(spreadsheet_row)
        return record_rows, spreadsheet_rows

    def _build_tiktok_feishu_rows(
        self,
        task: StoreTask,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[list[Any]]]:
        """把 TikTok 广告和概览结果合并成一条 33 字段记录。"""
        merged_fields: dict[str, Any] = {}
        collected_at: Any = ""
        for row_index, row in enumerate(rows, start=1):
            if not collected_at and row.get("采集时间"):
                collected_at = row["采集时间"]
            platform_fields = row.get("飞书字段", {})
            if not isinstance(platform_fields, dict):
                LOGGER.warning("[飞书][TK打包] 第 %s 条爬虫结果的 飞书字段 不是字典，已跳过", row_index)
                continue
            for field_name, value in platform_fields.items():
                previous_value = merged_fields.get(field_name, "")
                if previous_value not in ("", None) and value not in ("", None) and previous_value != value:
                    LOGGER.warning(
                        "[飞书][TK字段冲突] 字段=%s，前一模块值=%r，后一模块值=%r；采用后一模块值",
                        field_name,
                        previous_value,
                        value,
                    )
                if value not in ("", None) or field_name not in merged_fields:
                    merged_fields[field_name] = value

        if not collected_at:
            collected_at = datetime.now(timezone.utc).isoformat()

        known_fields = set(TIKTOK_TABLE_FIELD_ORDER)
        unknown_fields = sorted(set(merged_fields) - known_fields)
        if unknown_fields:
            LOGGER.warning("[飞书][TK未知字段] 以下字段不在 33 字段定义中，不写入多维表：%s", unknown_fields)

        formula_values = {
            field_name: merged_fields[field_name]
            for field_name in TIKTOK_FORMULA_FIELDS
            if merged_fields.get(field_name) not in ("", None)
        }
        if formula_values:
            LOGGER.info("[飞书][TK公式字段] 公式字段只读，不写入多维表；抓取值仍保留到电子表和机器人：%s", formula_values)

        # 多维表只发送店铺名、毫秒时间戳和非空的可写业务字段。
        bitable_record: dict[str, Any] = {
            "店铺名": task.store_name,
            "采集时间": self._to_feishu_timestamp_ms(collected_at),
        }
        for field_name in TIKTOK_TABLE_FIELD_ORDER:
            if field_name in {"店铺名", "采集时间"} or field_name in TIKTOK_FORMULA_FIELDS:
                continue
            value = merged_fields.get(field_name, "")
            if value not in ("", None):
                bitable_record[field_name] = value

        missing_fields = [
            field_name
            for field_name in TIKTOK_TABLE_FIELD_ORDER
            if field_name not in {"店铺名", "采集时间"}
            and field_name not in TIKTOK_FORMULA_FIELDS
            and merged_fields.get(field_name, "") in ("", None)
        ]
        if missing_fields:
            LOGGER.warning("[飞书][TK空字段] 以下可写字段本次无数据，不放入多维表请求体：%s", missing_fields)

        # 历史电子表保留完整 33 列。公式字段使用本次爬虫读到的值，未抓到的字段写空单元格。
        spreadsheet_values = dict(merged_fields)
        spreadsheet_values["店铺名"] = task.store_name
        spreadsheet_values["采集时间"] = collected_at
        spreadsheet_row = [spreadsheet_values.get(field_name, "") for field_name in TIKTOK_TABLE_FIELD_ORDER]
        return [bitable_record], [spreadsheet_row]

    def _build_shopee_feishu_rows(
        self,
        task: StoreTask,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[list[Any]]]:
        """把 Shopee 的 24 项指标合并成一条严格匹配 26 字段表结构的记录。"""
        merged_fields: dict[str, Any] = {}
        collected_at: Any = ""
        known_fields = set(SHOPEE_TABLE_FIELD_ORDER)

        for row_index, row in enumerate(rows, start=1):
            if not collected_at and row.get("采集时间"):
                collected_at = row["采集时间"]

            # SP_auto.py 当前按“一项指标一行”返回，通过“指标”和“数值”合并。
            metric_name = str(row.get("指标") or "").strip()
            if metric_name:
                if metric_name in known_fields:
                    value = row.get("数值", "")
                    if value not in ("", None) or metric_name not in merged_fields:
                        merged_fields[metric_name] = value
                else:
                    LOGGER.warning("[飞书][SP未知字段] 第 %s 条指标=%s 不在 26 字段定义中，已忽略", row_index, metric_name)

            # 同时兼容以后 SP_auto.py 直接返回“飞书字段”字典的写法。
            platform_fields = row.get("飞书字段", {})
            if not isinstance(platform_fields, dict):
                LOGGER.warning("[飞书][SP打包] 第 %s 条爬虫结果的 飞书字段 不是字典，已忽略该属性", row_index)
                continue
            for field_name, value in platform_fields.items():
                if field_name not in known_fields:
                    LOGGER.warning("[飞书][SP未知字段] 字段=%s 不在 26 字段定义中，已忽略", field_name)
                    continue
                if value not in ("", None) or field_name not in merged_fields:
                    merged_fields[field_name] = value

        if not collected_at:
            collected_at = datetime.now(timezone.utc).isoformat()

        # 飞书金额和小数字段不能接收空字符串，因此只把非空业务指标加入请求 JSON。
        bitable_record: dict[str, Any] = {
            "店铺名": task.store_name,
            "采集时间": self._to_feishu_timestamp_ms(collected_at),
        }
        for field_name in SHOPEE_TABLE_FIELD_ORDER:
            if field_name in {"店铺名", "采集时间"}:
                continue
            value = merged_fields.get(field_name, "")
            if value not in ("", None):
                bitable_record[field_name] = value

        missing_fields = [
            field_name
            for field_name in SHOPEE_TABLE_FIELD_ORDER
            if field_name not in {"店铺名", "采集时间"}
            and merged_fields.get(field_name, "") in ("", None)
        ]
        if missing_fields:
            LOGGER.warning("[飞书][SP空字段] 以下字段本次无数据，不放入多维表请求体：%s", missing_fields)

        # 历史电子表固定保留 26 列，空指标写空单元格，采集时间使用便于阅读的 ISO 文本。
        spreadsheet_values = dict(merged_fields)
        spreadsheet_values["店铺名"] = task.store_name
        spreadsheet_values["采集时间"] = collected_at
        spreadsheet_row = [spreadsheet_values.get(field_name, "") for field_name in SHOPEE_TABLE_FIELD_ORDER]
        return [bitable_record], [spreadsheet_row]

    def _build_mercado_feishu_rows(
        self,
        task: StoreTask,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[list[Any]]]:
        """把美客多爬虫结果转换成一条严格匹配 32 字段表结构的记录。"""
        merged_fields: dict[str, Any] = {}
        collected_at: Any = ""
        for row_index, row in enumerate(rows, start=1):
            if not collected_at and row.get("采集时间"):
                collected_at = row["采集时间"]
            platform_fields = row.get("飞书字段", {})
            if not isinstance(platform_fields, dict):
                LOGGER.warning("[飞书][MKD打包] 第 %s 条爬虫结果的 飞书字段 不是字典，已跳过", row_index)
                continue
            for field_name, value in platform_fields.items():
                if value not in ("", None) or field_name not in merged_fields:
                    merged_fields[field_name] = value

        if not collected_at:
            collected_at = datetime.now(timezone.utc).isoformat()

        known_fields = set(MERCADO_TABLE_FIELD_ORDER)
        unknown_fields = sorted(set(merged_fields) - known_fields)
        if unknown_fields:
            LOGGER.warning("[飞书][MKD未知字段] 以下字段不在 32 字段定义中，不写入多维表：%s", unknown_fields)

        # 数字、货币和进度字段都不能发送空字符串；空指标直接从 JSON 中省略。
        bitable_record: dict[str, Any] = {
            "店铺名": task.store_name,
            "采集时间": self._to_feishu_timestamp_ms(collected_at),
        }
        for field_name in MERCADO_TABLE_FIELD_ORDER:
            if field_name in {"店铺名", "采集时间"}:
                continue
            value = merged_fields.get(field_name, "")
            if value not in ("", None):
                bitable_record[field_name] = value

        missing_fields = [
            field_name
            for field_name in MERCADO_TABLE_FIELD_ORDER
            if field_name not in {"店铺名", "采集时间"}
            and merged_fields.get(field_name, "") in ("", None)
        ]
        if missing_fields:
            LOGGER.warning("[飞书][MKD空字段] 以下字段本次无数据，不放入多维表请求体：%s", missing_fields)

        # 电子表固定追加 32 列，并保留 ISO 采集时间方便人工查看。
        spreadsheet_values = dict(merged_fields)
        spreadsheet_values["店铺名"] = task.store_name
        spreadsheet_values["采集时间"] = collected_at
        spreadsheet_row = [spreadsheet_values.get(field_name, "") for field_name in MERCADO_TABLE_FIELD_ORDER]
        return [bitable_record], [spreadsheet_row]

    @staticmethod
    def _to_feishu_timestamp_ms(value: Any) -> int:
        """把 ISO 日期、秒级时间戳或毫秒时间戳统一转换成飞书日期字段需要的毫秒整数。"""
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            number = float(value)
            return int(number if number >= 1_000_000_000_000 else number * 1000)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except (TypeError, ValueError, OSError):
            fallback = int(datetime.now(timezone.utc).timestamp() * 1000)
            LOGGER.warning("[飞书][日期转换失败] 原始值=%r，改用当前时间戳=%s", value, fallback)
            return fallback

    def _cleanup_retention(self) -> None:
        """清理三张数据多维表中的旧记录，历史电子表不删除。"""
        bitable = self.config.get("feishu", {}).get("bitable", {})
        fields = bitable.get("data_fields", {})
        retention = int(self.config.get("data", {}).get("retention_days", 90))
        for platform in ("tiktok", "shopee", "mercado"):
            app_token, table_id = self.feishu.get_bitable_ref("data", platform)
            if table_id:
                removed = self.feishu.remove_old_records(table_id, fields.get("collected_at", "采集时间"), retention, app_token=app_token)
                if removed:
                    LOGGER.info("平台 %s 清理旧数据 %s 条", platform, removed)

    @staticmethod
    def _format_rows(rows: list[dict[str, Any]]) -> str:
        """生成适合机器人阅读的短文本，避免把大段原始数据直接推送。"""
        if not rows:
            return "本次没有采集到数据。"
        if any(row.get("平台") == "tiktok" for row in rows):
            merged_fields: dict[str, Any] = {}
            for row in rows:
                platform_fields = row.get("飞书字段", {})
                if isinstance(platform_fields, dict):
                    for field_name, value in platform_fields.items():
                        if value not in ("", None):
                            merged_fields[field_name] = value
            ordered_fields = [field_name for field_name in TIKTOK_TABLE_FIELD_ORDER if field_name in merged_fields]
            # 多维表之外的新抓取字段也附在机器人消息末尾，避免调试时看不到数据。
            extra_fields = sorted(set(merged_fields) - set(TIKTOK_TABLE_FIELD_ORDER))
            lines = [f"{field_name}: {merged_fields[field_name]}" for field_name in ordered_fields + extra_fields]
            return "\n".join(lines) if lines else "本次未抓取到有效指标，空值不会写入飞书数值字段。"
        if any(row.get("平台") == "mercado" for row in rows):
            merged_fields: dict[str, Any] = {}
            for row in rows:
                platform_fields = row.get("飞书字段", {})
                if isinstance(platform_fields, dict):
                    for field_name, value in platform_fields.items():
                        if value not in ("", None):
                            merged_fields[field_name] = value
            lines: list[str] = []
            for field_name in MERCADO_TABLE_FIELD_ORDER:
                if field_name not in merged_fields:
                    continue
                value = merged_fields[field_name]
                if field_name in MERCADO_PROGRESS_FIELDS and isinstance(value, (int, float)):
                    lines.append(f"{field_name}: {value * 100:.1f}%")
                else:
                    lines.append(f"{field_name}: {value}")
            return "\n".join(lines) if lines else "本次未抓取到有效指标，空值不会写入飞书数值字段。"
        if any(row.get("平台") == "shopee" for row in rows):
            merged_fields: dict[str, Any] = {}
            for row in rows:
                metric_name = str(row.get("指标") or "").strip()
                value = row.get("显示值", row.get("数值", ""))
                if metric_name and value not in ("", None):
                    merged_fields[metric_name] = value
                platform_fields = row.get("飞书字段", {})
                if isinstance(platform_fields, dict):
                    for field_name, field_value in platform_fields.items():
                        if field_value not in ("", None):
                            merged_fields[field_name] = field_value
            lines = [
                f"{field_name}: {merged_fields[field_name]}"
                for field_name in SHOPEE_TABLE_FIELD_ORDER
                if field_name not in {"店铺名", "采集时间"} and field_name in merged_fields
            ]
            return "\n".join(lines) if lines else "本次未抓取到有效指标，空值不会写入飞书数值字段。"
        lines = []
        for row in rows[:20]:
            platform_fields = row.get("飞书字段", {})
            if isinstance(platform_fields, dict):
                for field_name, value in platform_fields.items():
                    if value != "":
                        lines.append(f"{field_name}: {value}")
                continue
            value = str(row.get("数值", ""))
            lines.append(f"{row.get('指标', '')}: {value}")
        if not lines:
            return "本次未抓取到有效指标，空值已写入飞书。"
        if len(rows) > 20:
            lines.append(f"其余 {len(rows) - 20} 条已写入飞书。")
        return "\n".join(lines)

"""每日店铺任务编排器。"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone
from typing import Any

from .config import StoreTask
from .feishu_client import FeishuClient
from .ziniao_client import ZiniaoClient, ZiniaoStoreSession

LOGGER = logging.getLogger(__name__)


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
        tasks = self.feishu.list_control_tasks()
        if not tasks:
            LOGGER.info("没有可执行店铺，可能是控制表为空、开关暂停或飞书尚未配置")
            return

        try:
            self._prepare_ziniao()
            for task in tasks:
                self._run_store(task)
            self._cleanup_retention()
        finally:
            self.ziniao.exit_client()

    def _prepare_ziniao(self) -> None:
        """启动紫鸟、更新内核并缓存全部店铺信息。"""
        self.ziniao.start_client()
        self.ziniao.update_core()
        self.browser_list = self.ziniao.list_browsers()

    def _run_store(self, task: StoreTask) -> None:
        """处理一间店铺，context manager 确保关闭后才会进入下一间。"""
        identifier = task.browser_oauth or task.browser_id or self._find_browser_identifier(task.store_name)
        if not identifier:
            LOGGER.error("找不到店铺 %s 对应的紫鸟 browserOauth/browserId，跳过", task.store_name)
            self._safe_notify(task.recipient, f"{task.store_name} 数据任务失败", "没有找到紫鸟店铺标识，请检查店铺名是否与紫鸟一致。")
            return

        try:
            with ZiniaoStoreSession(self.ziniao, identifier, task.store_name) as session:
                crawler = self._load_crawler(task.platform)
                rows = crawler.collect(task.store_name, session.download_path, session.opened.get("debuggingPort"))
                self._write_feishu(task, rows)
                self._safe_notify(task.recipient, f"{task.store_name} {task.platform} 广告数据", self._format_rows(rows))
        except Exception as exc:
            LOGGER.exception("店铺 %s 处理失败", task.store_name)
            self._safe_notify(task.recipient, f"{task.store_name} 数据任务失败", str(exc))

    def _safe_notify(self, recipient: str, title: str, content: str) -> None:
        """推送失败只记录日志，不影响关闭店铺和后续店铺。"""
        try:
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
        bitable = feishu_cfg.get("bitable", {})
        app_token, table_id = self.feishu.get_bitable_ref("data", task.platform)
        fields = bitable.get("data_fields", {})
        now = datetime.now(timezone.utc).isoformat()
        record_rows: list[dict[str, Any]] = []
        spreadsheet_rows: list[list[Any]] = []
        for row in rows:
            record = {
                fields.get("store_name", "店铺名"): row.get("店铺名", task.store_name),
                fields.get("collected_at", "采集时间"): row.get("采集时间", now),
                fields.get("metric", "指标"): row.get("指标", ""),
                fields.get("value", "数值"): row.get("数值", ""),
                fields.get("raw_data", "原始数据"): row.get("原始数据", ""),
            }
            # 平台可以提供自己的字段字典。美客多用它写入已建立的 28 个同名字段，
            # 其他平台没有该字段时仍沿用上面的通用结构。
            platform_fields = row.get("飞书字段", {})
            if isinstance(platform_fields, dict):
                record.update(platform_fields)
            record_rows.append(record)
            spreadsheet_row = [
                record.get(fields.get("store_name", "店铺名")),
                record.get(fields.get("collected_at", "采集时间")),
                task.platform,
                record.get(fields.get("metric", "指标")),
                record.get(fields.get("value", "数值")),
                record.get(fields.get("raw_data", "原始数据")),
            ]
            if isinstance(platform_fields, dict):
                spreadsheet_row.extend(platform_fields.values())
            spreadsheet_rows.append(spreadsheet_row)
        self.feishu.batch_create_records(table_id, record_rows, app_token=app_token)
        spreadsheet_cfg = feishu_cfg.get("spreadsheets", {}).get(task.platform, "")
        if isinstance(spreadsheet_cfg, dict):
            token = str(spreadsheet_cfg.get("token") or spreadsheet_cfg.get("spreadsheet_token") or "")
            sheet_id = str(spreadsheet_cfg.get("sheet_id") or "")
            range_name = str(spreadsheet_cfg.get("range") or (f"{sheet_id}!A:Z" if sheet_id else "Sheet1!A:Z"))
            self.feishu.append_spreadsheet_rows(token, spreadsheet_rows, range_name)
        else:
            self.feishu.append_spreadsheet_rows(str(spreadsheet_cfg), spreadsheet_rows)

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

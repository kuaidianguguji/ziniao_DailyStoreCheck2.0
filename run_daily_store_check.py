"""每日店铺检查程序入口。

正式运行：python run_daily_store_check.py
立即测试：python run_daily_store_check.py --run-now
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_store_check.config import PROJECT_ROOT, load_config
from daily_store_check.orchestrator import DailyStoreCheck


def configure_logging(config: dict) -> None:
    """同时输出控制台和滚动日志，便于无人值守排查。"""
    output_dir = PROJECT_ROOT / config.get("data", {}).get("output_dir", "data")
    output_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(output_dir / "daily_store_check.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[console, file_handler], force=True)


def wait_for_schedule(schedule: dict, run_now: bool = False) -> None:
    """等待到配置中的 HH:MM；--run-now 或 schedule.enabled=false 时立即返回。"""
    if run_now or not schedule.get("enabled", True):
        return
    timezone_name = schedule.get("timezone", "Asia/Shanghai")
    zone = ZoneInfo(timezone_name)
    hour, minute = [int(part) for part in str(schedule.get("run_time", "07:00")).split(":", 1)]
    now = datetime.now(zone)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    logging.info("下次执行时间: %s", target.isoformat())
    # 分段等待，系统时间调整或程序收到中断时可以及时响应。
    while True:
        remaining = (target - datetime.now(zone)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="紫鸟多平台每日广告数据采集")
    parser.add_argument("--config", default=None, help="config.yaml 路径")
    parser.add_argument("--run-now", action="store_true", help="忽略计划时间并立即执行一次")
    return parser.parse_args()


def main() -> int:
    """加载配置并按 run_once 决定执行一次或每天循环。"""
    args = parse_args()
    config = load_config(args.config)
    configure_logging(config)
    schedule = config.get("schedule", {})
    while True:
        wait_for_schedule(schedule, args.run_now)
        DailyStoreCheck(config).run_once()
        if schedule.get("run_once", True) or args.run_now:
            return 0
        args.run_now = False


if __name__ == "__main__":
    raise SystemExit(main())


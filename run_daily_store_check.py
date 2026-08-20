"""每日店铺检查程序入口。

正式运行（常驻后台，每天按配置时间执行）：python run_daily_store_check.py
立即执行第一轮并继续常驻：python run_daily_store_check.py --run-now
立即执行一轮后退出：python run_daily_store_check.py --run-now --once
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


def wait_for_schedule(schedule: dict, run_now: bool = False, force_next_day: bool = False) -> None:
    """等待到配置时间；立即运行时跳过等待，完成一轮后强制等待到第二天。"""
    if run_now:
        return
    if not schedule.get("enabled", True):
        raise ValueError("schedule.enabled=false 时没有每日执行时间；请启用计划，或使用 --run-now --once")
    timezone_name = schedule.get("timezone", "Asia/Shanghai")
    zone = ZoneInfo(timezone_name)
    hour, minute = [int(part) for part in str(schedule.get("run_time", "07:00")).split(":", 1)]
    now = datetime.now(zone)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # --run-now 可能在当天计划时间之前完成。此时也必须等到第二天，不能在当天再次执行。
    if force_next_day:
        target += timedelta(days=1)
    elif target <= now:
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
    parser.add_argument("--run-now", action="store_true", help="立即执行第一轮，结束后继续常驻等待第二天")
    parser.add_argument("--once", action="store_true", help="执行一轮后退出；与 --run-now 一起使用可立即执行一次")
    return parser.parse_args()


def main() -> int:
    """加载配置并常驻执行每日任务；只有 --once 或 run_once=true 才退出。"""
    args = parse_args()
    config = load_config(args.config)
    configure_logging(config)
    schedule = config.get("schedule", {})
    run_immediately = args.run_now
    completed_round = False
    while True:
        wait_for_schedule(
            schedule,
            run_now=run_immediately,
            force_next_day=completed_round,
        )
        try:
            DailyStoreCheck(config).run_once()
        except Exception:
            # 单日任务的未预期异常不能杀死常驻调度进程；记录完整堆栈后等待下一天。
            logging.getLogger(__name__).exception("本轮每日店铺检查发生未处理异常，程序继续常驻等待下一次执行")

        # 只有明确的一次性开关才允许退出；--run-now 仅控制第一轮是否跳过等待。
        if schedule.get("run_once", False) or args.once:
            return 0
        logging.info("本轮每日店铺检查结束，程序继续后台常驻，等待下一次执行时间")
        run_immediately = False
        completed_round = True


if __name__ == "__main__":
    raise SystemExit(main())

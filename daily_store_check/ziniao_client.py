"""紫鸟 WebDriver 客户端与单店铺上下文。

官方示例支持通过本地 HTTP IPC 控制紫鸟。本文件把全局变量改成实例属性，
并提供 context manager，保证每个店铺处理结束后一定关闭 driver 和店铺。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import requests
from selenium import webdriver
from selenium.common import NoSuchElementException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

LOGGER = logging.getLogger(__name__)


class ZiniaoClient:
    """紫鸟本地 IPC 接口封装。"""

    def __init__(self, config: dict[str, Any]):
        """读取紫鸟客户端、驱动目录、IPC 端口和企业登录信息。"""
        ziniao = config.get("ziniao", {})
        browser = ziniao.get("browser", {})
        user = ziniao.get("user_info", {})
        self.client_path = str(browser.get("client_path", ""))
        self.driver_folder = Path(browser.get("webdriver_path", "")) if browser.get("webdriver_path") else None
        self.socket_port = int(browser.get("socket_port", 16851))
        self.version = str(browser.get("version", "v6"))
        self.user_info = {"company": user.get("company", ""), "username": user.get("username", ""), "password": user.get("password", "")}
        self.system = platform.system()

    @property
    def ipc_url(self) -> str:
        """返回紫鸟本地 HTTP IPC 地址。"""
        return f"http://127.0.0.1:{self.socket_port}"

    def send_http(self, payload: dict[str, Any]) -> dict[str, Any]:
        """按官方示例方式序列化请求体并发送紫鸟 IPC 请求。"""
        response = requests.post(self.ipc_url, json.dumps(payload).encode("utf-8"), timeout=120)
        response.raise_for_status()
        return json.loads(response.text)

    def start_client(self) -> None:
        """启动紫鸟客户端；已启动时不重复启动。"""
        try:
            probe_payload = {"action": "getBrowserList", "requestId": str(uuid.uuid4()), **self.user_info}
            probe = requests.post(
                self.ipc_url,
                json.dumps(probe_payload).encode("utf-8"),
                timeout=2,
            )
            if probe.ok:
                LOGGER.info("紫鸟客户端已经在端口 %s 运行，直接复用", self.socket_port)
                return
        except requests.RequestException:
            pass
        if not self.client_path:
            raise RuntimeError("未配置 ziniao.browser.client_path")
        if self.system == "Windows":
            command = [self.client_path, "--run_type=web_driver", "--ipc_type=http", f"--port={self.socket_port}"]
        elif self.system == "Darwin":
            command = ["open", "-a", self.client_path, "--args", "--run_type=web_driver", "--ipc_type=http", f"--port={self.socket_port}"]
        elif self.system == "Linux":
            command = [self.client_path, "--no-sandbox", "--run_type=web_driver", "--ipc_type=http", f"--port={self.socket_port}"]
        else:
            raise RuntimeError(f"不支持的操作系统: {self.system}")
        try:
            subprocess.Popen(command)
            time.sleep(5)
        except OSError as exc:
            raise RuntimeError(f"启动紫鸟客户端失败: {exc}") from exc

    def update_core(self) -> None:
        """等待紫鸟内核更新完成。"""
        payload = {"action": "updateCore", "requestId": str(uuid.uuid4()), **self.user_info}
        for _ in range(60):
            try:
                result = self.send_http(payload)
            except requests.RequestException:
                time.sleep(2)
                continue
            code = result.get("statusCode")
            if code == 0:
                return
            if code == -10003:
                raise RuntimeError(f"紫鸟登录或版本不支持: {result}")
            time.sleep(2)
        raise TimeoutError("等待紫鸟内核更新超时")

    def list_browsers(self) -> list[dict[str, Any]]:
        """读取紫鸟店铺列表。"""
        result = self.send_http({"action": "getBrowserList", "requestId": str(uuid.uuid4()), **self.user_info})
        if str(result.get("statusCode")) != "0":
            raise RuntimeError(f"读取紫鸟店铺失败: {result}")
        return result.get("browserList", [])

    def open_store(
        self,
        store_info: str,
        isWebDriverReadOnlyMode: int = 0,
        isprivacy: int = 0,
        isHeadless: int = 0,
        cookieTypeSave: int = 0,
        jsInfo: Any = "",
    ) -> dict[str, Any]:
        """严格按官方 ``open_store`` 参数调用 ``startBrowser`` 打开店铺。"""
        store_info = str(store_info)
        payload: dict[str, Any] = {
            "action": "startBrowser",
            "isWaitPluginUpdate": 0,
            "isHeadless": isHeadless,
            "requestId": str(uuid.uuid4()),
            "isWebDriverReadOnlyMode": isWebDriverReadOnlyMode,
            "cookieTypeLoad": 0,
            "cookieTypeSave": cookieTypeSave,
            "runMode": "1",
            "isLoadUserPlugin": False,
            "pluginIdType": 1,
            "privacyMode": isprivacy,
            "notPromptForDownload": 1,
            **self.user_info,
        }
        if store_info.isdigit():
            payload["browserId"] = store_info
        else:
            payload["browserOauth"] = store_info
        if len(str(jsInfo)) > 2:
            payload["injectJsInfo"] = json.dumps(jsInfo)
        result = self.send_http(payload)
        status_code = str(result.get("statusCode"))
        if status_code == "0":
            return result
        if status_code == "-10003":
            raise RuntimeError(f"紫鸟登录失败或当前版本不支持 startBrowser: {json.dumps(result, ensure_ascii=False)}")
        raise RuntimeError(f"紫鸟 startBrowser 打开店铺失败: {json.dumps(result, ensure_ascii=False)}")

    def close_store(self, browser_oauth: str) -> None:
        """关闭当前店铺。"""
        result = self.send_http({"action": "stopBrowser", "requestId": str(uuid.uuid4()), "duplicate": 0, "browserOauth": browser_oauth, **self.user_info})
        if str(result.get("statusCode")) != "0":
            raise RuntimeError(f"关闭紫鸟店铺失败: {result}")

    def get_driver(self, opened: dict[str, Any]) -> webdriver.Chrome:
        """根据紫鸟返回的内核信息找到 webdriver 并连接调试端口。"""
        browser_path = str(opened.get("browserPath") or "")
        if browser_path.lower().endswith(("superbrowser.exe", "superbrowser")):
            browser_path = os.path.dirname(browser_path)
        candidate = Path(browser_path) / ("webdriver.exe" if self.system == "Windows" else "webdriver") if browser_path else None
        if not candidate or not candidate.exists():
            core_type = opened.get("core_type")
            core_version = str(opened.get("core_version", ""))
            if self.driver_folder and core_version and (core_type in (None, 0, "Chromium", "")):
                suffix = ".exe" if self.system == "Windows" else ""
                candidate = self.driver_folder / f"chromedriver{core_version.split('.')[0]}{suffix}"
        if not candidate or not candidate.exists():
            raise FileNotFoundError(f"找不到紫鸟 webdriver: {candidate}")
        options = webdriver.ChromeOptions()
        options.add_argument("--log-level=3")
        options.add_experimental_option("debuggerAddress", f"127.0.0.1:{opened.get('debuggingPort')}")
        return webdriver.Chrome(service=Service(str(candidate)), options=options)

    @staticmethod
    def open_ip_check(driver: webdriver.Chrome, ip_check_url: str) -> bool:
        """按官方示例打开紫鸟 IP 检测页并检查成功按钮。"""
        try:
            driver.get(ip_check_url)
            driver.find_element(By.XPATH, '//button[contains(@class, "styles_btn--success")]')
            return True
        except NoSuchElementException:
            LOGGER.error("紫鸟 IP 检测页未找到成功元素")
            return False
        except Exception:
            LOGGER.error("紫鸟 IP 检测异常: %s", traceback.format_exc())
            return False

    @staticmethod
    def open_launcher_page(driver: webdriver.Chrome, launcher_page: str) -> None:
        """按官方示例打开紫鸟返回的平台主页并等待页面稳定。"""
        if not launcher_page:
            raise RuntimeError("紫鸟 startBrowser 没有返回 launcherPage")
        driver.get(launcher_page)
        time.sleep(6)

    def exit_client(self) -> None:
        """任务全部结束后通知紫鸟客户端退出。"""
        try:
            self.send_http({"action": "exit", "requestId": str(uuid.uuid4()), **self.user_info})
        except Exception:
            LOGGER.warning("退出紫鸟客户端时发生异常: %s", traceback.format_exc())


class ZiniaoStoreSession:
    """单店铺会话；退出时先退出 driver，再调用 stopBrowser。"""

    def __init__(self, client: ZiniaoClient, identifier: str, store_name: str):
        """记录客户端、店铺标识和用于日志展示的店铺名。"""
        self.client = client
        self.identifier = identifier
        self.store_name = store_name
        self.opened: dict[str, Any] = {}
        self.driver: webdriver.Chrome | None = None
        self.browser_oauth = identifier

    def __enter__(self) -> "ZiniaoStoreSession":
        LOGGER.info("打开店铺: %s", self.store_name)
        self.opened = self.client.open_store(self.identifier)
        self.browser_oauth = str(self.opened.get("browserOauth") or self.opened.get("browserId") or self.identifier)
        try:
            self.driver = self.client.get_driver(self.opened)
            self.driver.implicitly_wait(60)
            ip_check_url = str(self.opened.get("ipDetectionPage") or "")
            if not ip_check_url:
                raise RuntimeError("紫鸟 startBrowser 没有返回 ipDetectionPage，请升级紫鸟客户端")
            if not self.client.open_ip_check(self.driver, ip_check_url):
                raise RuntimeError("紫鸟 IP 检测未通过")
            self.client.open_launcher_page(self.driver, str(self.opened.get("launcherPage") or ""))
            return self
        except Exception:
            # __enter__ 内抛错时 Python 不会调用 __exit__，这里必须主动关闭店铺。
            try:
                self.client.close_store(self.browser_oauth)
            except Exception:
                LOGGER.error("初始化 driver 失败后关闭店铺 %s 也失败: %s", self.store_name, traceback.format_exc())
            raise

    @property
    def download_path(self) -> str:
        """返回紫鸟为当前店铺分配的下载目录。"""
        return str(self.opened.get("downloadPath") or "")

    def __exit__(self, exc_type, exc_value, traceback_value) -> bool:
        """无论爬虫成功或失败，均关闭浏览器和紫鸟店铺。"""
        try:
            if self.driver:
                self.driver.quit()
        finally:
            try:
                self.client.close_store(self.browser_oauth)
            except Exception:
                LOGGER.error("关闭店铺 %s 失败: %s", self.store_name, traceback.format_exc())
        LOGGER.info("关闭店铺: %s", self.store_name)
        return False

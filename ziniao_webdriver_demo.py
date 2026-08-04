"""
# 适用环境python3
"""
import hashlib
import os
import shutil
import time
import traceback
import uuid
import json
import platform
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import requests
import subprocess

import yaml
from selenium import webdriver
from selenium.common import NoSuchElementException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

from amazon.business_reports_export import amazon_sales_export
from amazon.customer_voice_export import amazon_customer_voice_export
from amazon.feedback_export import amazon_feedback_export
from amazon.orders_export import amazon_order_all_export


func_dict = {
    "feedback_export": amazon_feedback_export,
    "business_report_export": amazon_sales_export,
    "orders_export": amazon_order_all_export,
    "customer_voice_export": amazon_customer_voice_export
}

def kill_process(version: Literal["v5", "v6"]):
    """
    终止紫鸟客户端已启动的进程
    :param version: 客户端版本
    """
    # 确认是否继续
    confirmation = input("在启动之前，需要先关闭紫鸟浏览器的主进程，确定要终止进程吗？(y/n): ")
    if confirmation.lower() == 'y':
        if is_windows:
            if version == "v5":
                process_name = 'SuperBrowser.exe'
            else:
                process_name = 'ziniao.exe'
            os.system('taskkill /f /t /im ' + process_name)
            time.sleep(3)
        elif is_mac:
            os.system('killall ziniao')
            time.sleep(3)
        elif is_linux:
            os.system('killall ziniaobrowser')
            time.sleep(3)
    else:
        exit()


def start_browser():
    """
    启动客户端
    :return:
    """
    try:
        if is_windows:
            cmd = [client_path, '--run_type=web_driver', '--ipc_type=http', '--port=' + str(socket_port)]
        elif is_mac:
            cmd = ['open', '-a', client_path, '--args', '--run_type=web_driver', '--ipc_type=http',
                   '--port=' + str(socket_port)]
        elif is_linux:
            cmd = [client_path, '--no-sandbox', '--run_type=web_driver', '--ipc_type=http', '--port=' + str(socket_port)]
        else:
            exit()
        subprocess.Popen(cmd)
        time.sleep(5)
    except Exception:
        print('start browser process failed: ' + traceback.format_exc())
        exit()


def update_core():
    """
    下载所有内核，打开店铺前调用，需客户端版本5.285.7以上
    因为http有超时时间，所以这个action适合循环调用，直到返回成功
    """
    data = {
        "action": "updateCore",
        "requestId": str(uuid.uuid4()),
    }
    data.update(user_info)
    while True:
        result = send_http(data)
        print(result)
        if result is None:
            print("等待客户端启动...")
            time.sleep(2)
            continue
        if result.get("statusCode") is None or result.get("statusCode") == -10003:
            print("当前版本不支持此接口，请升级客户端")
            return
        elif result.get("statusCode") == 0:
            print("更新内核完成")
            return
        else:
            print(f"等待更新内核: {json.dumps(result)}")
            time.sleep(2)


def send_http(data):
    """
    通讯方式
    :param data:
    :return:
    """
    try:
        url = 'http://127.0.0.1:{}'.format(socket_port)
        response = requests.post(url, json.dumps(data).encode('utf-8'), timeout=120)
        return json.loads(response.text)
    except Exception as err:
        print(err)


def delete_all_cache():
    """
    删除所有店铺缓存
    非必要的，如果店铺特别多、硬盘空间不够了才要删除
    """
    if not is_windows:
        return
    local_appdata = os.getenv('LOCALAPPDATA')
    cache_path = os.path.join(local_appdata, 'SuperBrowser')
    if os.path.exists(cache_path):
        shutil.rmtree(cache_path)


def delete_all_cache_with_path(path):
    """
    :param path: 启动客户端参数使用--enforce-cache-path时设置的缓存路径
    删除所有店铺缓存
    非必要的，如果店铺特别多、硬盘空间不够了才要删除
    """
    if not is_windows:
        return
    cache_path = os.path.join(path, 'SuperBrowser')
    if os.path.exists(cache_path):
        shutil.rmtree(cache_path)


def open_store(store_info, isWebDriverReadOnlyMode=0, isprivacy=0, isHeadless=0, cookieTypeSave=0, jsInfo=""):
    request_id = str(uuid.uuid4())
    data = {
        "action": "startBrowser"
        , "isWaitPluginUpdate": 0
        , "isHeadless": isHeadless
        , "requestId": request_id
        , "isWebDriverReadOnlyMode": isWebDriverReadOnlyMode
        , "cookieTypeLoad": 0
        , "cookieTypeSave": cookieTypeSave
        , "runMode": "1"
        , "isLoadUserPlugin": False
        , "pluginIdType": 1
        , "privacyMode": isprivacy
        , "notPromptForDownload": 1  # 1 不弹，0：弹  下载文件的时候是否弹窗提示选择文件保存路径
    }
    data.update(user_info)

    if store_info.isdigit():
        data["browserId"] = store_info
    else:
        data["browserOauth"] = store_info

    if len(str(jsInfo)) > 2:
        data["injectJsInfo"] = json.dumps(jsInfo)

    r = send_http(data)
    if str(r.get("statusCode")) == "0":
        return r
    elif str(r.get("statusCode")) == "-10003":
        print(f"login Err {json.dumps(r, ensure_ascii=False)}")
        exit()
    else:
        print(f"Fail {json.dumps(r, ensure_ascii=False)} ")
        exit()


def close_store(browser_oauth):
    request_id = str(uuid.uuid4())
    data = {
        "action": "stopBrowser"
        , "requestId": request_id
        , "duplicate": 0
        , "browserOauth": browser_oauth
    }
    data.update(user_info)

    r = send_http(data)
    if str(r.get("statusCode")) == "0":
        return r
    elif str(r.get("statusCode")) == "-10003":
        print(f"login Err {json.dumps(r, ensure_ascii=False)}")
        exit()
    else:
        print(f"Fail {json.dumps(r, ensure_ascii=False)} ")
        exit()


def get_browser_list() -> list:
    request_id = str(uuid.uuid4())
    data = {
        "action": "getBrowserList",
        "requestId": request_id
    }
    data.update(user_info)

    r = send_http(data)
    if str(r.get("statusCode")) == "0":
        print(r)
        return r.get("browserList")
    elif str(r.get("statusCode")) == "-10003":
        print(f"login Err {json.dumps(r, ensure_ascii=False)}")
        exit()
    else:
        print(f"Fail {json.dumps(r, ensure_ascii=False)} ")
        exit()


def get_driver(open_ret_json):
    browser_path = open_ret_json.get('browserPath')
    core_type = open_ret_json.get('core_type')
    chrome_driver_path = None
    # 检查店铺内核目录是否存在对应驱动程序
    driver_exist = False
    if browser_path:
        if browser_path.endswith('superbrowser.exe') or browser_path.endswith('superbrowser'):
            browser_path = os.path.dirname(browser_path)
        if is_windows:
            chrome_driver_path = os.path.join(browser_path, 'webdriver.exe')
        else:
            chrome_driver_path = os.path.join(browser_path, 'webdriver')
        driver_exist = os.path.exists(chrome_driver_path)
    # 如果不存在，根据内核版本匹配下载的驱动程序
    if not driver_exist and (core_type == 'Chromium' or core_type == 0):
        major = open_ret_json.get('core_version').split('.')[0]
        if is_windows:
            chrome_driver_path = os.path.join(driver_folder_path, 'chromedriver%s.exe') % major
        else:
            chrome_driver_path = os.path.join(driver_folder_path, 'chromedriver%s') % major
        driver_exist = os.path.exists(chrome_driver_path)
    print(f"chrome_driver_path: {chrome_driver_path}")
    if not driver_exist:
        print(f"驱动程序不存在: {chrome_driver_path}")
        return None
    port = open_ret_json.get('debuggingPort')
    options = webdriver.ChromeOptions()
    options.add_argument('--log-level=3')
    options.add_experimental_option("debuggerAddress", '127.0.0.1:' + str(port))
    return webdriver.Chrome(service=Service(chrome_driver_path), options=options)


def open_ip_check(driver, ip_check_url):
    """
    打开ip检测页检测ip是否正常
    :param driver: driver实例
    :param ip_check_url ip检测页地址
    :return 检测结果
    """
    try:
        driver.get(ip_check_url)
        driver.find_element(By.XPATH, '//button[contains(@class, "styles_btn--success")]')
        return True
    except NoSuchElementException:
        print("未找到ip检测成功元素")
        return False
    except Exception as e:
        print("ip检测异常:" + traceback.format_exc())
        return False


def open_launcher_page(driver, launcher_page):
    driver.get(launcher_page)
    time.sleep(6)


def get_exit():
    """
    关闭客户端
    :return:
    """
    data = {"action": "exit", "requestId": str(uuid.uuid4())}

    data.update(user_info)

    print('@@ get_exit...')
    send_http(data)


def encrypt_sha1(fpath: str) -> str:
    with open(fpath, 'rb') as f:
        return hashlib.new('sha1', f.read()).hexdigest()


def download_file(url, save_path):
    # 发送GET请求获取文件内容
    response = requests.get(url, stream=True)
    # 检查请求是否成功
    if response.status_code == 200:
        # 创建一个本地文件并写入下载的内容（如果文件已存在，将被覆盖）
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
        print(f"文件已成功下载并保存到：{save_path}")
    else:
        print(f"下载失败，响应状态码为：{response.status_code}")


def download_driver():
    if is_windows:
        config_url = "https://cdn-superbrowser-attachment.ziniao.com/webdriver/exe_32/config.json"
    elif is_mac:
        arch = platform.machine()
        if arch == 'x86_64':
            config_url = "https://cdn-superbrowser-attachment.ziniao.com/webdriver/mac/x64/config.json"
        elif arch == 'arm64':
            config_url = "https://cdn-superbrowser-attachment.ziniao.com/webdriver/mac/arm64/config.json"
        else:
            return
    else:
        return
    response = requests.get(config_url)
    # 检查请求是否成功
    if response.status_code == 200:
        # 获取文本内容
        txt_content = response.text
        config = json.loads(txt_content)
    else:
        print(f"下载驱动失败，状态码：{response.status_code}")
        exit()
    if not os.path.exists(driver_folder_path):
        os.makedirs(driver_folder_path)

    # 获取文件夹中所有chromedriver文件
    driver_list = [filename for filename in os.listdir(driver_folder_path) if filename.startswith('chromedriver')]

    for item in config:
        filename = item['name']
        if is_windows:
            filename = filename + ".exe"
        local_file_path = os.path.join(driver_folder_path, filename)
        if filename in driver_list:
            # 判断sha1是否一致
            file_sha1 = encrypt_sha1(local_file_path)
            if file_sha1 == item['sha1']:
                print(f"驱动{filename}已存在，sha1校验通过...")
            else:
                print(f"驱动{filename}的sha1不一致，重新下载...")
                download_file(item['url'], local_file_path)
                # mac首次下载修改文件权限
                if is_mac:
                    cmd = ['chmod', '+x', local_file_path]
                    subprocess.Popen(cmd)
        else:
            print(f"驱动{filename}不存在，开始下载...")
            download_file(item['url'], local_file_path)
            # mac首次下载修改文件权限
            if is_mac:
                cmd = ['chmod', '+x', local_file_path]
                subprocess.Popen(cmd)


def use_one_browser_run_task(browser):
    """
    打开一个店铺运行脚本
    :param browser: 店铺信息
    """
    # 如果要指定店铺ID, 获取方法:登录紫鸟客户端->账号管理->选择对应的店铺账号->点击"查看账号"进入账号详情页->账号名称后面的ID即为店铺ID
    store_id = browser.get('browserOauth')
    store_name = browser.get("browserName")
    # 打开店铺
    print(f"=====打开店铺：{store_name}=====")
    ret_json = open_store(store_id)
    print(ret_json)
    store_id = ret_json.get("browserOauth")
    if store_id is None:
        store_id = ret_json.get("browserId")
    # 使用驱动实例开启会话
    driver = get_driver(ret_json)
    if driver is None:
        print(f"=====驱动获取失败，关闭店铺：{store_name}=====")
        close_store(store_id)
        return

    # 获取ip检测页地址
    ip_check_url = ret_json.get("ipDetectionPage")
    if not ip_check_url:
        print("ip检测页地址为空，请升级紫鸟浏览器到最新版")
        driver.quit()
        print(f"=====关闭店铺：{store_name}=====")
        close_store(store_id)
        exit()
    # 执行脚本
    try:
        # 设置元素查找等待时间-全局隐式等待
        driver.implicitly_wait(60)
        ip_usable = open_ip_check(driver, ip_check_url)
        if ip_usable:
            print("ip检测通过，打开店铺平台主页")
            open_launcher_page(driver, ret_json.get("launcherPage"))
            # 打开店铺平台主页后进行后续自动化操作
            print(f"=====进行脚本执行：{store_name}=====")
            download_path = ret_json.get("downloadPath")
            if not download_path.endswith('/') and not download_path.endswith('\\'):
                download_path = download_path + '\\'
            for task_name in task_list:
                func = func_dict.get(task_name)
                if func:
                    print(f"开始执行任务：{task_name}")
                    func(driver, store_name, download_path)
                    print(f"任务：{task_name}执行完毕")
            print(f"=====脚本全部执行完毕：{store_name}=====")
        else:
            print("ip检测不通过，请检查")
    except:
        print("脚本运行异常:" + traceback.format_exc())
    finally:
        driver.quit()
        print(f"=====关闭店铺：{store_name}=====")
        close_store(store_id)


def use_all_browser_run_task(browser_list):
    """
    循环打开所有店铺运行脚本
    :param browser_list: 店铺列表
    """
    for browser in browser_list:
        use_one_browser_run_task(browser)


def use_all_browser_run_task_with_thread_pool(browser_list, max_threads=3):
    """
    使用线程池控制最大并发线程数
    :param browser_list: 店铺列表
    :param max_threads: 最大并发线程数
    """
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        executor.map(use_one_browser_run_task, browser_list)


if __name__ == "__main__":
    """ 需要从系统角标将紫鸟浏览器完全退出后再运行"""
    is_windows = platform.system() == 'Windows'
    is_mac = platform.system() == 'Darwin'
    is_linux = platform.system() == 'Linux'

    with open('./config/config.yaml', 'r', encoding='utf-8') as config:
        cfg = yaml.safe_load(config)

    browser_config = cfg['ziniao']['browser']
    user_config = cfg['ziniao']['user_info']
    # 任务列表（亚马逊）
    task_list = cfg['task']['amazon']

    driver_folder_path = browser_config['webdriver_path']  # 驱动文件保存路径
    client_path = browser_config['client_path']  # 浏览器客户端路径
    socket_port = browser_config['socket_port']  # 系统未被占用的端口

    # 配置文件获取用户登录信息
    user_info = {
        "company": user_config['company'],
        "username": user_config['username'],
        "password": user_config['password']
    }

    """  
    windows用，V5版本
    有店铺运行的时候，会删除失败
    删除所有店铺缓存，非必要的，如果店铺特别多、硬盘空间不够了才要删除
    delete_all_cache()

    启动客户端参数使用--enforce-cache-path时用这个方法删除，传入设置的缓存路径删除缓存
    delete_all_cache_with_path(path)
    """

    '''下载各个版本的webdriver驱动'''
    download_driver()

    # 终止紫鸟客户端已启动的进程
    # 从配置文件读取，v5与v6的进程名不同，按版本修改v5或v6
    kill_process(version=browser_config['version'])

    print("=====启动客户端=====")
    start_browser()
    print("=====更新内核=====")
    update_core()

    """获取店铺列表"""
    print("=====获取店铺列表=====")
    browser_list = get_browser_list()
    if not browser_list:
        print("browser list is empty")
        exit()

    # 过滤指定店铺运行脚本(例如美国亚马逊：siteId=1)
    amazon_store = [browser for browser in browser_list if str(browser.get('siteId')) == '1']
    """打开第一个店铺运行脚本"""
    # use_one_browser_run_task(amazon_store[0])

    """循环打开所有店铺运行脚本"""
    # use_all_browser_run_task(browser_list)

    """多线程并发打开所有店铺运行脚本，max_threads设置最大线程数"""
    # 任务线程数
    thread_num = cfg['task']['thread_num']
    use_all_browser_run_task_with_thread_pool(amazon_store, max_threads=thread_num)

    """关闭客户端"""
    get_exit()


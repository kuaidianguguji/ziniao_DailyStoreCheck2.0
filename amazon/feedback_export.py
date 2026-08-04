import os
import platform
import subprocess
import time
from datetime import date

from selenium.webdriver.common.by import By

from util.csv_util import create_csv, append_csv
from util.webdriver_util import WebdriverUtil


def amazon_feedback_export(driver, store_name: str, download_path: str):
    webdriver_util = WebdriverUtil(driver)
    webdriver_util.go_to('https://sellercentral.amazon.com/home')
    # continue_button = webdriver_util.find_element_wait(by=By.XPATH, value='//input[@id="continue"]')
    # webdriver_util.move_to_click(continue_button)
    # sign_in_submit = webdriver_util.find_element_wait(by=By.XPATH, value='//input[@id="signInSubmit"]')
    # webdriver_util.move_to_click(sign_in_submit)
    # 判断验证码已填写
    # time.sleep(1)
    # 登录
    # auth_signin_button = webdriver_util.find_element_wait(by=By.XPATH, value='//input[@id="auth-signin-button"]')
    # webdriver_util.move_to_click(auth_signin_button)

    memu_ele = webdriver_util.find_element_wait(by=By.XPATH, value='//*[@data-test-tag="nav-button-container"]/div')
    webdriver_util.move_to_click(memu_ele)
    time.sleep(1)
    performance_memu = webdriver_util.find_element_wait(by=By.XPATH, value='//div[@data-menu-id="performance"]')
    webdriver_util.move_to_click(performance_memu)
    feedback_ele = webdriver_util.find_element_wait(by=By.XPATH, value='//a[@data-menu-id="feedback-manager"]')
    webdriver_util.move_to_click(feedback_ele)

    time.sleep(1)
    feedback_summary = webdriver_util.find_element_wait(by=By.XPATH, value='//feedback-summary')
    table_head = feedback_summary.find_element(By.TAG_NAME, 'kat-table-head')
    table_head_cells = table_head.find_elements(By.TAG_NAME, 'kat-table-cell')

    form_header = []
    for cell in table_head_cells:
        form_header.append(cell.text)
    # print(f"表头数据:{form_header}")
    current_date = date.today()
    current_date_str = current_date.strftime("%Y%m%d")
    save_file_name = download_path + f"{store_name}_feedback_summary_{current_date_str}.csv"
    create_csv(save_file_name, form_header)

    table_body = feedback_summary.find_element(By.TAG_NAME, 'kat-table-body')
    table_body_rows = table_body.find_elements(By.TAG_NAME, 'kat-table-row')
    for row in table_body_rows:
        row_cells = row.find_elements(By.TAG_NAME, 'kat-table-cell')
        content_list = []
        for cell in row_cells:
            # print(cell.text)
            content_list.append(cell.text)
        print(f"行数据:{content_list}")
        append_csv(save_file_name, content_list)
    print(f"导出文件：{save_file_name}")

    # 打开文件
    # try:
    #     if platform.system() == "Windows":
    #         os.startfile(save_file_name)
    #     elif platform.system() == "Darwin":  # macOS
    #         subprocess.Popen(["open", save_file_name])
    #     elif platform.system() == "Linux":
    #         subprocess.Popen(["xdg-open", save_file_name])
    #     else:
    #         print("不支持的操作系统")
    # except FileNotFoundError:
    #     print(f"文件 {save_file_name} 未找到")
    # except Exception as e:
    #     print(f"打开文件时发生错误: {e}")


import time

from selenium.webdriver.common.by import By

from util.webdriver_util import WebdriverUtil


def amazon_sales_export(driver, store_name: str, download_path: str):
    amazon_sales_dashboard_export(driver, store_name, download_path)
    amazon_business_report_export(driver, store_name, download_path)
    time.sleep(5)


def amazon_sales_dashboard_export(driver, store_name: str, download_path: str):
    webdriver_util = WebdriverUtil(driver)
    webdriver_util.go_to('https://sellercentral.amazon.com/business-reports')

    download_button = (webdriver_util.find_elements_wait(By.CLASS_NAME, 'css-1dzeh5m')[1]
                       .find_element(By.TAG_NAME, 'kat-button'))
    webdriver_util.move_to_click(download_button)
    time.sleep(3)
    print(f"{store_name}销售控制面板报表下载完成，目录:{download_path}")


def amazon_business_report_export(driver, store_name: str, download_path: str):
    webdriver_util = WebdriverUtil(driver)
    webdriver_util.go_to('https://sellercentral.amazon.com/business-reports#/report?id=102:SalesTrafficTimeSeries')

    kat_button = webdriver_util.find_element_wait(By.XPATH, '//kat-button[@data-testid="BR_TABLE_BAR_DOWNLOAD_BUTTON"]')
    kat_button_shadow_root = kat_button.shadow_root
    download_button = (kat_button_shadow_root.find_element(By.CSS_SELECTOR, 'div[class="icon__container"]')
                       .find_element(By.XPATH, ".."))
    webdriver_util.move_to_click(download_button)

    print(f'{store_name}销售和流量业务报表下载完成，目录:{download_path}')
    time.sleep(3)
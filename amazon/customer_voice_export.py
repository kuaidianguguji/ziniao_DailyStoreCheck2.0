import traceback
from datetime import date

from selenium.webdriver.common.by import By

from util.csv_util import create_csv, append_csv
from util.webdriver_util import WebdriverUtil


def amazon_customer_voice_export(driver, store_name: str, download_path: str):
    webdriver_util = WebdriverUtil(driver)
    # 打开“买家之声”
    webdriver_util.go_to('https://sellercentral.amazon.com/voice-of-the-customer')

    # 表头
    table_header = ['商品名称', 'ASIN', 'SKU', '状况', '订单配送方', '买家不满意率', '买家不满意订单', '订单总数', '星级评定',
                    '退货率', '造成负面买家体验的主要原因', '上次更新时间', '买家满意度状况', '已显示退货标记']

    current_date = date.today()
    current_date_str = current_date.strftime("%Y%m%d")
    save_file_name = download_path + f"{store_name}_customer_voice_{current_date_str}.csv"
    create_csv(save_file_name, table_header)

    page = 1
    while True:
        print(f"正在处理第{page}页...")
        customer_voice_page_export(webdriver_util, save_file_name)
        # 获取下一页
        try:
            pagination_ele = webdriver_util.find_element_wait(By.TAG_NAME, 'kat-pagination')
            page_shadow_root = pagination_ele.shadow_root
            next_page = page_shadow_root.find_element(By.CSS_SELECTOR, 'span[class="nav item"]')
        except Exception as e:
            print("下一页获取异常，导出结束" + traceback.format_exc())
            break
        if next_page and next_page.get_attribute('part') == 'pagination-nav-right':
            webdriver_util.move_to_click(next_page)
            page = page + 1
        else:
            print(f"{store_name}导出买家评价结束，导出文件：{save_file_name}")
            break

def customer_voice_page_export(webdriver_util, save_file_name):
    tabel = webdriver_util.find_element_wait(By.ID, 'listing-table')
    # 获取商品数据
    table_body = tabel.find_element(By.TAG_NAME, 'tbody')
    rows = table_body.find_elements(By.TAG_NAME, 'tr')
    for row in rows:
        cols = row.find_elements(By.TAG_NAME, 'td')
        content_list = []
        for index, col in enumerate(cols):
            if index == 0 or index == 13:
                continue
            elif index == 1:
                content_list.append(col.find_element(By.XPATH, ".//div/a").text)
                # 获取整个文本并分割
                full_text = col.text
                parts = full_text.split('\n')
                br_text = parts[1] if len(parts) > 1 else ""
                content_list.append(br_text)
            elif index == 2:
                parts = col.text.split('\n')
                br_text = parts[1] if len(parts) > 1 else ""
                content_list.append(parts[0])
                content_list.append(br_text)
            else:
                content_list.append(col.text)
        append_csv(save_file_name, content_list)

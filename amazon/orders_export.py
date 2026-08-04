import time
import traceback
from datetime import date

from selenium.webdriver.common.by import By

from util.csv_util import create_csv, append_csv
from util.webdriver_util import WebdriverUtil


def amazon_order_all_export(driver, store_name: str, download_path: str):
    webdriver_util = WebdriverUtil(driver)
    # 点击“管理订单”
    # webdriver_util.find_element_wait(By.XPATH, '//a[@data-page-id="seller-your-account"]').click()
    # 等待中订单、未发货订单、已取消订单、已发货订单
    order_type_list = ['pending', 'unshipped', 'canceled', 'shipped']
    for order_type in order_type_list:
        amazon_order_export(webdriver_util, order_type, store_name, download_path)


def amazon_order_export(webdriver_util: WebdriverUtil, order_type: str, store_name: str, download_path: str):
    print(f"正在处理{order_type}订单...")
    webdriver_util.go_to(f'https://sellercentral.amazon.com/orders-v3/mfn/{order_type}')

    # 获取表头
    if order_type == 'pending':
        form_header = ['订单距今时间', '订单日期', '订单时间', '订单号', '配送渠道', '销售渠道', '订单标签',
                       '商品名称', 'ASIN', 'SKU', '数量', '商品小计', '订单类型', '订单状态', '等待原因']
    elif order_type == 'canceled':
        form_header = ['订单距今时间', '订单日期', '订单时间', '订单号', '配送渠道', '销售渠道', '订单标签',
                       '商品名称', 'ASIN', 'SKU', '商品小计', '订单类型', '订单状态', '取消原因']
    else:
        form_header = ['订单距今时间', '订单日期', '订单时间', '订单号', '买家姓名', '配送渠道', '销售渠道', '订单标签',
                       '商品名称', 'ASIN', 'SKU', '数量', '商品小计', '订单类型', '配送时间', '送达时间', '订单状态']

    current_date = date.today()
    current_date_str = current_date.strftime("%Y%m%d")
    save_file_name = download_path + f"{store_name}_order_{order_type}_export_{current_date_str}.csv"
    create_csv(save_file_name, form_header)

    page = 1
    while True:
        print(f"正在处理第{page}页...")
        time.sleep(1)
        amazon_order_page_export(webdriver_util, save_file_name, order_type)
        # 获取下一页
        try:
            pagination_ele = webdriver_util.find_element_wait(By.CLASS_NAME, 'pagination-controls', 5)
            next_page = pagination_ele.find_element(By.XPATH, './/ul/li[2]')
        except Exception as e:
            print("下一页获取异常，导出结束" + traceback.format_exc())
            break
        if next_page and 'disabled' not in next_page.get_attribute('class'):
            webdriver_util.move_to_click(next_page.find_element(By.XPATH, './/a'))
            time.sleep(5)
            page = page + 1
        else:
            print(f"{order_type}订单导出结束，导出文件：{save_file_name}")
            break


def amazon_order_page_export(webdriver_util: WebdriverUtil, save_file_name: str, order_type: str):
    try:
        orders_tabel = webdriver_util.find_element_wait(By.ID, 'orders-table')
    except Exception as e:
        print(f"{order_type}订单未获取到数据")
        return
    # 获取订单数据
    table_body = orders_tabel.find_element(By.TAG_NAME, 'tbody')
    rows = table_body.find_elements(By.TAG_NAME, 'tr')
    for row in rows:
        cols = [col for col in row.find_elements(By.TAG_NAME, 'td') if col.get_attribute('class') != "hidden-table-cell"]
        # 如果列数只有2，则为上一行订单的多个商品
        if len(cols) == 2:
            for index, col in enumerate(cols):
                if index == 0:
                    continue
                product_info = col.find_elements(By.XPATH, './/div[@class="myo-list-orders-product-name-cell"]/div')
                if order_type == 'pending' or order_type == 'canceled':
                    content_index = 7
                else:
                    content_index = 8
                for info_index, product in enumerate(product_info):
                    if info_index == 0:
                        content_list[content_index] = product.text
                    else:
                        parts = product.text.split(": ")
                        content = parts[1] if len(parts) > 1 else product.text
                        content_list[content_index] = content
                    content_index = content_index + 1
            append_csv(save_file_name, content_list)
            continue
        # 获取订单数据
        content_list = []
        for index, col in enumerate(cols):
            if index == 0 or index == 3 or index > 6:
                continue
            elif index == 2:
                # 获取订单详情
                full_text = col.text
                texts = full_text.split('\n')
                order_tag = []
                for text_index, text in enumerate(texts):
                    if order_type == 'unshipped' or order_type == 'shipped':
                        if text_index == 1:  # 跳过“买家姓名“标题
                            continue
                        if text_index >= 5:
                            order_tag.append(text)
                            continue
                    else:
                        if text_index >= 3:
                            order_tag.append(text)
                            continue
                    parts = text.split(": ")
                    content = parts[1] if len(parts) > 1 else text
                    content_list.append(content)
                order_tag = ", ".join(order_tag)
                content_list.append(order_tag)
            elif index == 4:
                # 获取商品信息
                product_info = col.find_elements(By.XPATH, './/div[@class="myo-list-orders-product-name-cell"]/div')
                for info_index, product in enumerate(product_info):
                    if info_index == 0:
                        content_list.append(product.text)
                    else:
                        parts = product.text.split(": ")
                        content = parts[1] if len(parts) > 1 else product.text
                        content_list.append(content)
            elif index == 5:
                # 获取订单类型
                try:
                    table_order_type = webdriver_util.find_element_wait_under_element(col, By.XPATH, ".//div/div[1]", timeout=1).text
                    content_list.append(table_order_type)
                except Exception:
                    content_list.append('')
                try:
                    delivery_times = webdriver_util.find_element_wait_under_element(col, By.XPATH, ".//div/div[2]", timeout=1).text
                    delivery_time_list = delivery_times.split('\n')
                except Exception:
                    continue
                    # delivery_time_list = ['', '']
                for delivery_time in delivery_time_list:
                    parts = delivery_time.split(": ")
                    content = parts[1] if len(parts) > 1 else delivery_time
                    content_list.append(content)
            else:
                full_text = col.text
                texts = full_text.split('\n')
                for text in texts:
                    parts = text.split(": ")
                    content = parts[1] if len(parts) > 1 else text
                    content_list.append(content)
        if content_list:
            append_csv(save_file_name, content_list)

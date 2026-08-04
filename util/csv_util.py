# -*- coding: utf-8 -*-
import csv
import logging
import threading
import time

lock = threading.Lock()

def create_csv(file_name: str, form_header: list):
    """
    创建csv文件
    :return:
    """
    if not file_name.endswith(".csv"):
        file_name = file_name + ".csv"
    logging.info('创建csv文件:' + file_name)
    with open(file_name, 'w', newline='') as f:
        csv_write = csv.writer(f)
        csv_write.writerow(form_header)



def append_csv(file_name, content_list: list):
    with lock:
        print("添加csv记录....\n" + str(content_list))
        start_time = time.time()
        if not file_name.endswith(".csv"):
            file_name = file_name + ".csv"
        with open(file_name, 'a+', newline='') as f:
            csv_write = csv.writer(f)
            csv_write.writerow(content_list)
        end_time = time.time()
        print("添加csv时间：", '{:.5f}'.format(end_time-start_time))


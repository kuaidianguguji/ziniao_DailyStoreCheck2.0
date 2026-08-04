import logging
import time
import traceback
from typing import List, Literal

from selenium.common import StaleElementReferenceException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait

from config import config


class WebdriverUtil(object):

    def __init__(self, driver):
        self.driver = driver
        self.close_all_tab()
        self.driver.set_page_load_timeout(config.get_page_load_timeout())
        self.driver.set_script_timeout(config.get_page_load_timeout())

    def go_to(self, url, timeout=30):
        """
        打开指定url，设置指定超时时间，打开后设置回原来的超时时间
        :param url: 目标地址
        :param timeout: 超时时间
        :return:
        """
        implicit_wait = self.driver.timeouts.implicit_wait
        self.driver.implicitly_wait(timeout)
        self.driver.get(url)
        self.driver.implicitly_wait(implicit_wait)

    def close_all_tab(self) -> None:
        """
        关闭所有标签页，只保留一个
        """
        handle_list = self.driver.window_handles
        window_handles = []
        for handle in handle_list:
            self.driver.switch_to.window(handle)
            if not self.driver.current_url.startswith("devtools://"):
                window_handles.append(handle)

        handle_len = len(window_handles)
        self.driver.switch_to.window(window_handles[0])
        if handle_len > 1:
            for i in range(handle_len):
                self.driver.switch_to.window(window_handles[i])
                if i != handle_len - 1:
                    self.driver.close()

    def switch_tab(self, origin_handle=None, origin_url=None) -> str:
        if not origin_handle:
            origin_handle = self.driver.current_window_handle
        if not origin_url:
            origin_url = self.driver.current_url
        handles = self.driver.window_handles
        for item in handles:
            self.driver.switch_to.window(item)
            try:
                current_url = self.driver.current_url
            except:
                logging.info("切换标签页加载超时...")
                self.driver.close()
                self.driver.switch_to.window(origin_handle)
                return origin_handle
            if current_url != origin_url:
                break
        return origin_handle

    def find_element(self, by=By.ID, value=None) -> WebElement:
        return self.driver.find_element(by, value)

    def find_element_wait_under_element(self, element: WebElement, by=By.ID, value=None, timeout=30) -> WebElement:
        implicit_wait = self.driver.timeouts.implicit_wait
        self.driver.implicitly_wait(0)
        element = WebDriverWait(element, timeout, 0.5).until(
            expected_conditions.presence_of_element_located((by, value)))
        self.driver.implicitly_wait(implicit_wait)
        return element

    def find_element_wait(self, by=By.ID, value=None, timeout=30) -> WebElement:
        implicit_wait = self.driver.timeouts.implicit_wait
        self.driver.implicitly_wait(0)
        try:
            element = WebDriverWait(self.driver, timeout, 0.5).until(
                expected_conditions.presence_of_element_located((by, value)))
        except StaleElementReferenceException as e:
            print('StaleElementReferenceException异常，重新获取元素')
            element = WebDriverWait(self.driver, timeout, 0.5).until(
                expected_conditions.presence_of_element_located((by, value)))
        self.driver.implicitly_wait(implicit_wait)
        return element

    def find_element_wait_not(self, by=By.ID, value=None, timeout=30) -> WebElement | Literal[True]:
        implicit_wait = self.driver.timeouts.implicit_wait
        self.driver.implicitly_wait(0)
        element = WebDriverWait(self.driver, timeout, 0.5).until_not(
            expected_conditions.presence_of_element_located((by, value)))
        self.driver.implicitly_wait(implicit_wait)
        return element

    def find_elements(self, by=By.ID, value=None) -> List[WebElement]:
        return self.driver.find_elements(by, value)

    def find_elements_wait(self, by=By.ID, value=None, timeout=30) -> List[WebElement]:
        implicit_wait = self.driver.timeouts.implicit_wait
        self.driver.implicitly_wait(0)
        elements = WebDriverWait(self.driver, timeout, 0.5).until(
            expected_conditions.presence_of_all_elements_located((by, value)))
        self.driver.implicitly_wait(implicit_wait)
        return elements

    @staticmethod
    def input_verbatim(element: WebElement, data: str):
        for character in list(data):
            element.send_keys(character)

    def move_to_by_js(self, element):
        self.driver.execute_script("arguments[0].scrollIntoView();", element)

    def move_to_click(self, element, move=True) -> None:
        """
        移动到指定元素点击
        :param element: 元素
        :param move: 是否先移动鼠标到元素上
        :return:
        """
        enable_check_count = 0
        while True:
            if enable_check_count == 60:
                break
            enabled = element.is_displayed()
            if enabled:
                break
            time.sleep(1)
            enable_check_count = enable_check_count + 1
        if move:
            ActionChains(self.driver).move_to_element(element).perform()
        self.driver.execute_script('arguments[0].click();', element)

    def move_to_send_key(self, input_element, input_value: str, move=True, init_input=False) -> None:
        """
        移动到指定元素点击，并输入内容
        :param input_element: 元素
        :param input_value: 输入的内容
        :param move: 是否移动鼠标至元素
        :param init_input: 是否初始化输入框空字符串（比特保存账号密码时会导致填充无法覆盖，需要输入空字符后再清空）
        :return:
        """
        self.move_to_click(input_element, move=move)
        try:
            # input_element.click()
            if init_input:
                self.input_verbatim(input_element, " ")
            input_element.clear()
            self.input_verbatim(input_element, input_value)
        except Exception as e:
            # traceback.print_exc()
            logging.error(traceback.format_exc())
            self.driver.execute_script('arguments[0].click();', input_element)
            input_element.clear()
            self.input_verbatim(input_element, input_value)

    def quit(self) -> None:
        """
        关闭浏览器
        """
        self.driver.quit()



class GlobalVar:

    env_args = []

    # 窗口大小
    window_height = 1200
    window_width = 1600

    screenshot_fail = 0
    # 页面加载超时时间
    page_load_timeout = 180


def get_env_args():
    return str(GlobalVar.env_args)


def get_env_args_list():
    return GlobalVar.env_args


def get_screenshot_fail():
    return GlobalVar.screenshot_fail


def get_page_load_timeout():
    return GlobalVar.page_load_timeout


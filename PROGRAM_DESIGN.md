# 紫鸟每日店铺检查程序设计

## 1. 目标与边界

程序每天在配置时间执行一次，从飞书多维表“控制台”读取店铺名、推送人员、开关和平台。开关为空或暂停时不处理；其余店铺严格串行执行：打开一间紫鸟店铺、运行对应平台广告爬虫、写入飞书、推送结果、关闭店铺，关闭完成后才处理下一间。

短期多维表 `TK_数据`、`SP_数据`、`MKD_数据` 只保留最近 `retention_days` 天，默认 90 天。三个飞书电子表只追加，不删除，作为完整历史记录。

## 2. 目录职责

```text
ziniao_DailyStoreCheck_codex_two/
├─ config/config.example.yaml          # 可提交的配置模板，不含真实凭据
├─ config/config.yaml                  # 本机运行配置，已被 Git 忽略
├─ daily_store_check/
│  ├─ config.py                        # 配置加载、平台名标准化、开关判断、StoreTask
│  ├─ feishu_client.py                 # 飞书 token、多维表、电子表、机器人
│  ├─ ziniao_client.py                 # 紫鸟 IPC、店铺开关、WebDriver 会话
│  └─ orchestrator.py                  # 串行业务流程和平台爬虫注册
├─ tiktok/TK_auto.py                   # TikTok 独立自动化和爬虫
├─ shopee/SP_auto.py                   # Shopee 独立自动化和爬虫
├─ mercado/MKD_auto.py                 # 美客多独立自动化和爬虫
├─ run_daily_store_check.py            # 生产入口和每日时间调度
├─ requirements-daily.txt              # 本项目新增依赖
└─ ziniao_webdriver_demo.py             # 官方示例，保留作对照
```

## 3. 核心执行流程

1. `run_daily_store_check.main` 读取 YAML，并调用 `wait_for_schedule` 等待计划时间。
2. `DailyStoreCheck.run_once` 调用 `FeishuClient.list_control_tasks`，过滤空开关、暂停和未知平台。
3. `_prepare_ziniao` 启动紫鸟客户端、更新内核、读取完整店铺列表。
4. `_run_store` 用控制表店铺名精确匹配紫鸟 `browserName`，得到 `browserOauth` 或 `browserId`。
5. `ZiniaoStoreSession` 严格调用官方 `startBrowser` 打开一间店铺，连接 WebDriver，完成 `ipDetectionPage` 检测后打开 `launcherPage`；`with` 结束时一定先 `driver.quit`，再调用紫鸟 `stopBrowser`。
6. `_load_crawler` 根据配置动态载入 `TK_auto.py`、`SP_auto.py` 或 `MKD_auto.py`。
7. 爬虫优先用 DrissionPage 连接紫鸟的 `debuggingPort`，输出统一结构。
8. `_write_feishu` 写入对应数据多维表，并追加对应历史电子表。
9. `_safe_notify` 根据“推送人员”的 `open_id` 使用应用机器人定向推送；没有人员 ID 时可退回 webhook。
10. 所有店铺完成后，`_cleanup_retention` 清理三张短期多维表中的过期数据，并退出紫鸟客户端。

## 4. 文件、函数和关键变量

### `config/config.example.yaml` 与本机 `config/config.yaml`

首次部署时复制示例文件为 `config/config.yaml`，再填写本机路径和凭据。真实配置已被 Git 忽略。

- `schedule.run_time`：每日执行时间，格式 `HH:MM`。
- `schedule.timezone`：时区，默认 `Asia/Shanghai`。
- `schedule.run_once`：`true` 表示执行后退出，适合 Windows 任务计划程序；`false` 表示进程常驻并每天循环。
- `feishu.app_id/app_secret`：飞书自建应用凭据，也可用环境变量覆盖。
- `feishu.bitable.control`：控制台多维表的 `app_token + table_id`。
- `feishu.bitable.data_tables`：三个短期多维表分别填写自己的 `app_token + table_id`。
- `feishu.bitable.default_app_token`：如果四张多维表属于同一个应用，可以填公共 token，子项只填 `table_id`。
- `spreadsheets`：三个历史电子表分别填写 `token + sheet_id`；也可以额外指定完整 `range`。
- `retention_days`：短期多维表保留天数。
- `platforms.*.crawler`：平台到 Python 爬虫类的映射，格式 `模块:类名`。

### `daily_store_check/config.py`

- `load_config`：读取 YAML，并允许 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`ZINIAO_*` 环境变量覆盖敏感字段。
- `normalise_platform`：兼容 `TK`、`SP`、`MKD`、`meicado`、`mercado` 等写法。
- `is_enabled_switch`：只有明确的开启值才执行；暂停、空值和其他未知值都不执行。
- `StoreTask`：控制表记录的内部数据类，关键变量为 `store_name`、`recipient`、`platform`。

### `daily_store_check/feishu_client.py`

- `_get_tenant_token`：获取并缓存租户 token。
- `list_records`：处理多维表分页。
- `list_control_tasks`：读取并过滤控制表，人员字段优先解析成 `open_id`。
- `batch_create_records`：每 500 条分批写入数据多维表。
- `remove_old_records`：按采集时间计算 90 天截止线并批量删除过期记录。
- `append_spreadsheet_rows`：向历史电子表追加二维数组。
- `send_robot_message`：优先应用机器人按 `receive_id_type` 定向发送；webhook 只作为固定群机器人兜底。
- `configured`：凭据是否齐全；不齐全时读写接口进入 dry-run 并记录日志。

### `daily_store_check/ziniao_client.py`

- `send_http`：官方本地 HTTP IPC 通道。
- `start_client`：按 Windows/macOS/Linux 拼接紫鸟启动参数。
- `update_core`：轮询内核更新状态，最多等待约 120 秒。
- `list_browsers`：读取紫鸟店铺列表。
- `open_store/close_store`：完整复用官方 `startBrowser/stopBrowser` 请求字段，一对一控制店铺生命周期。
- `open_ip_check/open_launcher_page`：复用官方示例的 IP 检测页和平台主页启动步骤。
- `get_driver`：优先使用紫鸟内核自带 webdriver，找不到时按内核主版本查找下载目录。
- `ZiniaoStoreSession`：关键安全边界；任何成功、异常或爬虫失败都执行关闭动作。
- `browser_oauth`：当前已打开店铺的真实紫鸟标识。
- `opened`：紫鸟返回的调试端口、下载目录、启动页等会话数据。

### 三个平台独立文件

- `TK_auto.py`、`SP_auto.py`、`MKD_auto.py` 各自维护 DrissionPage 连接、标签页获取、页面操作、数据提取和结果组装。
- 三个平台故意不共享爬虫基类；即使逻辑重复，也保留在各自文件中，方便后续独立修改。
- 每个文件使用 `Chromium(紫鸟调试端口)` 接管浏览器，再通过 `browser.latest_tab` 获取当前标签页。
- 平台文件不调用 `tab.get()`，只接管紫鸟已经打开的当前页面。
- 三个平台当前选择器是骨架示例。登录真实后台后，应以账号当前 DOM 为准替换选择器和指标。

### `daily_store_check/orchestrator.py`

- `run_once`：一轮完整任务，唯一店铺循环位于这里，未使用线程池。
- `_find_browser_identifier`：控制表店铺名与紫鸟店铺名大小写无关的精确匹配。
- `_load_crawler`：动态加载平台类，新增平台时不需要修改循环逻辑。
- `_write_feishu`：一份标准爬虫结果同时转换为多维表记录和电子表行。
- `_cleanup_retention`：只清理短期多维表。
- `_safe_notify`：消息推送失败不会阻断下一个店铺。

### `run_daily_store_check.py`

- `configure_logging`：控制台和 5 MB 滚动文件日志，保留 5 个备份。
- `wait_for_schedule`：按配置时区等待每日时间，每 60 秒重新计算一次。
- `--run-now`：开发阶段立即执行，不等待计划时间。

## 5. 飞书准备清单

### 推荐配置关系

```yaml
feishu:
  app_id: "飞书自建应用 App ID"
  app_secret: "飞书自建应用 App Secret"
  bitable:
    control:
      app_token: "控制台多维表所属应用 token"
      table_id: "控制台数据表 table_id"
    data_tables:
      tiktok:
        app_token: "TK_数据多维表所属应用 token"
        table_id: "TK_数据数据表 table_id"
  spreadsheets:
    tiktok:
      token: "TK 历史电子表 spreadsheet token"
      sheet_id: "TK 历史电子表内工作表 ID"
```

如果四张多维表在同一个多维表应用中，可把公共 token 填入 `bitable.default_app_token`，每个子表只填写自己的 `table_id`。三张电子表仍然分别填写各自的 `token` 和 `sheet_id`。

1. 给自建应用开放多维表读写、电子表读写和机器人发消息权限，并发布应用版本。
2. 把应用机器人加入需要发送消息的可见范围。
3. 控制台“推送人员”必须使用飞书“人员”字段。接口会从人员字段返回值中读取 `open_id`/`user_id`，不会把姓名文本猜成用户 ID。
4. 控制台店铺名必须与紫鸟 `browserName` 完全一致。
5. 三张短期表至少建立：店铺名、采集时间、指标、数值、原始数据。
6. 采集时间字段可使用日期字段或文本字段；若使用日期字段，应把写入值改成毫秒时间戳。
7. 电子表第一行建议固定为：店铺名、采集时间、平台、指标、数值、原始数据。

## 6. 启动方式

```powershell
pip install -r requirements-daily.txt
Copy-Item config/config.example.yaml config/config.yaml
python run_daily_store_check.py --run-now
python run_daily_store_check.py
```

无人值守运行推荐让 Windows 任务计划程序每天 07:00 启动，并把 `schedule.enabled` 设为 `false`；程序内置时间等待适合临时常驻方案。两者只选一种，避免重复执行。

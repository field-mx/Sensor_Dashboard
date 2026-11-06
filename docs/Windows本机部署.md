# Windows本机部署

## 安装

交付给Windows 10/11的64位电脑；当前使用CPU模式，预测和训练均不依赖学校服务器或GPU。

1. 将完整交付文件夹解压到普通本地目录，建议避开同步盘。保留`vendor`、`configs`、`sample_data`和`wheelhouse`。
2. 安装64位Python 3.11并保留Python Launcher（`py`）。双击`Setup.cmd`创建软件自己的`.venv`，不修改系统或原工程环境。
3. 安装完成后双击`StartDemo.cmd`。首次模型加载需要几秒；失败会保留控制台提示，日志见下文。

交付包内有`wheelhouse`时，安装使用随包固定依赖，无需联网下载依赖；Python本体需事先安装。没有`wheelhouse`时，安装脚本使用PyPI及PyTorch官方CPU源。固定版本见`requirements-lock.txt`。
参考：[Python Windows安装说明](https://docs.python.org/3.11/using/windows.html)、[PyTorch本地安装](https://pytorch.org/get-started/locally/)。

已有Python路径但没有`py`时，在软件目录运行：

```powershell
& '你的Python3.11路径/python.exe' -m demo_tools.setup_environment
```

## 启动、停止和检查

以下命令都在本软件根目录运行，不需要激活虚拟环境。

```powershell
# 一键启动后台并打开界面
.\.venv\Scripts\python.exe -m sensor_demo.launcher launch
# 仅后台，包含预测API和每日调度
.\.venv\Scripts\python.exe -m sensor_demo.launcher backend
# 仅界面
.\.venv\Scripts\python.exe -m sensor_demo.launcher frontend
# 后台状态 / 正常停止
.\.venv\Scripts\python.exe -m sensor_demo.launcher status
.\.venv\Scripts\python.exe -m sensor_demo.launcher stop
# 固定依赖、源数据、公开权重和真实模型检查
.\.venv\Scripts\python.exe -m demo_tools.doctor --model
```

`StartBackend.cmd`、`StartFrontend.cmd`和`StopBackend.cmd`分别提供对应入口。需要调试日志时可前台运行：

```powershell
.\.venv\Scripts\python.exe -m demo_server --port 18000
.\.venv\Scripts\python.exe -m sensor_demo
```

训练和验证进程由后台按任务自动启动，无需用户输入内部worker命令。关闭界面不会停止后台或训练。正在训练/验证时停止后台会明确拒绝，先等待完成或在界面取消训练。

## 地址、配置与文件

| 项目 | 默认值 |
|---|---|
| API | `http://127.0.0.1:18000`，仅本机HTTP，不对局域网开放 |
| API说明 | `http://127.0.0.1:18000/docs` |
| 展示文件 | 本软件`sample_data/SensorData.xlsx` |
| 运行目录 | `%LOCALAPPDATA%\SensorDashboardDemo\runtime` |
| 客户端设置 | 运行目录`client.json`；示例`configs/client.example.json` |
| 数据和任务 | 运行目录`training/demo.sqlite3`、`training/datasets`、`training/jobs` |
| 活动模型 | 运行目录`training/active-model.json`，候选使用独立权重 |
| 日志 | 运行目录`logs`和`client-events.jsonl` |
| CSV默认导出位置 | 运行目录`exports/demo_runs`，也可在保存对话框自行选择 |

可以在界面修改本机后台地址；关闭模型更新窗口再重新打开后使用新地址。需要改端口时，先停止旧后台，修改`client.json`的`api_url`，然后重新启动一键入口。端口占用、其他交付目录的后台或模型文件不完整时，会明确报错。

验收或隔离运行可在启动前设置`SENSOR_DEMO_DATA_DIR`为独立目录；前后端必须使用相同值。默认用户目录已经避开源代码同步目录。不要让同步软件回写运行数据库、环境或Git目录。

## 每日任务

默认关闭，Asia/Shanghai时区，02:00执行；设置精确到秒，便于现场临近时间验收。只用已校验、已声明正常且未处理的采样内容/时间区间。重复回放、改名上传都不算新增。

后台运行时，前端可以退出；电脑休眠、关机或后台停止时无法执行。允许最多1秒的正常调度抖动，超过1秒视为错过并持久记录，不自动补训。重启后保留开关和记录；当天计划已错过会显示“错过，未补训”，可手动“立即检查并后训练”。不安装Windows开机任务、不要求管理员权限。自动训练生成候选，需验证并手动激活。

## 数据与模型契约

样例为24,234个连续1秒采样点、61测点。严格按公开schema名称和顺序提取56模型通道，保留原始单位；不在外层重复标准化。预测输入`[96,56]`，输出未来连续96秒`[96,56]`；另外5测点仅观测。

训练分区按时间60%/20%/20%，训练/验证分区至少223点，评估分区至少256点以容纳96秒历史和160秒固定故障回归，窗口不跨区。配方最多5轮，batch4，学习率1e-5，按预声明预算使用64/32/32窗口，新建优化器，保留物理图和原归一化参数。真实loss和耗时写入任务记录。训练失败保留活动模型；产生的候选可能未通过验证。

PRCTF固定提交`9b9b19401d0e87fe8b0c21c27abe3368ecc73122`，工业权重SHA256为`a07d8df103c32adacf297908dc3df14d261357cc73faa18c560bf4586f5b079e`；CTF上游固定`b1c100effb9c27cd25de07e91de7f9005d18d5e7`。完整发布清单、来源与第三方说明保留在`vendor/PhysicsRelationCTF`。随包源码及依赖的许可证状态按各自原始说明保留。

## 常见问题

- “后台不可达”：先运行`StartBackend.cmd`，检查地址及`logs/demo_server-*.log`。
- “预热不足”：先播放96个源时间点，可用10×加速；5个仅观测通道不会生成预测。
- “重复/区间重叠”：选择真正不重叠的新数据；再次手动演示训练可在后训练任务页选择原已登记数据，不会冒充自动新增。
- “训练失败/验证未通过”：查看界面原因，原活动模型保留；修复数据或环境后手动重试。
- 三维页只提供结构示意及可确认测点，不把未标定位置伪装成真实安装坐标。

开发检查命令为`.\.venv\Scripts\python.exe -m pytest -q`。实际桌面验收记录随完整离线交付包提供；公开源码仓库不包含私有运行记录。

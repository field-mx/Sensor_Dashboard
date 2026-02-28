# 电解制备氢气监控系统

> **Electrolysis H₂ Production Monitoring Dashboard**  
> 基于 PyQt6 + pyqtgraph 的工业级实时监控界面，适用于质子交换膜（PEM）或碱性水电解制氢设备的现场数据可视化。

---

## 功能概览

| 模块 | 功能 |
|---|---|
| **三维场数据视图** | 温度场、氢气浓度场、氧气浓度场、压力场的实时 3D 散点云图，支持拖拽旋转、悬停预览、点击固定历史折线图卡 |
| **槽电压监控** | 16 通道实时电压数值显示（0–30 V，精度 0.001 V），支持单通道展开查看近 1 分钟历史波形 |
| **历史趋势弹窗** | 点击三维传感器点弹出非模态历史趋势图，含 5 分钟数据、统计摘要（最小/最大/均值/标准差） |
| **后端数据调度** | `DataManager` 集中管理数据源，支持模拟数据与真实硬件无缝切换，缓存最近 5 分钟历史数据 |

---

## 项目结构

```
Sensor_Dashboard/
├── main.py                  # 应用入口，初始化 Qt 应用、主窗口、模拟数据推送
│
├── backend/
│   ├── mock_generator.py    # 模拟数据生成线程（QThread）
│   └── data_manager.py      # 核心数据调度类（QObject），含历史缓存与查询接口
│
├── frontend/
│   ├── main_window.py       # 主窗口：左侧菜单 + 右侧页面堆栈
│   ├── view_3d.py           # 三维场数据散点云图页面（含悬停卡、固定历史图卡、色条）
│   ├── view_voltage.py      # 槽电压监控页面（16 通道折叠卡片 + 波形图）
│   ├── history_dialog.py    # 历史趋势弹窗（非模态 QDialog）
│   └── styles.py            # 全局深色工业风主题、颜色常量、渐变工具函数
│
└── requirements.txt
```

---

## 环境要求

| 依赖 | 版本 |
|---|---|
| Python | ≥ 3.10 |
| PyQt6 | ≥ 6.4 |
| pyqtgraph | ≥ 0.13 |
| numpy | ≥ 1.24 |
| PyOpenGL | ≥ 3.1 |

---

## 快速开始

```bash
# 1. 克隆仓库
git clone <repo-url>
cd Sensor_Dashboard

# 2. 创建虚拟环境（推荐 conda）
conda create -n sensorDashboard python=3.11
conda activate sensorDashboard

# 3. 安装依赖
pip install -r requirements.txt

# 4. 运行
python main.py
```

---

## 数据格式说明

### 场数据（`field_data_ready` 信号）

```python
{
    "timestamp": datetime,
    "fields": {
        "temperature":      [{"x": int, "y": int, "z": int, "sensor_id": str, "value": float}, ...],
        "h2_concentration": [...],  # 体积分数，中心值约 0.03
        "o2_concentration": [...],  # 体积分数，中心值约 0.20
        "pressure":         [...],  # kPa，中心值约 101.3
    }
}
```

- **传感器 ID 格式**：`S_XXYYZZ`，例如坐标 (1, 2, 0) → `S_010200`
- **网格尺寸**：默认 4×4×3 = 48 个传感器点（可在 `mock_generator.py` 中调整 `GRID_X/Y/Z`）

### 槽电压数据（`voltage_data_ready` 信号）

```python
{
    "timestamp": datetime,
    "channels": {
        "CH01": float,   # 0.0 – 30.0 V，精确到 0.001 V
        "CH02": float,
        ...
        "CH16": float,
    }
}
```

---

## 后端接口

### DataManager 关键方法

```python
from backend.data_manager import DataManager

dm = DataManager()
dm.start_mock_source()          # 启动模拟数据源
dm.field_data_updated.connect(your_slot)    # 订阅实时场数据
dm.voltage_data_updated.connect(your_slot)  # 订阅实时电压数据

# 查询历史（按 sensor_id）
result = dm.query_field_history(
    sensor_id="S_010200",
    field_name="temperature",   # temperature | h2_concentration | o2_concentration | pressure
    duration_sec=300            # 最近 N 秒，默认 300（5 分钟）
)
# result: {"timestamps": [...], "values": [...], "sensor_id": "S_010200", "field_name": "temperature"}

# 查询历史（按坐标）
result = dm.query_field_history(x=1, y=2, z=0, field_name="pressure")

# 查询电压历史
result = dm.query_voltage_history(channel="CH01", duration_sec=60)

# 接入真实硬件（替换数据源，无需修改其他代码）
dm.set_hardware_source(your_hardware_object)  # 需具备 field_data_ready 和 voltage_data_ready 信号
```

### 接入真实硬件

`DataManager.set_hardware_source()` 接受任意具备以下两个 `pyqtSignal` 的 `QObject`：

- `field_data_ready(dict)` —— 格式同上方场数据结构
- `voltage_data_ready(dict)` —— 格式同上方电压数据结构

切换数据源时无需修改调度逻辑和 UI 代码：

```python
dm.stop()                          # 停止模拟源
dm.set_hardware_source(hw_driver)  # 无缝切换
```

---

## 界面交互说明

### 三维视图
- **拖拽**：旋转三维场景
- **鼠标悬停**靠近传感器点（屏幕距离 < 12 px）：弹出轻量浮动信息卡（传感器 ID、当前值、坐标）
- **左键单击**传感器点：固定一张历史折线图卡，跟随旋转移动；再次单击同一点取消
- **右侧色条**：实时显示场值范围（上=最大值，下=最小值）

### 槽电压页面
- **折叠态**：紧凑显示通道名 + 实时数值，颜色随电压报警阈值变化（> 25 V 变红）
- **展开态**：显示近 60 秒滚动波形图
- **全部展开/折叠**按钮：一键操作所有通道

---

## 配置参数

主要可配置常量集中在以下文件：

| 文件 | 常量 | 说明 |
|---|---|---|
| `mock_generator.py` | `EMIT_INTERVAL_MS` | 数据发射间隔（ms），默认 1000 |
| `mock_generator.py` | `NUM_CHANNELS` | 槽电压通道数，默认 16 |
| `mock_generator.py` | `GRID_X/Y/Z` | 三维传感器网格尺寸，默认 4×4×3 |
| `mock_generator.py` | `FIELD_CONFIGS` | 各场物理量中心值与波动幅度 |
| `data_manager.py` | `HISTORY_DURATION_SEC` | 历史缓存时长（秒），默认 300 |
| `view_voltage.py` | `NUM_CHANNELS` | 界面显示通道数，默认 16 |

---

## 开发说明

- **纯信号/槽通信**：后端与前端之间完全通过 `pyqtSignal` 解耦，后端不含任何 UI 代码
- **线程安全**：`DataManager` 的历史缓存读写均受 `threading.Lock` 保护
- **历史容量**：使用 `collections.deque(maxlen=N)` 自动淘汰旧帧，不会内存溢出
- **GL 覆盖问题**：悬浮信息卡挂载于普通 `QWidget` 层而非 OpenGL 控件，避免被 GL 渲染覆盖

---

## License

本项目为上海电气联合研究项目内部原型，未经许可不得对外分发。

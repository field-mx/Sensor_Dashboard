"""
styles.py
深色工业风全局样式表 & 颜色主题常量
"""
import numpy as np

# ── 颜色常量 ────────────────────────────────────────────────────────────────
BG_DEEP      = "#0d1117"   # 最深背景
BG_SIDEBAR   = "#161b22"   # 侧边栏背景
BG_CARD      = "#1c2128"   # 卡片/面板背景
BORDER_COLOR = "#30363d"   # 边框/网格线
ACCENT_BLUE  = "#58a6ff"   # 主强调色（蓝）
ACCENT_GREEN = "#3fb950"   # 次强调色（绿）
ACCENT_AMBER = "#d29922"   # 警告色（琥珀）
ACCENT_RED   = "#f85149"   # 报警色
TEXT_PRIMARY = "#e6edf3"   # 主文字
TEXT_MUTED   = "#8b949e"   # 次要文字

# 16 个通道配色（槽电压波形，可扩展）
CHANNEL_COLORS = [
    "#58a6ff",  # CH01 蓝
    "#3fb950",  # CH02 绿
    "#d29922",  # CH03 琥珀
    "#f85149",  # CH04 红
    "#bc8cff",  # CH05 紫
    "#39d353",  # CH06 亮绿
    "#ff7b72",  # CH07 橙红
    "#79c0ff",  # CH08 浅蓝
    "#ffa657",  # CH09 橙
    "#56d364",  # CH10 芽绿
    "#e3b341",  # CH11 金黄
    "#f0883e",  # CH12 深橙
    "#a5d6ff",  # CH13 天蓝
    "#d2a8ff",  # CH14 淡紫
    "#7ee787",  # CH15 薄荷绿
    "#ff9a8b",  # CH16 珊瑚红
]

# ── 全局 QSS ────────────────────────────────────────────────────────────────
DARK_STYLE = f"""
/* ---- 全局 ---- */
QMainWindow, QDialog, QWidget {{
    background-color: {BG_DEEP};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
}}

/* ---- 侧边栏 ---- */
#sidebar {{
    background-color: {BG_SIDEBAR};
    border-right: 1px solid {BORDER_COLOR};
}}

#sidebar_title {{
    color: {TEXT_PRIMARY};
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 2px;
    padding: 8px 4px 4px 12px;
    color: {TEXT_MUTED};
}}

/* ---- 菜单按钮 ---- */
#menu_btn {{
    background-color: transparent;
    color: {TEXT_MUTED};
    border: none;
    border-left: 3px solid transparent;
    padding: 14px 8px 14px 18px;
    text-align: left;
    font-size: 13px;
    font-weight: 500;
}}
#menu_btn:hover {{
    background-color: rgba(88, 166, 255, 0.08);
    color: {TEXT_PRIMARY};
    border-left: 3px solid rgba(88, 166, 255, 0.4);
}}
#menu_btn[active="true"] {{
    background-color: rgba(88, 166, 255, 0.15);
    color: {ACCENT_BLUE};
    border-left: 3px solid {ACCENT_BLUE};
    font-weight: 700;
}}

/* ---- 页面头部 ---- */
#page_header {{
    background-color: {BG_CARD};
    border-bottom: 1px solid {BORDER_COLOR};
    padding: 10px 20px;
}}

#page_title {{
    font-size: 16px;
    font-weight: 700;
    color: {TEXT_PRIMARY};
    letter-spacing: 1px;
}}

#page_subtitle {{
    font-size: 11px;
    color: {TEXT_MUTED};
}}

/* ---- 统计标签 ---- */
#stat_card {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER_COLOR};
    border-radius: 6px;
    padding: 8px 14px;
}}
#stat_value {{
    font-size: 18px;
    font-weight: 700;
    color: {ACCENT_BLUE};
}}
#stat_label {{
    font-size: 10px;
    color: {TEXT_MUTED};
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* ---- 弹窗 ---- */
QDialog {{
    background-color: {BG_CARD};
    border: 1px solid {BORDER_COLOR};
    border-radius: 8px;
}}

/* ---- 状态栏 ---- */
QStatusBar {{
    background-color: {BG_SIDEBAR};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER_COLOR};
    font-size: 11px;
}}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{
    background: {BG_DEEP};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_COLOR};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_MUTED};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* ---- 分隔线 ---- */
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {BORDER_COLOR};
}}

/* ---- 关闭按钮等 ---- */
QPushButton#close_btn {{
    background-color: rgba(248, 81, 73, 0.12);
    color: {ACCENT_RED};
    border: 1px solid rgba(248, 81, 73, 0.3);
    border-radius: 4px;
    padding: 6px 16px;
}}
QPushButton#close_btn:hover {{
    background-color: rgba(248, 81, 73, 0.25);
}}
"""


# ── Colormap 工具 ────────────────────────────────────────────────────────────
def scalar_to_rgba(values: np.ndarray, alpha: float = 0.9) -> np.ndarray:
    """
    将标量数组归一化后映射到 viridis-like RGBA 颜色数组。
    返回 shape (N, 4) 的 float32 数组，值域 [0, 1]。
    """
    v = np.asarray(values, dtype=float)
    vmin, vmax = v.min(), v.max()
    if vmax == vmin:
        t = np.zeros_like(v)
    else:
        t = (v - vmin) / (vmax - vmin)   # [0, 1]

    # 自定义 viridis-like colormap: 蓝→青→绿→黄→红
    # 控制点 RGB（0~1）
    ctrl = np.array([
        [0.267, 0.005, 0.329],   # 深紫蓝  t=0
        [0.190, 0.408, 0.663],   # 深蓝    t=0.25
        [0.128, 0.566, 0.551],   # 青绿    t=0.5
        [0.478, 0.757, 0.297],   # 浅绿黄  t=0.75
        [0.993, 0.906, 0.144],   # 黄      t=1.0
    ], dtype=np.float32)
    ctrl_t = np.linspace(0, 1, len(ctrl))

    r = np.interp(t, ctrl_t, ctrl[:, 0]).astype(np.float32)
    g = np.interp(t, ctrl_t, ctrl[:, 1]).astype(np.float32)
    b = np.interp(t, ctrl_t, ctrl[:, 2]).astype(np.float32)
    a = np.full_like(r, alpha)

    return np.stack([r, g, b, a], axis=1)


def hex_to_gl(hex_color: str, alpha: float = 1.0):
    """十六进制颜色字符串转 OpenGL (r, g, b, a) tuple，值域 [0,1]."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))
    return (r, g, b, alpha)

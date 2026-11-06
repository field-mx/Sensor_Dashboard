"""Offline branding and a legend outside the plot's graphics scene."""
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame,QHBoxLayout,QLabel,QVBoxLayout,QWidget

APP_NAME='电解槽AI监控系统'
ASSETS=Path(__file__).parent/'assets'

def brand_image(name,filename,width,height,background='white'):
    pixmap=QPixmap(str(ASSETS/filename))
    if pixmap.isNull():raise RuntimeError(f'品牌图像加载失败：{filename}，请保留完整软件文件夹。')
    label=QLabel();label.setAccessibleName(name);label.setToolTip(name)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter);label.setFixedHeight(height+16)
    label.setPixmap(pixmap.scaled(width,height,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
    label.setStyleSheet(f'background:{background};border:0;border-radius:5px;padding:8px;')
    return label

def create_brand_header():
    header=QFrame();header.setObjectName('brandHeader');header.setStyleSheet('#brandHeader {background:white;border:0;}')
    layout=QVBoxLayout(header);layout.setContentsMargins(2,12,2,12);layout.setSpacing(7)
    title=QLabel(APP_NAME);title.setObjectName('appBrandTitle');title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title.setStyleSheet('font-size:18px;color:#0969da;background:white;padding:0;')
    layout.addWidget(title)
    layout.addWidget(brand_image('上海电气 Logo','shanghai_electric.png',162,42))
    layout.addWidget(brand_image('清华大学 Logo','tsinghua.png',162,52,'#660874'))
    return header

def create_forecast_legend():
    legend=QWidget();legend.setObjectName('forecastLegend');legend.setAccessibleName('预测曲线图例（绘图区外）')
    legend.setStyleSheet('#forecastLegend {background:white;border:1px solid #d0d7de;border-radius:4px;}')
    layout=QHBoxLayout(legend);layout.setContentsMargins(12,5,12,5);layout.setSpacing(24)
    for text,color,line in [('观测','#0969da','solid'),('已到达时刻的预测','#1a7f37','solid'),('未来96秒预测','#bf8700','dashed')]:
        entry=QHBoxLayout();entry.setSpacing(7)
        swatch=QFrame();swatch.setFixedSize(30,3);swatch.setStyleSheet(f'background:transparent;border:0;border-top:2px {line} {color};')
        label=QLabel(text);label.setStyleSheet('background:white;border:0;')
        entry.addWidget(swatch);entry.addWidget(label);layout.addLayout(entry)
    layout.addStretch()
    return legend

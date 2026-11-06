"""Reuse the original electrolyzer box scene; no invented sensor positions."""
import numpy as np
import pyqtgraph.opengl as gl
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QHeaderView
from .geometry import _box_mesh

class TwinPanel(QWidget):
    selected_tag=pyqtSignal(str)
    def __init__(self,parent=None):
        super().__init__(parent);layout=QVBoxLayout(self)
        self.caption=QLabel('电解槽结构示意 · 测点未定位 · 可旋转、缩放查看');layout.addWidget(self.caption)
        row=QHBoxLayout();layout.addLayout(row,1)
        self.view=gl.GLViewWidget();self.view.setBackgroundColor('#f0f2f5');self.view.setCameraPosition(distance=140,elevation=25,azimuth=-35);row.addWidget(self.view,2)
        self.values_table=QTableWidget(0,3);self.values_table.setHorizontalHeaderLabels(['测点','当前观测','状态 / 位置']);self.values_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.values_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);self.values_table.horizontalHeader().setStretchLastSection(True);row.addWidget(self.values_table,1)
        self.values_table.cellClicked.connect(lambda r,c:self.selected_tag.emit(self.values_table.item(r,0).text()))
        self.frame_label=QLabel('等待真实数据；不显示启动假数值。');layout.addWidget(self.frame_label)
        grid=gl.GLGridItem();grid.setSize(100,60);grid.setSpacing(10,10);grid.setColor((.7,.73,.77,.36));self.view.addItem(grid)
        for center,size,color in [((0,0,8),(76,28,18),(.72,.78,.84,.52)),((0,0,21),(70,22,8),(.38,.52,.68,.42)),((-42,0,8),(6,32,22),(.16,.32,.52,.7)),((42,0,8),(6,32,22),(.16,.32,.52,.7))]:self.add_box(center,size,color)
        for x in np.linspace(-28,28,8):self.add_box((float(x),0,8),(1.6,29,20),(.12,.24,.36,.36))
        for y,color in [(-23,(.10,.44,.22,.7)),(23,(.65,.18,.18,.7))]:self.view.addItem(gl.GLLinePlotItem(pos=np.array([[-42,y,14],[42,y,14]],float),color=color,width=5,antialias=True))
    def add_box(self,center,size,color):
        vertices,faces=_box_mesh(center,size)
        item=gl.GLMeshItem(vertexes=vertices,faces=faces,faceColors=np.tile(color,(len(faces),1)),smooth=False,drawEdges=True,edgeColor=(.1,.17,.24,.35));item.setGLOptions('translucent');self.view.addItem(item)
    def clear(self):self.values_table.setRowCount(0);self.frame_label.setText('等待真实数据；测点位置未标定。')
    def update_frame(self,data,observed,engine,seq):
        tags=['SCRDLFK','SCRDYFK','avg','max','AT010301','AT010302','TT010301','TT010302','TT010303','PT010303','PT010304','FT010401']
        self.values_table.setRowCount(len(tags))
        for row,tag in enumerate(tags):
            i=data.channels.index(tag);state='未纳入模型'
            if engine and tag in engine.channels:state={'normal':'正常','warning':'待确认','alarm':'预警','unavailable':'不可用'}[engine.statuses[engine.channels.index(tag)]]
            fields=[tag,f'{observed[i]:.6g} {data.units[i]}',state+' / 未定位']
            for col,value in enumerate(fields):self.values_table.setItem(row,col,QTableWidgetItem(value))
        self.frame_label.setText(f'源时间 {data.times[seq]} · 数值来自本次回放 · 点击测点可查看曲线')
